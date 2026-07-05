"""Safe arithmetic formula parser and evaluator for custom commission rules.

The escape hatch of the plan DSL: when a plan can't be expressed with the
built-in rule vocabulary (flat_rate / tiered / accelerator), a `formula` rule
evaluates an arithmetic expression per credited transaction.

Follows the same design as filter_parser.py — a small hand-written tokenizer
and recursive-descent parser producing an AST that evaluates deterministically
with no side effects, no attribute access, and no Python eval(). All math is
Decimal; floats never enter the calculation.

Grammar (highest precedence last):

    expr        := or_expr
    or_expr     := and_expr ("or" and_expr)*
    and_expr    := comparison ("and" comparison)*
    comparison  := additive (("=="|"!="|">"|">="|"<"|"<=") additive)?
    additive    := multiplicative (("+"|"-") multiplicative)*
    multiplicative := unary (("*"|"/") unary)*
    unary       := "-" unary | primary
    primary     := NUMBER | STRING | IDENT | IDENT "(" args ")" | "(" expr ")"

Functions: min, max, abs, round, floor, ceil, if(cond, then, else).
Identifiers resolve against a caller-supplied context dict (the engine binds
amount, margin, quota, attainment_pct, bookings, product, and metadata fields).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any


class FormulaError(ValueError):
    """Raised when a formula fails to parse. Caught at plan-load time."""


class FormulaEvalError(ValueError):
    """Raised when a formula fails to evaluate for a specific transaction
    (missing variable, division by zero, type mismatch). The engine catches
    this per-row and records a rule_skipped ledger entry instead of aborting."""


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


class TokenType(enum.Enum):
    IDENT = enum.auto()
    STRING = enum.auto()
    NUMBER = enum.auto()
    EQ = enum.auto()
    NE = enum.auto()
    GT = enum.auto()
    GE = enum.auto()
    LT = enum.auto()
    LE = enum.auto()
    PLUS = enum.auto()
    MINUS = enum.auto()
    STAR = enum.auto()
    SLASH = enum.auto()
    LPAREN = enum.auto()
    RPAREN = enum.auto()
    COMMA = enum.auto()
    AND = enum.auto()
    OR = enum.auto()
    EOF = enum.auto()


_TWO_CHAR = {
    "==": TokenType.EQ,
    "!=": TokenType.NE,
    ">=": TokenType.GE,
    "<=": TokenType.LE,
}

_ONE_CHAR = {
    ">": TokenType.GT,
    "<": TokenType.LT,
    "+": TokenType.PLUS,
    "-": TokenType.MINUS,
    "*": TokenType.STAR,
    "/": TokenType.SLASH,
    "(": TokenType.LPAREN,
    ")": TokenType.RPAREN,
    ",": TokenType.COMMA,
}

_KEYWORDS = {"and": TokenType.AND, "or": TokenType.OR}


@dataclass
class _Token:
    type: TokenType
    value: str
    pos: int


def _tokenize(source: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    while i < len(source):
        c = source[i]
        if c in (" ", "\t", "\n"):
            i += 1
            continue
        # Backtick-quoted field names: `Deal Type`
        if c == "`":
            j = i + 1
            while j < len(source) and source[j] != "`":
                j += 1
            if j >= len(source):
                raise FormulaError(f"Unclosed backtick at position {i}")
            tokens.append(_Token(TokenType.IDENT, source[i + 1 : j], i))
            i = j + 1
            continue
        if source[i : i + 2] in _TWO_CHAR:
            tokens.append(_Token(_TWO_CHAR[source[i : i + 2]], source[i : i + 2], i))
            i += 2
            continue
        if c in _ONE_CHAR:
            tokens.append(_Token(_ONE_CHAR[c], c, i))
            i += 1
            continue
        if c == "'" or c == '"':
            quote = c
            j = i + 1
            while j < len(source) and source[j] != quote:
                j += 1
            if j >= len(source):
                raise FormulaError(f"Unclosed quote at position {i}")
            tokens.append(_Token(TokenType.STRING, source[i + 1 : j], i))
            i = j + 1
            continue
        if c.isdigit() or (c == "." and i + 1 < len(source) and source[i + 1].isdigit()):
            j = i
            while j < len(source) and (source[j].isdigit() or source[j] == "."):
                j += 1
            tokens.append(_Token(TokenType.NUMBER, source[i:j], i))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < len(source) and (source[j].isalnum() or source[j] == "_"):
                j += 1
            word = source[i:j]
            ttype = _KEYWORDS.get(word.lower(), TokenType.IDENT)
            tokens.append(_Token(ttype, word, i))
            i = j
            continue
        raise FormulaError(f"Unexpected character '{c}' at position {i}")
    tokens.append(_Token(TokenType.EOF, "", len(source)))
    return tokens


# ---------------------------------------------------------------------------
# Runtime value handling
# ---------------------------------------------------------------------------
#
# Values at runtime are Decimal, bool, or str. Arithmetic coerces
# numeric-looking strings (metadata columns arrive as strings) to Decimal;
# comparisons try Decimal on both sides first and fall back to string
# equality, mirroring the filter DSL's coercion rules.


def _as_decimal(v: object, what: str) -> Decimal:
    if isinstance(v, bool):
        raise FormulaEvalError(f"{what} must be a number, got a condition")
    if isinstance(v, Decimal):
        return v
    if isinstance(v, str):
        try:
            return Decimal(v)
        except InvalidOperation:
            raise FormulaEvalError(f"{what} must be a number, got {v!r}") from None
    raise FormulaEvalError(f"{what} must be a number, got {type(v).__name__}")


def _try_decimal(v: object) -> Decimal | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _as_bool(v: object, what: str) -> bool:
    if isinstance(v, bool):
        return v
    raise FormulaEvalError(
        f"{what} must be a condition (a comparison or and/or of comparisons), "
        f"got {v!r}"
    )


# ---------------------------------------------------------------------------
# AST nodes
# ---------------------------------------------------------------------------


@dataclass
class _Number:
    value: Decimal

    def eval(self, ctx: dict[str, Any]) -> object:
        return self.value


@dataclass
class _String:
    value: str

    def eval(self, ctx: dict[str, Any]) -> object:
        return self.value


@dataclass
class _Var:
    name: str

    def eval(self, ctx: dict[str, Any]) -> object:
        if self.name not in ctx:
            raise FormulaEvalError(
                f"Unknown variable {self.name!r} (available: "
                f"{', '.join(sorted(ctx.keys()))})"
            )
        v = ctx[self.name]
        if v is None:
            raise FormulaEvalError(f"Variable {self.name!r} has no value for this transaction")
        return v


@dataclass
class _Neg:
    operand: Any

    def eval(self, ctx: dict[str, Any]) -> object:
        return -_as_decimal(self.operand.eval(ctx), "operand of unary '-'")


@dataclass
class _BinOp:
    op: str
    left: Any
    right: Any

    def eval(self, ctx: dict[str, Any]) -> object:
        lv = _as_decimal(self.left.eval(ctx), f"left side of '{self.op}'")
        rv = _as_decimal(self.right.eval(ctx), f"right side of '{self.op}'")
        if self.op == "+":
            return lv + rv
        if self.op == "-":
            return lv - rv
        if self.op == "*":
            return lv * rv
        # "/"
        if rv == 0:
            raise FormulaEvalError("Division by zero")
        return lv / rv


@dataclass
class _Comparison:
    op: str
    left: Any
    right: Any

    def eval(self, ctx: dict[str, Any]) -> object:
        lv = self.left.eval(ctx)
        rv = self.right.eval(ctx)
        ld, rd = _try_decimal(lv), _try_decimal(rv)
        if ld is not None and rd is not None:
            lv, rv = ld, rd
        else:
            lv, rv = str(lv), str(rv)
        if self.op == "==":
            return lv == rv
        if self.op == "!=":
            return lv != rv
        if self.op == ">":
            return lv > rv
        if self.op == ">=":
            return lv >= rv
        if self.op == "<":
            return lv < rv
        return lv <= rv  # "<="


@dataclass
class _And:
    left: Any
    right: Any

    def eval(self, ctx: dict[str, Any]) -> object:
        return _as_bool(self.left.eval(ctx), "left side of 'and'") and _as_bool(
            self.right.eval(ctx), "right side of 'and'"
        )


@dataclass
class _Or:
    left: Any
    right: Any

    def eval(self, ctx: dict[str, Any]) -> object:
        return _as_bool(self.left.eval(ctx), "left side of 'or'") or _as_bool(
            self.right.eval(ctx), "right side of 'or'"
        )


@dataclass
class _Call:
    name: str
    args: list[Any]

    def eval(self, ctx: dict[str, Any]) -> object:
        name = self.name
        if name == "if":
            cond = _as_bool(self.args[0].eval(ctx), "first argument of if()")
            # Only the taken branch is evaluated, so if(quota > 0, x / quota, 0)
            # never divides by zero.
            return self.args[1].eval(ctx) if cond else self.args[2].eval(ctx)
        vals = [
            _as_decimal(a.eval(ctx), f"argument {i + 1} of {name}()")
            for i, a in enumerate(self.args)
        ]
        if name == "min":
            return min(vals)
        if name == "max":
            return max(vals)
        if name == "abs":
            return abs(vals[0])
        if name == "round":
            places = int(vals[1]) if len(vals) == 2 else 0
            q = Decimal(1).scaleb(-places)
            return vals[0].quantize(q, rounding=ROUND_HALF_UP)
        if name == "floor":
            return vals[0].to_integral_value(rounding=ROUND_FLOOR)
        # "ceil"
        return vals[0].to_integral_value(rounding=ROUND_CEILING)


_FUNCTIONS: dict[str, tuple[int, int]] = {
    # name -> (min_args, max_args)
    "min": (2, 99),
    "max": (2, 99),
    "abs": (1, 1),
    "round": (1, 2),
    "floor": (1, 1),
    "ceil": (1, 1),
    "if": (3, 3),
}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


_COMPARISON_OPS = {
    TokenType.EQ: "==",
    TokenType.NE: "!=",
    TokenType.GT: ">",
    TokenType.GE: ">=",
    TokenType.LT: "<",
    TokenType.LE: "<=",
}


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    @property
    def _cur(self) -> _Token:
        return self.tokens[self.pos]

    def _expect(self, ttype: TokenType) -> _Token:
        tok = self._cur
        if tok.type != ttype:
            raise FormulaError(
                f"Expected {ttype.name} but got {tok.type.name} at position {tok.pos}"
            )
        self.pos += 1
        return tok

    def parse(self) -> Any:
        node = self._or_expr()
        self._expect(TokenType.EOF)
        return node

    def _or_expr(self) -> Any:
        left = self._and_expr()
        while self._cur.type == TokenType.OR:
            self.pos += 1
            left = _Or(left, self._and_expr())
        return left

    def _and_expr(self) -> Any:
        left = self._comparison()
        while self._cur.type == TokenType.AND:
            self.pos += 1
            left = _And(left, self._comparison())
        return left

    def _comparison(self) -> Any:
        left = self._additive()
        if self._cur.type in _COMPARISON_OPS:
            op = _COMPARISON_OPS[self._cur.type]
            self.pos += 1
            return _Comparison(op, left, self._additive())
        return left

    def _additive(self) -> Any:
        left = self._multiplicative()
        while self._cur.type in (TokenType.PLUS, TokenType.MINUS):
            op = self._cur.value
            self.pos += 1
            left = _BinOp(op, left, self._multiplicative())
        return left

    def _multiplicative(self) -> Any:
        left = self._unary()
        while self._cur.type in (TokenType.STAR, TokenType.SLASH):
            op = self._cur.value
            self.pos += 1
            left = _BinOp(op, left, self._unary())
        return left

    def _unary(self) -> Any:
        if self._cur.type == TokenType.MINUS:
            self.pos += 1
            return _Neg(self._unary())
        return self._primary()

    def _primary(self) -> Any:
        tok = self._cur
        if tok.type == TokenType.NUMBER:
            self.pos += 1
            try:
                return _Number(Decimal(tok.value))
            except InvalidOperation:
                raise FormulaError(f"Invalid number {tok.value!r} at position {tok.pos}") from None
        if tok.type == TokenType.STRING:
            self.pos += 1
            return _String(tok.value)
        if tok.type == TokenType.LPAREN:
            self.pos += 1
            node = self._or_expr()
            self._expect(TokenType.RPAREN)
            return node
        if tok.type == TokenType.IDENT:
            self.pos += 1
            if self._cur.type == TokenType.LPAREN:
                return self._call(tok)
            return _Var(tok.value)
        raise FormulaError(
            f"Expected a number, variable, or function at position {tok.pos}, "
            f"got {tok.type.name}"
        )

    def _call(self, name_tok: _Token) -> _Call:
        name = name_tok.value.lower()
        if name not in _FUNCTIONS:
            raise FormulaError(
                f"Unknown function {name_tok.value!r} at position {name_tok.pos} "
                f"(available: {', '.join(sorted(_FUNCTIONS))})"
            )
        self._expect(TokenType.LPAREN)
        args: list[Any] = []
        if self._cur.type != TokenType.RPAREN:
            args.append(self._or_expr())
            while self._cur.type == TokenType.COMMA:
                self.pos += 1
                args.append(self._or_expr())
        self._expect(TokenType.RPAREN)
        lo, hi = _FUNCTIONS[name]
        if not (lo <= len(args) <= hi):
            expected = str(lo) if lo == hi else f"{lo}-{hi}"
            raise FormulaError(
                f"Function {name}() takes {expected} argument(s), got {len(args)}"
            )
        return _Call(name, args)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class CompiledFormula:
    """A parsed formula ready to evaluate against per-transaction contexts."""

    source: str
    _ast: Any
    variables: frozenset[str]

    def evaluate(self, context: dict[str, Any]) -> Decimal:
        """Evaluate the formula. The result must be a number.

        Raises FormulaEvalError on missing variables, division by zero, or
        a non-numeric result (e.g. a bare comparison with no if() around it).
        """
        result = self._ast.eval(context)
        if isinstance(result, bool):
            raise FormulaEvalError(
                "Formula produced a condition, not an amount — wrap it in "
                "if(condition, amount_if_true, amount_if_false)"
            )
        return _as_decimal(result, "formula result")


def compile_formula(source: str) -> CompiledFormula:
    """Parse a formula string. Raises FormulaError (a ValueError) on bad syntax.

    Called from the FormulaRule model validator, so a plan with a broken
    formula fails at load time with a clear message, never mid-run.
    """
    if not source or not source.strip():
        raise FormulaError("Formula is empty")
    try:
        ast = _Parser(_tokenize(source)).parse()
    except FormulaError as e:
        raise FormulaError(f"Invalid formula {source!r}: {e}") from e
    fields: set[str] = set()
    _walk_variables(ast, fields)
    return CompiledFormula(source=source, _ast=ast, variables=frozenset(fields))


# Variables the engine always binds, regardless of transaction data.
_BUILTIN_VARIABLES = frozenset({
    "amount", "margin", "product", "quota", "attainment_pct", "bookings",
})


def check_formula_fields(
    formula_source: str,
    transactions: list[Any],
) -> list[str]:
    """Return formula variables that are neither built-in nor present in any
    transaction's metadata — the same typo guard filters get. A formula
    referencing a nonexistent variable skips every row at run time, so the
    caller warns up front instead.
    """
    if not formula_source or not transactions:
        return []
    try:
        compiled = compile_formula(formula_source)
    except FormulaError:
        return []  # unparseable formulas fail loudly elsewhere
    unused: list[str] = []
    for f in sorted(compiled.variables):
        if f in _BUILTIN_VARIABLES:
            continue
        found = any(
            isinstance(getattr(t, "metadata", None), dict) and f in (t.metadata or {})
            for t in transactions
        )
        if not found:
            unused.append(f)
    return unused


def _walk_variables(node: object, fields: set[str]) -> None:
    if isinstance(node, _Var):
        fields.add(node.name)
    elif isinstance(node, _Neg):
        _walk_variables(node.operand, fields)
    elif isinstance(node, (_BinOp, _Comparison, _And, _Or)):
        _walk_variables(node.left, fields)
        _walk_variables(node.right, fields)
    elif isinstance(node, _Call):
        for a in node.args:
            _walk_variables(a, fields)

"""Safe filter expression parser and evaluator for transaction rules.

Parses a small DSL for equality, comparison, and set-membership checks
against canonical transaction fields and metadata. The evaluator is
deterministic and side-effect-free — no computation, no I/O.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from icm_engine.models import Transaction

# ---------------------------------------------------------------------------
# Canonical fields
# ---------------------------------------------------------------------------

_CANONICAL_FIELDS = frozenset({
    "id", "payee_id", "deal_id", "period", "amount", "product", "close_date",
    "bill_rate", "pay_rate", "units", "margin",
})


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
    IN = enum.auto()
    LPAREN = enum.auto()
    RPAREN = enum.auto()
    LBRACKET = enum.auto()
    RBRACKET = enum.auto()
    COMMA = enum.auto()
    AND = enum.auto()
    OR = enum.auto()
    EOF = enum.auto()


TOKEN_MAP: dict[str, TokenType] = {
    "==": TokenType.EQ,
    "!=": TokenType.NE,
    ">=": TokenType.GE,
    "<=": TokenType.LE,
    ">": TokenType.GT,
    "<": TokenType.LT,
    "in": TokenType.IN,
    "and": TokenType.AND,
    "or": TokenType.OR,
    "(": TokenType.LPAREN,
    ")": TokenType.RPAREN,
    "[": TokenType.LBRACKET,
    "]": TokenType.RBRACKET,
    ",": TokenType.COMMA,
}


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
                raise ValueError(f"Unclosed backtick at position {i}")
            tokens.append(_Token(TokenType.IDENT, source[i + 1 : j], i))
            i = j + 1
            continue
        if source[i : i + 2] in ("==", "!=", ">=", "<="):
            tokens.append(_Token(TOKEN_MAP[source[i : i + 2]], source[i : i + 2], i))
            i += 2
            continue
        if c in "><()[]=,":
            tokens.append(_Token(TOKEN_MAP[c], c, i))
            i += 1
            continue
        if c == "'" or c == '"':
            quote = c
            j = i + 1
            while j < len(source) and source[j] != quote:
                j += 1
            if j >= len(source):
                raise ValueError(f"Unclosed quote at position {i}")
            tokens.append(_Token(TokenType.STRING, source[i + 1 : j], i))
            i = j + 1
            continue
        if c.isdigit():
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
            word = source[i:j].lower()
            ttype = TOKEN_MAP.get(word, TokenType.IDENT)
            tokens.append(_Token(ttype, source[i:j], i))
            i = j
            continue
        raise ValueError(f"Unexpected character '{c}' at position {i}")
    tokens.append(_Token(TokenType.EOF, "", len(source)))
    return tokens


# ---------------------------------------------------------------------------
# Field resolution and type coercion
# ---------------------------------------------------------------------------


def _resolve_field(txn: Transaction, field: str) -> object:
    """Resolve a filter field: canonical attribute first, then metadata, else None."""
    if field == "margin":
        # Filter on the effective gross profit (explicit override or computed).
        return txn.margin_value
    if field in _CANONICAL_FIELDS:
        return getattr(txn, field, None)
    meta: dict[str, Any] = getattr(txn, "metadata", None) or {}
    if field in meta:
        return meta[field]
    return None


def _try_decimal(v: object) -> Decimal | None:
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _try_date(v: object) -> date | None:
    if isinstance(v, date):
        return v
    try:
        from datetime import datetime as _dt
        s = str(v).strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
            try:
                return _dt.strptime(s, fmt).date()
            except ValueError:
                continue
    except Exception:
        pass
    return None


def _values_match(a: object, b: object) -> bool:
    """Test equality with progressive type coercion."""
    da = _try_decimal(a)
    db = _try_decimal(b)
    if da is not None and db is not None:
        return da == db
    if _try_date(a) is not None and _try_date(b) is not None:
        return _try_date(a) == _try_date(b)
    return str(a) == str(b)


# ---------------------------------------------------------------------------
# AST nodes
# ---------------------------------------------------------------------------


class _AST(Protocol):
    def eval(self, txn: Transaction) -> bool: ...


@dataclass
class _Comparison:
    field: str
    op: str
    value: object

    def eval(self, txn: Transaction) -> bool:
        field_val = _resolve_field(txn, self.field)
        if field_val is None:
            # A missing field is "not equal" to any concrete value, but cannot
            # satisfy ==, ordering, or membership checks.
            return self.op == "!="

        rhs = self.value

        fd = _try_decimal(field_val)
        rd = _try_decimal(rhs)
        if fd is not None and rd is not None:
            if self.op == "==":
                return fd == rd
            if self.op == "!=":
                return fd != rd
            if self.op == ">":
                return fd > rd
            if self.op == "<":
                return fd < rd
            if self.op == ">=":
                return fd >= rd
            if self.op == "<=":
                return fd <= rd
            return False

        f_date = _try_date(field_val)
        r_date = _try_date(rhs)
        if f_date is not None and r_date is not None:
            if self.op == "==":
                return f_date == r_date
            if self.op == "!=":
                return f_date != r_date
            if self.op in (">", "<", ">=", "<="):
                if self.op == ">":
                    return f_date > r_date
                if self.op == "<":
                    return f_date < r_date
                if self.op == ">=":
                    return f_date >= r_date
                if self.op == "<=":
                    return f_date <= r_date
            return False

        sa, sb = str(field_val), str(rhs)
        if self.op == "==":
            return sa == sb
        if self.op == "!=":
            return sa != sb
        if self.op == ">":
            return sa > sb
        if self.op == "<":
            return sa < sb
        if self.op == ">=":
            return sa >= sb
        if self.op == "<=":
            return sa <= sb
        return False


@dataclass
class _InExpr:
    field: str
    values: list[object]

    def eval(self, txn: Transaction) -> bool:
        field_val = _resolve_field(txn, self.field)
        if field_val is None:
            return False
        for v in self.values:
            if _values_match(field_val, v):
                return True
        return False


@dataclass
class _And:
    left: _AST
    right: _AST

    def eval(self, txn: Transaction) -> bool:
        return self.left.eval(txn) and self.right.eval(txn)


@dataclass
class _Or:
    left: _AST
    right: _AST

    def eval(self, txn: Transaction) -> bool:
        return self.left.eval(txn) or self.right.eval(txn)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


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
            raise ValueError(f"Expected {ttype.name} but got {tok.type.name} at {tok.pos}")
        self.pos += 1
        return tok

    def parse(self) -> _AST:
        node = self._or_expr()
        self._expect(TokenType.EOF)
        return node

    def _or_expr(self) -> _AST:
        left: _AST = self._and_expr()
        while self._cur.type == TokenType.OR:
            self.pos += 1
            left = _Or(left, self._and_expr())
        return left

    def _and_expr(self) -> _AST:
        left: _AST = self._atom()
        while self._cur.type == TokenType.AND:
            self.pos += 1
            left = _And(left, self._atom())
        return left

    def _atom(self) -> _AST:
        if self._cur.type == TokenType.LPAREN:
            self.pos += 1
            node = self._or_expr()
            self._expect(TokenType.RPAREN)
            return node
        return self._comparison()

    def _comparison(self) -> _AST:
        field = self._expect(TokenType.IDENT).value
        tok = self._cur
        if tok.type == TokenType.IN:
            self.pos += 1
            return self._in_list(field)
        if tok.type in (
            TokenType.EQ, TokenType.NE, TokenType.GT,
            TokenType.GE, TokenType.LT, TokenType.LE,
        ):
            self.pos += 1
            val_tok = self._cur
            if val_tok.type == TokenType.NUMBER:
                self.pos += 1
                return _Comparison(field, tok.value, Decimal(val_tok.value))
            val = self._expect(TokenType.STRING).value
            return _Comparison(field, tok.value, val)
        raise ValueError(f"Expected operator at {tok.pos}, got {tok.type.name}")

    def _in_list(self, field: str) -> _InExpr:
        self._expect(TokenType.LBRACKET)
        values: list[object] = []
        while True:
            val_tok = self._cur
            if val_tok.type == TokenType.STRING:
                values.append(val_tok.value)
            elif val_tok.type == TokenType.NUMBER:
                values.append(Decimal(val_tok.value))
            else:
                raise ValueError(f"Expected value in 'in' list at {val_tok.pos}")
            self.pos += 1
            if self._cur.type == TokenType.COMMA:
                self.pos += 1
                continue
            break
        self._expect(TokenType.RBRACKET)
        return _InExpr(field, values)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compile_filter(source: str | None) -> Callable[[Transaction], bool]:
    """Compile a filter expression string into a callable predicate.

    Returns a predicate that always returns True if source is None or blank.
    """
    if source is None or source.strip() == "":
        return lambda txn: True
    try:
        tokens = _tokenize(source)
        ast: _AST = _Parser(tokens).parse()
    except (ValueError, ArithmeticError) as e:
        # Surface a clear, attributable error instead of a cryptic mid-run crash.
        raise ValueError(f"Invalid rule filter {source!r}: {e}") from e
    return ast.eval


def check_filter_fields(
    filter_source: str | None,
    transactions: list[Transaction],
) -> list[str]:
    """Return filter fields that appear in no transaction (canonical or metadata).

    Used as a typo guard: if a filter references a field that exists nowhere,
    it will silently match nothing. This returns the list of such fields so
    the caller can warn the user.
    """
    if not filter_source or not transactions:
        return []
    try:
        tokens = _tokenize(filter_source)
        ast = _Parser(tokens).parse()
    except ValueError:
        return []
    refs = _collect_filter_fields(ast)
    unused: list[str] = []
    for f in sorted(refs):
        if f in _CANONICAL_FIELDS:
            continue
        found = any(
            isinstance(getattr(t, "metadata", None), dict) and f in (t.metadata or {})
            for t in transactions
        )
        if not found:
            unused.append(f)
    return unused


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _collect_filter_fields(ast: _AST) -> set[str]:
    """Walk an AST and return the set of field names referenced in filters."""
    fields: set[str] = set()
    _walk_fields(ast, fields)
    return fields


def _walk_fields(node: object, fields: set[str]) -> None:
    if isinstance(node, _Comparison):
        fields.add(node.field)
    elif isinstance(node, _InExpr):
        fields.add(node.field)
    elif isinstance(node, (_And, _Or)):
        _walk_fields(node.left, fields)
        _walk_fields(node.right, fields)

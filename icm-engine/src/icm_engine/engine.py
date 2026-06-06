from __future__ import annotations

import enum
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from icm_engine.ledger import LedgerEntry
from icm_engine.models import (
    AcceleratorRule,
    Commission,
    Credit,
    FlatRateRule,
    Payee,
    Plan,
    Tier,
    TieredRule,
    Transaction,
)

# ---------------------------------------------------------------------------
# Filter expression parser & evaluator
# ---------------------------------------------------------------------------

_CANONICAL_FIELDS = frozenset({
    "id", "payee_id", "deal_id", "period", "amount", "product", "close_date",
})


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


# ------------------------------------------------------------------
# Field resolution
# ------------------------------------------------------------------


def _resolve_field(txn: Transaction, field: str) -> object:
    """Resolve a filter field: canonical attribute first, then metadata, else None."""
    if field in _CANONICAL_FIELDS:
        return getattr(txn, field, None)
    meta: dict[str, Any] = getattr(txn, "metadata", None) or {}
    if field in meta:
        return meta[field]
    return None


# ------------------------------------------------------------------
# Type coercion
# ------------------------------------------------------------------


def _try_decimal(v: object) -> Decimal | None:
    """Attempt to parse a value as Decimal. Returns None on failure."""
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _try_date(v: object) -> date | None:
    """Attempt to parse a value as a date. Returns None on failure."""
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
    """Test equality with progressive type coercion.

    Tries: Decimal equality → date equality → string equality.
    """
    da = _try_decimal(a)
    db = _try_decimal(b)
    if da is not None and db is not None:
        return da == db
    if _try_date(a) is not None and _try_date(b) is not None:
        return _try_date(a) == _try_date(b)
    return str(a) == str(b)


# ------------------------------------------------------------------
# AST nodes
# ------------------------------------------------------------------


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
            return False  # missing field → never match

        rhs = self.value

        # Try numeric comparison first
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

        # Try date comparison
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

        # Fallback: string comparison (only for == / !=)
        if self.op == "==":
            return str(field_val) == str(rhs)
        if self.op == "!=":
            return str(field_val) != str(rhs)
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


# ------------------------------------------------------------------
# Tokenizer
# ------------------------------------------------------------------


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


# ------------------------------------------------------------------
# Parser
# ------------------------------------------------------------------


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


# ------------------------------------------------------------------
# Compile
# ------------------------------------------------------------------


def compile_filter(source: str | None) -> Callable[[Transaction], bool]:
    """Compile a filter expression string into a callable predicate.

    Returns a predicate that always returns True if source is None or blank.
    """
    if source is None or source.strip() == "":
        return lambda txn: True
    tokens = _tokenize(source)
    ast: _AST = _Parser(tokens).parse()
    return ast.eval


# ------------------------------------------------------------------
# Field usage check (typo guard)
# ------------------------------------------------------------------


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


def check_filter_fields(
    filter_source: str | None,
    transactions: list[Transaction],
) -> list[str]:
    """Return filter fields that appear in no transaction (canonical or metadata).

    Used as a typo guard: if a filter references a field that exists nowhere,
    it will silently match nothing. This returns the list of such fields so
    the caller can warn the user.

    Args:
        filter_source: The raw filter expression string.
        transactions: The loaded transactions to check against.

    Returns:
        List of field names that are neither canonical nor present in any
        transaction's metadata.
    """
    if not filter_source or not transactions:
        return []
    try:
        tokens = _tokenize(filter_source)
        ast = _Parser(tokens).parse()
    except ValueError:
        return []  # don't interfere with parse errors
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
# Engine
# ---------------------------------------------------------------------------


_MAX_DATE = date(9999, 12, 31)


def _sort_key(txn: Transaction) -> tuple[int, date]:
    """Sort key: undated transactions sort after all dated ones, stable."""
    if txn.close_date is None:
        return (1, _MAX_DATE)
    return (0, txn.close_date)


def _window_key(period: str, period_type: str) -> str:
    """Map a YYYY-MM period to a grouping window key.

    monthly:   "2026-02" → "2026-02"  (unchanged)
    quarterly: months 01-03 → "YYYY-Q1", 04-06 → "Q2", 07-09 → "Q3", 10-12 → "Q4"
    annual:    "2026-03" → "2026" (entire year as one window)

    This is the SEAM for future windowing modes (cumulative, YTD, custom).
    Attainment resets each window (v1 design). Cumulative/year-to-date is a
    possible future mode — route any such change through this function.
    """
    if period_type == "monthly":
        return period
    if period_type == "quarterly":
        year, month = period.split("-")
        m = int(month)
        q = (m - 1) // 3 + 1
        return f"{year}-Q{q}"
    if period_type == "annual":
        return period.split("-")[0]
    return period  # unknown period_type — pass through unchanged


@dataclass
class _CreditUnit:
    """A resolved credit: one payee's share of a deal, ready for rule evaluation."""
    transaction_id: str
    payee_id: str
    credited_amount: Decimal
    split_pct: Decimal
    kind: str
    period: str
    product: str | None
    close_date: date | None
    metadata: dict[str, Any]


def _resolve_credits(transactions: list[Transaction]) -> list[_CreditUnit]:
    """Expand transactions with credits into credit units."""
    units: list[_CreditUnit] = []
    for txn in transactions:
        credits = txn.credits
        if not credits:
            credits = [Credit(payee_id=txn.payee_id, split_pct=Decimal("1"), kind="split")]
        for c in credits:
            units.append(_CreditUnit(
                transaction_id=txn.id,
                payee_id=c.payee_id,
                credited_amount=txn.amount * c.split_pct,
                split_pct=c.split_pct,
                kind=c.kind,
                period=txn.period,
                product=txn.product,
                close_date=txn.close_date,
                metadata=txn.metadata,
            ))
    return units


def _compute_attainment(
    credits: list[_CreditUnit],
    payee_map: dict[str, Payee],
    period_type: str,
) -> list[AttainmentSummary]:
    """Compute bookings vs quota per (payee, window).

    v1: ALL credited bookings count toward attainment. Quota-category filtering
    (e.g. only new-business deals) is a future extension.
    """
    bookings: dict[tuple[str, str], Decimal] = {}
    for cu in credits:
        window = _window_key(cu.period, period_type)
        key = (cu.payee_id, window)
        bookings[key] = bookings.get(key, Decimal("0")) + cu.credited_amount

    summaries: list[AttainmentSummary] = []
    for (pid, window), booked in sorted(bookings.items()):
        p = payee_map.get(pid)
        quota = p.quota_for(window) if p else Decimal("0")
        pct = booked / quota if quota != 0 else None
        summaries.append(AttainmentSummary(
            payee_id=pid,
            period=window,
            bookings=booked,
            quota=quota,
            attainment_pct=pct,
        ))
    return summaries


def _make_synthetic_transactions(credits: list[_CreditUnit]) -> list[Transaction]:
    """Convert credit units into synthetic Transactions for rule evaluation."""
    return [
        Transaction(
            id=cu.transaction_id,
            payee_id=cu.payee_id,
            amount=cu.credited_amount,
            period=cu.period,
            product=cu.product,
            close_date=cu.close_date,
            metadata={**cu.metadata, "_credit_split_pct": str(cu.split_pct), "_credit_kind": cu.kind},
        )
        for cu in credits
    ]


def _stamp_credits(commissions: list[Commission], credits: list[_CreditUnit]) -> None:
    """Patch split_pct and kind from credit units onto Commission objects."""
    # Build lookup: (transaction_id, payee_id) -> credit unit
    lookup: dict[tuple[str, str], _CreditUnit] = {}
    for cu in credits:
        lookup[(cu.transaction_id, cu.payee_id)] = cu
    for c in commissions:
        key = (c.transaction_id, c.payee_id)
        if key in lookup:
            cu = lookup[key]
            c.split_pct = cu.split_pct
            c.kind = cu.kind


@dataclass
class AttainmentSummary:
    payee_id: str
    period: str
    bookings: Decimal
    quota: Decimal
    attainment_pct: Decimal | None  # None if quota == 0


@dataclass
class CalculationResult:
    commissions: list[Commission] = field(default_factory=list)
    ledger: list[LedgerEntry] = field(default_factory=list)
    attainment: list[AttainmentSummary] = field(default_factory=list)
    draw_balances: dict[str, Decimal] = field(default_factory=dict)


@dataclass
class Adjustment:
    """Per (payee, period) delta between prior and current commission totals."""
    payee_id: str
    period: str
    prior_amount: Decimal
    new_amount: Decimal
    delta: Decimal  # new - prior (negative = clawback)


@dataclass
class TrueUpResult:
    current: CalculationResult
    adjustments: list[Adjustment]
    exceptions: list[dict[str, Any]]
    ledger: list[LedgerEntry]


@dataclass
class CommissionDelta:
    """A per-key delta between prior and current commissions."""
    transaction_id: str
    payee_id: str
    rule_id: str
    origin_period: str
    prior_amount: Decimal
    new_amount: Decimal
    delta: Decimal  # new - prior
    is_new: bool    # no prior line
    is_removed: bool  # no current line
    # Fields for constructing a Commission object
    base_amount: Decimal
    rate: Decimal
    kind: str
    split_pct: Decimal


def _diff_commissions(
    prior: list[Commission],
    current: list[Commission],
) -> list[CommissionDelta]:
    """Diff prior vs current commissions, aggregated by (transaction_id, rule_id, payee_id).

    Returns one CommissionDelta per non-zero delta. Aggregates by key because
    tiered rules can produce multiple Commission objects per (transaction, rule, payee).
    """
    def _key(c: Commission) -> tuple[str, str, str]:
        return (c.transaction_id, c.rule_id, c.payee_id)

    # Aggregate totals by key
    prior_totals: dict[tuple[str, str, str], Decimal] = {}
    prior_refs: dict[tuple[str, str, str], Commission] = {}
    for c in prior:
        k = _key(c)
        prior_totals[k] = prior_totals.get(k, Decimal("0")) + c.commission_amount
        prior_refs[k] = c

    current_totals: dict[tuple[str, str, str], Decimal] = {}
    current_refs: dict[tuple[str, str, str], Commission] = {}
    for c in current:
        k = _key(c)
        current_totals[k] = current_totals.get(k, Decimal("0")) + c.commission_amount
        current_refs[k] = c

    all_keys = set(prior_totals.keys()) | set(current_totals.keys())

    deltas: list[CommissionDelta] = []
    for key in sorted(all_keys):
        tid, rid, pid = key
        prior_amt = prior_totals.get(key, Decimal("0"))
        new_amt = current_totals.get(key, Decimal("0"))
        delta = new_amt - prior_amt

        if delta == 0:
            continue

        # Use current as reference, fall back to prior for removed deals
        ref = current_refs.get(key) or prior_refs.get(key)
        if ref is None:
            continue

        deltas.append(CommissionDelta(
            transaction_id=tid,
            payee_id=pid,
            rule_id=rid,
            origin_period=ref.period,
            prior_amount=prior_amt,
            new_amount=new_amt,
            delta=delta,
            is_new=prior_amt == 0,
            is_removed=new_amt == 0,
            base_amount=delta,
            rate=Decimal("1"),
            kind=ref.kind,
            split_pct=ref.split_pct,
        ))

    return deltas


# ------------------------------------------------------------------
# Caps
# ------------------------------------------------------------------


def _apply_cap(
    commissions: list[Commission], cap_amount: Decimal, rule_id: str,
) -> list[Commission]:
    """Apply a per-rule cap. Sums payee-period totals and emits negative
    cap_adjustment lines for any excess.
    """
    by_pp: dict[tuple[str, str], list[Commission]] = {}
    for c in commissions:
        key = (c.payee_id, c.period)
        by_pp.setdefault(key, []).append(c)

    result: list[Commission] = []
    for (pid, period), lines in by_pp.items():
        total = sum(line.commission_amount for line in lines)
        if total > cap_amount:
            excess = total - cap_amount
            result.extend(lines)
            result.append(Commission(
                transaction_id="*",
                payee_id=pid,
                period=period,
                rule_id=rule_id,
                base_amount=-excess,
                rate=Decimal("1"),
                commission_amount=-excess,
                notes=f"cap_adjustment: {total} capped to {cap_amount}",
            ))
        else:
            result.extend(lines)
    return result


def _apply_plan_payout_cap(
    all_commissions: list[Commission],
    payout_cap: Decimal,
    all_ledger: list[LedgerEntry],
) -> list[Commission]:
    """Apply plan-level payout cap per (payee, period), emitting cap_adjustment lines."""
    by_pp: dict[tuple[str, str], list[Commission]] = {}
    for c in all_commissions:
        key = (c.payee_id, c.period)
        by_pp.setdefault(key, []).append(c)

    result: list[Commission] = []
    for (pid, period), lines in by_pp.items():
        total = sum(line.commission_amount for line in lines)
        if payout_cap is not None and total > payout_cap:
            excess = total - payout_cap
            result.extend(lines)
            cap_line = Commission(
                transaction_id="*",
                payee_id=pid,
                period=period,
                rule_id="payout_cap",
                base_amount=-excess,
                rate=Decimal("1"),
                commission_amount=-excess,
                notes=f"plan_cap_adjustment: {total} capped to {payout_cap}",
            )
            result.append(cap_line)
            all_ledger.append(LedgerEntry(
                transaction_id="*",
                payee_id=pid,
                rule_id="payout_cap",
                event_type="cap_applied",
                inputs={"total": str(total), "cap": str(payout_cap)},
                outputs={"cap_adjustment": str(-excess)},
                human_readable=f"Cap applied for {pid} {period}: {total} → {payout_cap}",
            ))
        else:
            result.extend(lines)
    return result


class CommissionEngine:
    def calculate(
        self,
        plan: Plan,
        transactions: list[Transaction],
        payees: list[Payee],
        locked_periods: set[str] | None = None,
        effective_period: str | None = None,
        prior_commissions: list[Commission] | None = None,
        adjustments: list[Any] | None = None,
        prior_draw_balances: dict[str, Decimal] | None = None,
        mbos: list[Any] | None = None,
    ) -> CalculationResult:
        all_commissions: list[Commission] = []
        all_ledger: list[LedgerEntry] = []
        payee_map = {p.id: p for p in payees}
        pt = plan.period_type

        # Resolve credits — expand multi-payee transactions into credit units
        credits = _resolve_credits(transactions)

        # Compute attainment: bookings per (payee, window) vs quota
        attainment = _compute_attainment(credits, payee_map, pt)
        for a in attainment:
            all_ledger.append(LedgerEntry(
                transaction_id="*",
                payee_id=a.payee_id,
                rule_id="*",
                event_type="attainment_computed",
                inputs={"bookings": str(a.bookings), "quota": str(a.quota)},
                outputs={"attainment_pct": str(a.attainment_pct) if a.attainment_pct is not None else "N/A"},
                human_readable=(
                    f"{a.payee_id} booked {a.bookings} against "
                    f"{a.quota} quota = {a.attainment_pct * 100:.1f}%"
                    if a.attainment_pct is not None
                    else f"{a.payee_id} booked {a.bookings} (quota=0)"
                ),
            ))

        # Emit credit_allocated ledger entries
        for cu in credits:
            all_ledger.append(LedgerEntry(
                transaction_id=cu.transaction_id,
                payee_id=cu.payee_id,
                rule_id="*",
                event_type="credit_allocated",
                inputs={
                    "split_pct": str(cu.split_pct),
                    "kind": cu.kind,
                    "credited_amount": str(cu.credited_amount),
                },
                human_readable=(
                    f"Credit {cu.kind}: {cu.payee_id} gets "
                    f"{cu.split_pct} of {cu.transaction_id} = {cu.credited_amount}"
                ),
            ))

        # Build attainment lookup for threshold gates
        att_by_payee_window: dict[tuple[str, str], Decimal | None] = {}
        for a in attainment:
            att_by_payee_window[(a.payee_id, a.period)] = a.attainment_pct

        for rule in plan.rules:
            # Build synthetic transactions for this rule's evaluation
            synth_txns = _make_synthetic_transactions(credits)

            # Threshold gate: filter out payees below min_attainment_pct
            min_att = getattr(rule, "min_attainment_pct", None)
            if min_att is not None and min_att > 0:
                gated_txns: list[Transaction] = []
                for t in synth_txns:
                    key = (t.payee_id, _window_key(t.period, pt))
                    att_pct = att_by_payee_window.get(key)
                    if att_pct is None or att_pct >= min_att:
                        gated_txns.append(t)
                    else:
                        all_ledger.append(LedgerEntry(
                            transaction_id=t.id,
                            payee_id=t.payee_id,
                            rule_id=rule.id,
                            event_type="rule_skipped",
                            inputs={"filter": rule.filter or "(none)",
                                    "reason": "below_threshold_gate",
                                    "attainment_pct": str(att_pct),
                                    "min_attainment_pct": str(min_att)},
                            human_readable=(
                                f"Transaction {t.id} skipped by rule {rule.id} "
                                f"(attainment {att_pct} below gate {min_att})"
                            ),
                        ))
                synth_txns = gated_txns

            if isinstance(rule, FlatRateRule):
                commissions, ledger = self._calc_flat_rate(
                    rule, synth_txns, payee_map, pt,
                    quota_category=getattr(rule, "quota_category", None),
                )
            elif isinstance(rule, TieredRule):
                commissions, ledger = self._calc_tiered(
                    rule, synth_txns, payee_map, pt,
                    quota_category=getattr(rule, "quota_category", None),
                )
            elif isinstance(rule, AcceleratorRule):
                commissions, ledger = self._calc_accelerator(
                    rule, synth_txns, payee_map, pt,
                    quota_category=getattr(rule, "quota_category", None),
                )
            else:
                continue

            # Per-rule cap
            rule_cap = getattr(rule, "cap", None)
            if rule_cap is not None and rule_cap >= 0:
                commissions = _apply_cap(commissions, rule_cap, rule.id)

            # Stamp credit metadata onto commissions
            _stamp_credits(commissions, credits)
            all_commissions.extend(commissions)
            all_ledger.extend(ledger)

        # --- MBOs / bonuses (after rules, before caps/draws) ---
        if mbos:
            for mbo in mbos:
                pid = getattr(mbo, "payee_id", "")
                period_val = getattr(mbo, "period", "")
                amt = Decimal(str(getattr(mbo, "amount", "0")))
                label = str(getattr(mbo, "label", ""))
                mbo_id = str(getattr(mbo, "id", ""))
                c = Commission(
                    transaction_id=mbo_id or f"mbo_{pid}_{period_val}",
                    payee_id=pid,
                    period=period_val,
                    rule_id="mbo",
                    base_amount=amt,
                    rate=Decimal("1"),
                    commission_amount=amt,
                    origin_period=period_val,
                    notes=label,
                )
                all_commissions.append(c)
                all_ledger.append(LedgerEntry(
                    transaction_id=c.transaction_id,
                    payee_id=pid,
                    rule_id="mbo",
                    event_type="mbo",
                    inputs={"amount": str(amt), "label": label},
                    outputs={"commission_amount": str(amt)},
                    human_readable=(
                        f"MBO {label} for {pid} {period_val}: {amt}"
                        if label else f"MBO for {pid} {period_val}: {amt}"
                    ),
                ))

        # Plan-level payout cap (per payee, per period)
        if plan.payout_cap is not None:
            all_commissions = _apply_plan_payout_cap(all_commissions, plan.payout_cap, all_ledger)

        # --- Draws / guarantees ---
        draw_balances: dict[str, Decimal] = {}
        for pid in {c.payee_id for c in all_commissions}:
            p = payee_map.get(pid)
            draw = None
            if p is not None:
                draw = getattr(p, "draw", None)
            if draw is None:
                draw = getattr(plan, "draw", None)
            if draw is None:
                continue

            draw_amt = getattr(draw, "amount", Decimal("0"))
            recoverable = bool(getattr(draw, "recoverable", False))
            prior_bal = (prior_draw_balances or {}).get(pid, Decimal("0"))

            # Sum this payee's post-cap commission per period
            by_period: dict[str, Decimal] = {}
            for c in all_commissions:
                if c.payee_id == pid:
                    by_period[c.period] = by_period.get(c.period, Decimal("0")) + c.commission_amount

            for period, earned in sorted(by_period.items()):
                if recoverable:
                    available = max(Decimal("0"), earned - draw_amt)
                    recovered = min(prior_bal, available)
                    payout = max(earned - recovered, draw_amt)
                    new_shortfall = max(Decimal("0"), draw_amt - earned)
                    new_balance = prior_bal - recovered + new_shortfall

                    if recovered > 0:
                        all_commissions.append(Commission(
                            transaction_id="*", payee_id=pid, period=period,
                            rule_id="draw", base_amount=-recovered, rate=Decimal("1"),
                            commission_amount=-recovered,
                            notes=f"draw_recovery: recovered {recovered} toward draw {draw_amt}",
                        ))
                    if earned < draw_amt:
                        topup = draw_amt - earned
                        all_commissions.append(Commission(
                            transaction_id="*", payee_id=pid, period=period,
                            rule_id="draw", base_amount=topup, rate=Decimal("1"),
                            commission_amount=topup,
                            notes=f"draw_topup: floor {draw_amt}, earned {earned}",
                        ))

                    all_ledger.append(LedgerEntry(
                        transaction_id="*", payee_id=pid, rule_id="draw",
                        event_type="draw",
                        inputs={
                            "earned": str(earned), "draw": str(draw_amt),
                            "recovered": str(recovered), "prior_balance": str(prior_bal),
                        },
                        outputs={"payout": str(payout), "new_balance": str(new_balance)},
                        human_readable=(
                            f"Draw for {pid} {period}: earned {earned}, draw {draw_amt}, "
                            f"recovered {recovered}, balance {prior_bal}→{new_balance}"
                        ),
                    ))
                    draw_balances[pid] = new_balance
                    prior_bal = new_balance  # carry forward for next period
                else:
                    # Non-recoverable: simple floor
                    if earned < draw_amt:
                        topup = draw_amt - earned
                        all_commissions.append(Commission(
                            transaction_id="*", payee_id=pid, period=period,
                            rule_id="draw", base_amount=topup, rate=Decimal("1"),
                            commission_amount=topup,
                            notes=f"draw_topup: guarantee {draw_amt}, earned {earned}",
                        ))
                        all_ledger.append(LedgerEntry(
                            transaction_id="*", payee_id=pid, rule_id="draw",
                            event_type="draw",
                            inputs={"earned": str(earned), "draw": str(draw_amt)},
                            outputs={"topup": str(topup)},
                            human_readable=(
                                f"Draw (non-recoverable) for {pid} {period}: "
                                f"earned {earned}, topped up to {draw_amt}"
                            ),
                        ))

        # Delta-based true-up for locked periods
        if locked_periods and effective_period:
            # Retain only non-locked-period commissions at full amount
            non_locked = [c for c in all_commissions if c.period not in locked_periods]

            if prior_commissions:
                locked_current = [c for c in all_commissions if c.period in locked_periods]
                deltas = _diff_commissions(prior_commissions, locked_current)
            else:
                # Without prior, emit full amounts as new (backward compat / no lock yet)
                deltas = [
                    CommissionDelta(
                        transaction_id=c.transaction_id,
                        payee_id=c.payee_id,
                        rule_id=c.rule_id,
                        origin_period=c.period,
                        prior_amount=Decimal("0"),
                        new_amount=c.commission_amount,
                        delta=c.commission_amount,
                        is_new=True,
                        is_removed=False,
                        base_amount=c.commission_amount,
                        rate=Decimal("1"),
                        kind=c.kind,
                        split_pct=c.split_pct,
                    )
                    for c in all_commissions
                    if c.period in locked_periods
                ]

            true_up_lines: list[Commission] = []
            for d in deltas:
                notes = f"true_up: {d.prior_amount} → {d.new_amount} (delta {d.delta})"
                c = Commission(
                    transaction_id=d.transaction_id,
                    payee_id=d.payee_id,
                    period=effective_period,
                    origin_period=d.origin_period,
                    rule_id=d.rule_id,
                    base_amount=d.base_amount,
                    rate=d.rate,
                    commission_amount=d.delta,
                    kind=d.kind,
                    split_pct=d.split_pct,
                    notes=notes,
                )
                true_up_lines.append(c)
                all_ledger.append(LedgerEntry(
                    transaction_id=c.transaction_id,
                    payee_id=c.payee_id,
                    rule_id=c.rule_id,
                    event_type="true_up",
                    inputs={
                        "origin_period": c.origin_period,
                        "effective_period": effective_period,
                        "prior_amount": str(d.prior_amount),
                        "new_amount": str(d.new_amount),
                        "delta": str(d.delta),
                    },
                    outputs={"commission_amount": str(c.commission_amount)},
                    human_readable=(
                        f"True-up {c.transaction_id} from {c.origin_period} "
                        f"→ {effective_period}: {c.commission_amount} ({c.notes})"
                    ),
                ))

            all_commissions = non_locked + true_up_lines

        # --- Manual adjustments (post-locking, post-caps/draws) ---
        if adjustments:
            for adj in adjustments:
                pid = getattr(adj, "payee_id", "")
                period_val = getattr(adj, "period", "")
                amt = Decimal(str(getattr(adj, "amount", "0")))
                reason = str(getattr(adj, "reason", ""))
                adj_id = str(getattr(adj, "id", ""))
                c = Commission(
                    transaction_id=adj_id or f"adj_{pid}_{period_val}",
                    payee_id=pid,
                    period=period_val,
                    rule_id="manual_adjustment",
                    base_amount=amt,
                    rate=Decimal("1"),
                    commission_amount=amt,
                    notes=reason,
                )
                all_commissions.append(c)
                all_ledger.append(LedgerEntry(
                    transaction_id=c.transaction_id,
                    payee_id=pid,
                    rule_id="manual_adjustment",
                    event_type="manual_adjustment",
                    inputs={"amount": str(amt), "reason": reason},
                    outputs={"commission_amount": str(amt)},
                    human_readable=f"Manual adjustment for {pid} {period_val}: {amt} ({reason})",
                ))

        return CalculationResult(
            commissions=all_commissions, ledger=all_ledger,
            attainment=attainment, draw_balances=draw_balances,
        )

    def true_up(
        self,
        plan: Plan,
        transactions: list[Transaction],
        payees: list[Payee],
        prior: list[Commission],
    ) -> TrueUpResult:
        """Recompute and diff against prior commissions.

        Returns adjustments (per payee/period), exceptions (only changed deals),
        and true_up ledger entries.
        """
        current = self.calculate(plan, transactions, payees)

        deltas = _diff_commissions(prior, current.commissions)

        # Aggregate adjustments by (payee, period)
        adj_map: dict[tuple[str, str], Decimal] = {}
        exceptions: list[dict[str, Any]] = []

        for d in deltas:
            pid = d.payee_id
            period = d.origin_period
            adj_key = (pid, period)
            adj_map[adj_key] = adj_map.get(adj_key, Decimal("0")) + d.delta

            status: str
            if d.is_new:
                status = "new"
            elif d.is_removed:
                status = "removed"
            else:
                status = "changed"

            exceptions.append({
                "transaction_id": d.transaction_id,
                "payee_id": pid,
                "rule_id": d.rule_id,
                "status": status,
                "prior_amount": str(d.prior_amount),
                "new_amount": str(d.new_amount),
                "delta": str(d.delta),
            })

        # Build sorted adjustments
        adjustments = sorted(
            [
                Adjustment(
                    payee_id=pid,
                    period=period,
                    prior_amount=Decimal("0"),
                    new_amount=Decimal("0"),
                    delta=delta,
                )
                for (pid, period), delta in adj_map.items()
            ],
            key=lambda a: (a.payee_id, a.period),
        )

        # Fill in prior/new totals for each adjustment
        for adj in adjustments:
            prior_total = sum(
                (c.commission_amount for c in prior
                if c.payee_id == adj.payee_id and c.period == adj.period),
                Decimal("0")
            )
            new_total = sum(
                (c.commission_amount for c in current.commissions
                if c.payee_id == adj.payee_id and c.period == adj.period),
                Decimal("0")
            )
            adj.prior_amount = prior_total
            adj.new_amount = new_total

        # True-up ledger entries
        tu_ledger: list[LedgerEntry] = []
        for adj in adjustments:
            tu_ledger.append(LedgerEntry(
                transaction_id="*",
                payee_id=adj.payee_id,
                rule_id="*",
                event_type="true_up",
                inputs={
                    "period": adj.period,
                    "prior_amount": str(adj.prior_amount),
                    "new_amount": str(adj.new_amount),
                },
                outputs={"delta": str(adj.delta)},
                human_readable=(
                    f"True-up {adj.payee_id} {adj.period}: "
                    f"{adj.prior_amount} → {adj.new_amount} "
                    f"(delta {adj.delta})"
                ),
            ))

        return TrueUpResult(
            current=current,
            adjustments=adjustments,
            exceptions=exceptions,
            ledger=tu_ledger,
        )

    # -- flat rate -----------------------------------------------------------

    def _calc_flat_rate(
        self,
        rule: FlatRateRule,
        transactions: list[Transaction],
        payee_map: dict[str, Payee],
        period_type: str = "monthly",
        quota_category: str | None = None,
    ) -> tuple[list[Commission], list[LedgerEntry]]:
        predicate = compile_filter(rule.filter)
        results: list[Commission] = []
        ledger: list[LedgerEntry] = []
        for txn in transactions:
            if not predicate(txn):
                ledger.append(
                    LedgerEntry(
                        transaction_id=txn.id,
                        payee_id=txn.payee_id,
                        rule_id=rule.id,
                        event_type="rule_skipped",
                        inputs={"filter": rule.filter or "(none)", "reason": "filter_excluded"},
                        human_readable=(
                            f"Transaction {txn.id} skipped by rule {rule.id}"
                        ),
                    )
                )
                continue
            commission_amount = rule.rate * txn.amount
            window = _window_key(txn.period, period_type)
            results.append(
                Commission(
                    transaction_id=txn.id,
                    payee_id=txn.payee_id,
                    period=window,
                    rule_id=rule.id,
                    base_amount=txn.amount,
                    rate=rule.rate,
                    commission_amount=commission_amount,
                    notes=f"Flat rate {rule.rate} on {txn.amount}",
                )
            )
            ledger.append(
                LedgerEntry(
                    transaction_id=txn.id,
                    payee_id=txn.payee_id,
                    rule_id=rule.id,
                    event_type="commission_computed",
                    inputs={
                        "amount": str(txn.amount),
                        "rate": str(rule.rate),
                    },
                    outputs={"commission_amount": str(commission_amount)},
                    human_readable=(
                        f"Flat rate: {txn.amount} * {rule.rate} = {commission_amount}"
                    ),
                )
            )
        return results, ledger

    # -- tiered --------------------------------------------------------------

    def _calc_tiered(
        self,
        rule: TieredRule,
        transactions: list[Transaction],
        payee_map: dict[str, Payee],
        period_type: str = "monthly",
        quota_category: str | None = None,
    ) -> tuple[list[Commission], list[LedgerEntry]]:
        predicate = compile_filter(rule.filter)
        results: list[Commission] = []
        ledger: list[LedgerEntry] = []

        matching: list[Transaction] = []
        for t in transactions:
            if not predicate(t):
                ledger.append(LedgerEntry(
                    transaction_id=t.id,
                    payee_id=t.payee_id,
                    rule_id=rule.id,
                    event_type="rule_skipped",
                    inputs={"filter": rule.filter or "(none)", "reason": "filter_excluded"},
                    human_readable=f"Transaction {t.id} skipped by rule {rule.id} (filter excluded)",
                ))
            else:
                matching.append(t)

        grouped = self._group_by_payee_period(matching, period_type)

        for (payee_id, window), txn_group in grouped.items():
            payee = payee_map.get(payee_id)
            if payee is None:
                for t in txn_group:
                    ledger.append(LedgerEntry(
                        transaction_id=t.id,
                        payee_id=payee_id,
                        rule_id=rule.id,
                        event_type="rule_skipped",
                        inputs={"filter": rule.filter or "(none)", "reason": "payee_not_found"},
                        human_readable=(
                            f"Transaction {t.id} skipped by rule {rule.id}: "
                            f"payee {payee_id} not found"
                        ),
                    ))
                continue

            ledger.append(
                LedgerEntry(
                    transaction_id="*",
                    payee_id=payee_id,
                    rule_id=rule.id,
                    event_type="rule_evaluated",
                    inputs={
                        "payee_id": payee_id,
                        "txn_count": str(len(txn_group)),
                    },
                    human_readable=(
                        f"Evaluating tiered rule {rule.id} for {payee_id}"
                    ),
                )
            )

            quota = payee.quota_for(window, category=quota_category)

            if quota == Decimal("0"):
                top_tier = rule.tiers[-1]
                for txn in txn_group:
                    commission = txn.amount * top_tier.rate
                    results.append(
                        Commission(
                            transaction_id=txn.id,
                            payee_id=txn.payee_id,
                            period=window,
                            rule_id=rule.id,
                            base_amount=txn.amount,
                            rate=top_tier.rate,
                            commission_amount=commission,
                            notes="Quota is zero — top tier applied",
                        )
                    )
                    ledger.append(
                        LedgerEntry(
                            transaction_id=txn.id,
                            payee_id=txn.payee_id,
                            rule_id=rule.id,
                            event_type="commission_computed",
                            inputs={
                                "amount": str(txn.amount),
                                "rate": str(top_tier.rate),
                                "quota": "0",
                            },
                            outputs={"commission_amount": str(commission)},
                            human_readable=(
                                f"Zero-quota tiered: {txn.amount} * "
                                f"{top_tier.rate} = {commission}"
                            ),
                        )
                    )
                continue

            sorted_txns = sorted(txn_group, key=_sort_key)
            cumulative = Decimal("0")

            for txn in sorted_txns:
                remainder = txn.amount
                _iter = 0
                while remainder > 0:
                    _iter += 1
                    if _iter > 10000:
                        raise RuntimeError(
                            f"Tiered calculation exceeded 10000 iterations for payee "
                            f"{payee_id} — likely a bug in tier thresholds or infinite loop"
                        )
                    current_pct = cumulative / quota
                    tier = self._find_tier(rule.tiers, current_pct)
                    if tier is None:
                        # Attainment at or above all tiers — remainder at highest rate
                        top = rule.tiers[-1]
                        commission = remainder * top.rate
                        results.append(
                            Commission(
                                transaction_id=txn.id,
                                payee_id=txn.payee_id,
                                period=window,
                                rule_id=rule.id,
                                base_amount=remainder,
                                rate=top.rate,
                                commission_amount=commission,
                                notes=f"Tier {top.threshold_pct} (top): {remainder} @ {top.rate}",
                            )
                        )
                        ledger.append(
                            LedgerEntry(
                                transaction_id=txn.id,
                                payee_id=txn.payee_id,
                                rule_id=rule.id,
                                event_type="commission_computed",
                                inputs={
                                    "amount": str(remainder),
                                    "rate": str(top.rate),
                                    "cumulative_pct": str(cumulative / quota),
                                    "quota": str(quota),
                                },
                                outputs={"commission_amount": str(commission)},
                                human_readable=(
                                    f"Tiered (top tier {top.threshold_pct}): "
                                    f"{remainder} @ {top.rate} = {commission}"
                                ),
                            )
                        )
                        cumulative += remainder
                        remainder = Decimal("0")
                        break

                    remaining_in_tier = self._remaining_in_tier(
                        rule.tiers, tier, current_pct, quota
                    )
                    piece = min(remainder, remaining_in_tier)
                    commission = piece * tier.rate
                    prev_cum_pct = cumulative / quota
                    cumulative += piece
                    new_cum_pct = cumulative / quota

                    results.append(
                        Commission(
                            transaction_id=txn.id,
                            payee_id=txn.payee_id,
                            period=window,
                            rule_id=rule.id,
                            base_amount=piece,
                            rate=tier.rate,
                            commission_amount=commission,
                            notes=f"Tier {tier.threshold_pct}: {piece} @ {tier.rate}",
                        )
                    )
                    if prev_cum_pct < tier.threshold_pct <= new_cum_pct:
                        ledger.append(
                            LedgerEntry(
                                transaction_id=txn.id,
                                payee_id=txn.payee_id,
                                rule_id=rule.id,
                                event_type="tier_crossed",
                                inputs={
                                    "threshold_pct": str(tier.threshold_pct),
                                    "from_pct": str(prev_cum_pct),
                                    "to_pct": str(new_cum_pct),
                                },
                                human_readable=(
                                    f"Crossed tier {tier.threshold_pct}: "
                                    f"{prev_cum_pct} -> {new_cum_pct}"
                                ),
                            )
                        )
                    ledger.append(
                        LedgerEntry(
                            transaction_id=txn.id,
                            payee_id=txn.payee_id,
                            rule_id=rule.id,
                            event_type="commission_computed",
                            inputs={
                                "amount": str(piece),
                                "rate": str(tier.rate),
                                "cumulative_pct": str(new_cum_pct),
                                "quota": str(quota),
                            },
                            outputs={"commission_amount": str(commission)},
                            human_readable=(
                                f"Tiered: {piece} @ {tier.rate} "
                                f"(at {new_cum_pct:.1%} of quota) = {commission}"
                            ),
                        )
                    )
                    remainder -= piece

        return results, ledger

    @staticmethod
    def _find_tier(tiers: list[Tier], current_pct: Decimal) -> Tier | None:
        for tier in tiers:
            if current_pct < tier.threshold_pct:
                return tier
        return None

    @staticmethod
    def _remaining_in_tier(
        tiers: list[Tier], current_tier: Tier, current_pct: Decimal, quota: Decimal
    ) -> Decimal:
        threshold = current_tier.threshold_pct
        remaining_pct = threshold - current_pct
        return remaining_pct * quota

    # -- accelerator ---------------------------------------------------------

    def _calc_accelerator(
        self,
        rule: AcceleratorRule,
        transactions: list[Transaction],
        payee_map: dict[str, Payee],
        period_type: str = "monthly",
        quota_category: str | None = None,
    ) -> tuple[list[Commission], list[LedgerEntry]]:
        predicate = compile_filter(rule.filter)
        results: list[Commission] = []
        ledger: list[LedgerEntry] = []

        matching: list[Transaction] = []
        for t in transactions:
            if not predicate(t):
                ledger.append(LedgerEntry(
                    transaction_id=t.id,
                    payee_id=t.payee_id,
                    rule_id=rule.id,
                    event_type="rule_skipped",
                    inputs={"filter": rule.filter or "(none)", "reason": "filter_excluded"},
                    human_readable=f"Transaction {t.id} skipped by rule {rule.id} (filter excluded)",
                ))
            else:
                matching.append(t)

        grouped = self._group_by_payee_period(matching, period_type)

        for (payee_id, window), txn_group in grouped.items():
            payee = payee_map.get(payee_id)
            if payee is None:
                for t in txn_group:
                    ledger.append(LedgerEntry(
                        transaction_id=t.id,
                        payee_id=payee_id,
                        rule_id=rule.id,
                        event_type="rule_skipped",
                        inputs={"filter": rule.filter or "(none)", "reason": "payee_not_found"},
                        human_readable=(
                            f"Transaction {t.id} skipped by rule {rule.id}: "
                            f"payee {payee_id} not found"
                        ),
                    ))
                continue
            quota = payee.quota_for(window, category=quota_category)
            if quota == Decimal("0"):
                for t in txn_group:
                    ledger.append(LedgerEntry(
                        transaction_id=t.id,
                        payee_id=payee_id,
                        rule_id=rule.id,
                        event_type="rule_skipped",
                        inputs={"filter": rule.filter or "(none)", "reason": "zero_quota"},
                        human_readable=(
                            f"Transaction {t.id} skipped by rule {rule.id}: "
                            f"payee {payee_id} has zero quota"
                        ),
                    ))
                continue

            sorted_txns = sorted(txn_group, key=_sort_key)
            cumulative = Decimal("0")

            for txn in sorted_txns:
                threshold_amount = rule.threshold_pct * quota
                below_threshold = max(Decimal("0"), threshold_amount - cumulative)
                above_threshold = max(Decimal("0"), txn.amount - below_threshold)

                if above_threshold > 0:
                    commission = above_threshold * rule.rate * rule.multiplier
                    results.append(
                        Commission(
                            transaction_id=txn.id,
                            payee_id=txn.payee_id,
                            period=window,
                            rule_id=rule.id,
                            base_amount=above_threshold,
                            rate=rule.rate * rule.multiplier,
                            commission_amount=commission,
                            notes=(
                                f"Accelerator {rule.multiplier}x on "
                                f"{above_threshold} above {rule.threshold_pct}"
                            ),
                        )
                    )
                    ledger.append(
                        LedgerEntry(
                            transaction_id=txn.id,
                            payee_id=txn.payee_id,
                            rule_id=rule.id,
                            event_type="commission_computed",
                            inputs={
                                "above_threshold": str(above_threshold),
                                "rate": str(rule.rate),
                                "multiplier": str(rule.multiplier),
                                "cumulative": str(cumulative),
                                "quota": str(quota),
                            },
                            outputs={"commission_amount": str(commission)},
                            human_readable=(
                                f"Accelerator: {above_threshold} * "
                                f"{rule.rate} * {rule.multiplier} = {commission}"
                            ),
                        )
                    )

                cumulative += txn.amount

        return results, ledger

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _group_by_payee_period(
        transactions: list[Transaction],
        period_type: str = "monthly",
    ) -> dict[tuple[str, str], list[Transaction]]:
        grouped: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
        for txn in transactions:
            key = _window_key(txn.period, period_type)
            grouped[(txn.payee_id, key)].append(txn)
        return grouped


# ------------------------------------------------------------------
# Payee trace — full pipeline breakdown per payee/period
# ------------------------------------------------------------------


def build_payee_trace(
    payee_id: str,
    period: str,
    *,
    commissions: list[Commission],
    ledger: list[LedgerEntry],
    attainment: list[Any],
    plan_name: str = "",
    payout_cap: Decimal | None = None,
    draw_cfg: Any = None,
) -> dict[str, Any]:
    """Build a self-describing pipeline trace for one payee in one period.

    Returns a list of stage dicts that the UI renders in order.
    Each stage has an id, label, kind, inputs, and output.
    """

    # Filter commissions for this payee+period
    my_commissions = [
        c for c in commissions
        if c.payee_id == payee_id and (c.period == period or c.origin_period == period)
    ]

    # Filter ledger for this payee+period
    my_ledger = [
        e for e in ledger
        if e.payee_id == payee_id
        and (e.transaction_id != "*" or e.event_type not in ("credit_allocated", "attainment_computed"))
    ]

    stages: list[dict[str, Any]] = []

    # --- Attainment header ---
    att_info: dict[str, Any] = {}
    for a in attainment:
        if getattr(a, "payee_id", "") == payee_id:
            att_info = {
                "bookings": str(getattr(a, "bookings", "0")),
                "quota": str(getattr(a, "quota", "0")),
                "pct": str(getattr(a, "attainment_pct", "N/A")),
            }
            break

    # --- Stage 1: Rules ---
    rule_lines: dict[str, list[dict[str, Any]]] = {}
    for c in my_commissions:
        if c.rule_id in ("payout_cap", "draw", "manual_adjustment"):
            continue
        rule_lines.setdefault(c.rule_id, []).append({
            "txn_id": c.transaction_id,
            "base": str(c.base_amount),
            "rate": str(c.rate),
            "amount": str(c.commission_amount),
            "notes": c.notes,
        })
    rule_total = sum(
        (Decimal(r["amount"]) for lines in rule_lines.values() for r in lines),
        Decimal("0")
    )
    stages.append({
        "id": "rules", "label": "Rules", "kind": "computation",
        "inputs": {"attainment": att_info},
        "output": {
            "total": str(rule_total),
            "by_rule": {rid: {"lines": lines, "total": str(sum(Decimal(r["amount"]) for r in lines))}
                        for rid, lines in sorted(rule_lines.items())},
        },
    })

    # --- Stage 2: Plan cap ---
    cap_line = next((c for c in my_commissions if c.rule_id == "payout_cap"), None)
    if cap_line is not None or payout_cap is not None:
        cap_val = str(payout_cap) if payout_cap is not None else "N/A"
        cap_adj = str(cap_line.commission_amount) if cap_line else "0"
        post_cap = rule_total + (cap_line.commission_amount if cap_line else Decimal("0"))
        stages.append({
            "id": "plan_cap", "label": "Plan Cap", "kind": "adjustment",
            "inputs": {"earned": str(rule_total), "cap": cap_val},
            "output": {
                "total": str(post_cap),
                "adjustment": cap_adj,
                "note": f"capped: {rule_total} → {post_cap}" if cap_line else "no cap applied",
            },
        })
        running_total = post_cap
    else:
        running_total = rule_total

    # --- Stage 3: Draw ---
    draw_lines = [c for c in my_commissions if c.rule_id == "draw"]
    if draw_lines:
        draw_total = sum(c.commission_amount for c in draw_lines)
        draw_ledger = [e for e in my_ledger if e.event_type == "draw"]
        draw_inputs: dict[str, str] = {}
        if draw_ledger:
            draw_inputs = draw_ledger[0].inputs
        stages.append({
            "id": "draw", "label": "Draw", "kind": "adjustment",
            "inputs": draw_inputs,
            "output": {
                "total": str(running_total + draw_total),
                "adjustment": str(draw_total),
                "note": draw_ledger[0].human_readable if draw_ledger else "",
            },
        })
        running_total = running_total + draw_total

    # --- Stage 4: Cross-period ---
    cross_items: list[dict[str, Any]] = []
    for c in my_commissions:
        if c.origin_period and c.origin_period != c.period and c.rule_id != "draw":
            cross_items.append({
                "type": "true_up",
                "origin": c.origin_period,
                "amount": str(c.commission_amount),
                "note": c.notes,
            })
    # Add draw recovery as cross-period if it references prior balance
    for e in my_ledger:
        if e.event_type == "draw" and e.inputs.get("recovered", "0") != "0":
            cross_items.append({
                "type": "draw_carry",
                "origin": "prior periods",
                "amount": "-" + e.inputs.get("recovered", "0"),
                "note": f"draw recovery from prior balance {e.inputs.get('prior_balance', '?')}",
            })
    if cross_items:
        cross_total = sum(Decimal(it["amount"]) for it in cross_items)
        stages.append({
            "id": "cross_period", "label": "Cross-Period", "kind": "cross_period",
            "items": cross_items,
            "output": {"cross_period_total": str(cross_total)},
        })
        running_total = running_total + cross_total

    # --- Stage 5: Manual adjustments ---
    manual_lines = [c for c in my_commissions if c.rule_id == "manual_adjustment"]
    if manual_lines:
        manual_items = [
            {"type": "manual_adjustment", "amount": str(c.commission_amount),
             "reason": c.notes}
            for c in manual_lines
        ]
        manual_total = sum(c.commission_amount for c in manual_lines)
        stages.append({
            "id": "manual", "label": "Manual Adjustments", "kind": "manual",
            "items": manual_items,
            "output": {"manual_total": str(manual_total)},
        })
        running_total = running_total + manual_total

    # --- Stage 6: Final ---
    stages.append({
        "id": "final", "label": "Payout", "kind": "final",
        "output": {"total": str(running_total), "currency": "USD"},
    })

    return {
        "payee_id": payee_id,
        "period": period,
        "plan_name": plan_name,
        "stages": stages,
    }

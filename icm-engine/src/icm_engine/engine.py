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


class _AST(Protocol):
    def eval(self, txn: Transaction) -> bool: ...


@dataclass
class _Comparison:
    field: str
    op: str
    value: object

    def eval(self, txn: Transaction) -> bool:
        field_val: object = getattr(txn, self.field, None)
        rhs: object = self.value
        if isinstance(field_val, Decimal):
            rhs = Decimal(str(self.value))
        if self.op == "==":
            return bool(field_val == rhs)
        if self.op == "!=":
            return bool(field_val != rhs)
        if self.op in (">", "<", ">=", "<=") and isinstance(field_val, Decimal):
            d_rhs = Decimal(str(self.value))
            if self.op == ">":
                return bool(field_val > d_rhs)
            if self.op == "<":
                return bool(field_val < d_rhs)
            if self.op == ">=":
                return bool(field_val >= d_rhs)
            if self.op == "<=":
                return bool(field_val <= d_rhs)
        return False


@dataclass
class _InExpr:
    field: str
    values: list[object]

    def eval(self, txn: Transaction) -> bool:
        field_val: object = getattr(txn, self.field, None)
        return field_val in self.values


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


def _tokenize(source: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    while i < len(source):
        c = source[i]
        if c in (" ", "\t", "\n"):
            i += 1
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


def compile_filter(source: str | None) -> Callable[[Transaction], bool]:
    """Compile a filter expression string into a callable predicate.

    Returns a predicate that always returns True if source is None or blank.
    """
    if source is None or source.strip() == "":
        return lambda txn: True
    tokens = _tokenize(source)
    ast: _AST = _Parser(tokens).parse()
    return ast.eval


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
        quota = p.quota if p else Decimal("0")
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


class CommissionEngine:
    def calculate(
        self,
        plan: Plan,
        transactions: list[Transaction],
        payees: list[Payee],
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

        for rule in plan.rules:
            # Build synthetic transactions for this rule's evaluation
            synth_txns = _make_synthetic_transactions(credits)
            if isinstance(rule, FlatRateRule):
                commissions, ledger = self._calc_flat_rate(
                    rule, synth_txns, payee_map, pt
                )
            elif isinstance(rule, TieredRule):
                commissions, ledger = self._calc_tiered(
                    rule, synth_txns, payee_map, pt
                )
            elif isinstance(rule, AcceleratorRule):
                commissions, ledger = self._calc_accelerator(
                    rule, synth_txns, payee_map, pt
                )
            else:
                continue
            # Stamp credit metadata onto commissions
            _stamp_credits(commissions, credits)
            all_commissions.extend(commissions)
            all_ledger.extend(ledger)

        return CalculationResult(commissions=all_commissions, ledger=all_ledger, attainment=attainment)

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

        # Build lookup: key -> Commission for both sides
        def _key(c: Commission) -> tuple[str, str, str]:
            return (c.transaction_id, c.rule_id, c.payee_id)

        prior_map: dict[tuple[str, str, str], Commission] = {}
        for c in prior:
            prior_map[_key(c)] = c

        current_map: dict[tuple[str, str, str], Commission] = {}
        for c in current.commissions:
            current_map[_key(c)] = c

        all_keys = set(prior_map.keys()) | set(current_map.keys())

        # Aggregate adjustments by (payee, period)
        adj_map: dict[tuple[str, str], Decimal] = {}
        exceptions: list[dict[str, Any]] = []

        for key in sorted(all_keys):
            tid, rid, pid = key
            pc = prior_map.get(key)
            cc = current_map.get(key)
            prior_amt = pc.commission_amount if pc else Decimal("0")
            new_amt = cc.commission_amount if cc else Decimal("0")
            delta = new_amt - prior_amt

            if delta != 0:
                adj_key = (pid, cc.period if cc else (pc.period if pc else "unknown"))
                adj_map[adj_key] = adj_map.get(adj_key, Decimal("0")) + delta

                status: str
                if pc is None:
                    status = "new"
                elif cc is None:
                    status = "removed"
                else:
                    status = "changed"

                exceptions.append({
                    "transaction_id": tid,
                    "payee_id": pid,
                    "rule_id": rid,
                    "status": status,
                    "prior_amount": str(prior_amt),
                    "new_amount": str(new_amt),
                    "delta": str(delta),
                })

        # Build sorted adjustments
        adjustments = sorted(
            [
                Adjustment(
                    payee_id=pid,
                    period=period,
                    prior_amount=Decimal("0"),  # computed from diff
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
                c.commission_amount for c in prior
                if c.payee_id == adj.payee_id and c.period == adj.period
            )
            new_total = sum(
                c.commission_amount for c in current.commissions
                if c.payee_id == adj.payee_id and c.period == adj.period
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

            quota = payee.quota
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
            quota = payee.quota
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

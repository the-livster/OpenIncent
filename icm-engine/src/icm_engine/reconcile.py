"""Reconcile a commission run against what was actually paid.

The managed-run wedge as a single step: take the commissions the engine computed
(the plan, applied exactly) and a file of what the client actually paid their
reps, and report every discrepancy — who was underpaid, who was overpaid, who was
missed, and who was paid with no matching commission.

Like `validate`, this is a *report*, not a judgment: results are returned, never
raised, and nothing here touches the calculation core. The numbers being diffed
were already computed by the engine; this only compares two sets of totals.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from icm_engine.models import Commission

Status = Literal["match", "underpaid", "overpaid", "missing", "unexpected"]

# A (payee_id, period) key for one reconciliation cell.
Key = tuple[str, str]

_CENTS = Decimal("0.01")

# Tolerant header aliases for a client-supplied "what we paid" file.
_PAYEE_ALIASES = {
    "payee_id", "payee", "rep", "rep_id", "recruiter", "recruiter_id",
    "employee", "employee_id", "id", "name",
}
_PERIOD_ALIASES = {"period", "month", "pay_period", "payroll_period", "pay_month"}
_PAID_ALIASES = {
    "amount_paid", "paid", "paid_amount", "amount", "commission_paid",
    "commission", "payout", "total", "total_paid",
}
_COMMISSION_AMOUNT_ALIASES = {"commission_amount", "amount", "commission", "payout"}


def _parse_money(raw: str | None) -> Decimal:
    """Parse a money cell tolerantly: strips currency symbols, thousands commas,
    and whitespace; reads parenthesised values as negative. Blank → 0."""
    if raw is None:
        return Decimal("0")
    s = str(raw).strip()
    if not s:
        return Decimal("0")
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    for sym in ("$", "£", "€", ",", " "):
        s = s.replace(sym, "")
    if not s or s in {"-", "."}:
        return Decimal("0")
    try:
        value = Decimal(s)
    except InvalidOperation as e:
        raise ValueError(f"Cannot parse amount {raw!r}") from e
    return -value if negative else value


def _q(amount: Decimal) -> Decimal:
    """Quantize to cents for display/report stability."""
    return amount.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _resolve_header(fieldnames: list[str], aliases: set[str]) -> str | None:
    """Return the first column whose normalised name is in `aliases`."""
    for name in fieldnames:
        if name is None:
            continue
        if name.strip().lower().replace(" ", "_") in aliases:
            return name
    return None


@dataclass
class ReconcileLine:
    payee_id: str
    period: str
    computed: Decimal  # what the plan says (engine)
    paid: Decimal      # what the client actually paid
    delta: Decimal     # computed - paid  (positive = rep is owed more)
    status: Status

    @property
    def abs_delta(self) -> Decimal:
        return abs(self.delta)


@dataclass
class ReconcileReport:
    lines: list[ReconcileLine] = field(default_factory=list)
    tolerance: Decimal = _CENTS

    @property
    def discrepancies(self) -> list[ReconcileLine]:
        return [ln for ln in self.lines if ln.status != "match"]

    @property
    def has_discrepancies(self) -> bool:
        return any(ln.status != "match" for ln in self.lines)

    @property
    def total_computed(self) -> Decimal:
        return sum((ln.computed for ln in self.lines), Decimal("0"))

    @property
    def total_paid(self) -> Decimal:
        return sum((ln.paid for ln in self.lines), Decimal("0"))

    @property
    def net_delta(self) -> Decimal:
        """computed - paid across everyone (positive = under-paid in aggregate)."""
        return self.total_computed - self.total_paid

    @property
    def total_owed_to_payees(self) -> Decimal:
        """Sum of money reps are owed (underpaid + missed)."""
        return sum((ln.delta for ln in self.lines if ln.delta > 0), Decimal("0"))

    @property
    def total_overpaid(self) -> Decimal:
        """Sum of money paid above plan (overpaid + unexpected)."""
        return sum((-ln.delta for ln in self.lines if ln.delta < 0), Decimal("0"))

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for ln in self.lines:
            out[ln.status] = out.get(ln.status, 0) + 1
        return out


def commissions_to_totals(commissions: list[Commission]) -> dict[Key, Decimal]:
    """Aggregate engine commission lines into per-(payee, period) totals."""
    totals: dict[Key, Decimal] = {}
    for c in commissions:
        key = (c.payee_id, c.period)
        totals[key] = totals.get(key, Decimal("0")) + c.commission_amount
    return totals


def reconcile_totals(
    computed: dict[Key, Decimal],
    paid: dict[Key, Decimal],
    tolerance: Decimal = _CENTS,
) -> ReconcileReport:
    """Diff computed vs paid per (payee, period). Returns a sorted report.

    Classification (delta = computed - paid):
      match       |delta| <= tolerance
      underpaid   present in both, computed > paid  (rep is owed more)
      overpaid    present in both, paid > computed  (client paid above plan)
      missing     only computed (> tol)             (rep never paid)
      unexpected  only paid (> tol)                 (paid with no commission)
    """
    report = ReconcileReport(tolerance=tolerance)
    for key in sorted(set(computed) | set(paid)):
        comp = computed.get(key, Decimal("0"))
        pay = paid.get(key, Decimal("0"))
        delta = comp - pay
        in_computed = key in computed
        in_paid = key in paid

        status: Status
        if abs(delta) <= tolerance:
            status = "match"
        elif in_computed and not in_paid:
            status = "missing"
        elif in_paid and not in_computed:
            status = "unexpected"
        elif delta > 0:
            status = "underpaid"
        else:
            status = "overpaid"

        report.lines.append(ReconcileLine(
            payee_id=key[0], period=key[1],
            computed=_q(comp), paid=_q(pay), delta=_q(delta), status=status,
        ))
    return report


def reconcile_commissions(
    commissions: list[Commission],
    paid: dict[Key, Decimal],
    tolerance: Decimal = _CENTS,
) -> ReconcileReport:
    """Convenience: reconcile engine commission objects against paid totals."""
    return reconcile_totals(commissions_to_totals(commissions), paid, tolerance)


# --- file loaders ---------------------------------------------------------


def load_commission_totals_csv(path: str | Path) -> dict[Key, Decimal]:
    """Load engine `commissions.csv` output and aggregate per (payee, period)."""
    totals: dict[Key, Decimal] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return totals
        amount_col = _resolve_header(list(reader.fieldnames), _COMMISSION_AMOUNT_ALIASES)
        if amount_col is None:
            raise ValueError(
                f"{path}: no commission amount column found "
                f"(looked for {sorted(_COMMISSION_AMOUNT_ALIASES)})"
            )
        for row in reader:
            payee = (row.get("payee_id") or row.get("payee") or "").strip()
            period = (row.get("period") or "").strip()
            if not payee or not period:
                continue
            key = (payee, period)
            totals[key] = totals.get(key, Decimal("0")) + _parse_money(row.get(amount_col))
    return totals


def load_paid_csv(
    path: str | Path, default_period: str | None = None,
) -> dict[Key, Decimal]:
    """Load a client 'what we paid' file into per-(payee, period) totals.

    Tolerant of column names (rep/employee/payee; month/period; paid/amount/...).
    Rows with no period use `default_period` if given, else raise a clear error.
    Repeated (payee, period) rows are summed.
    """
    totals: dict[Key, Decimal] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return totals
        cols = list(reader.fieldnames)
        payee_col = _resolve_header(cols, _PAYEE_ALIASES)
        period_col = _resolve_header(cols, _PERIOD_ALIASES)
        paid_col = _resolve_header(cols, _PAID_ALIASES)
        if payee_col is None or paid_col is None:
            raise ValueError(
                f"{path}: paid file needs a payee column and an amount column "
                f"(found columns: {cols})"
            )
        for row in reader:
            payee = (row.get(payee_col) or "").strip()
            if not payee:
                continue
            period = (row.get(period_col) or "").strip() if period_col else ""
            if not period:
                if default_period is None:
                    raise ValueError(
                        f"{path}: row for {payee!r} has no period and no "
                        f"--period default was provided"
                    )
                period = default_period
            key = (payee, period)
            totals[key] = totals.get(key, Decimal("0")) + _parse_money(row.get(paid_col))
    return totals


def write_report_csv(report: ReconcileReport, path: str | Path) -> None:
    """Write the full reconciliation (all lines) to a CSV."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["payee_id", "period", "computed", "paid", "delta", "status"])
        for ln in report.lines:
            writer.writerow([
                ln.payee_id, ln.period, str(ln.computed), str(ln.paid),
                str(ln.delta), ln.status,
            ])

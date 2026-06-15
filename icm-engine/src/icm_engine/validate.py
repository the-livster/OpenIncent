"""Ingestion validation — surface data problems before they corrupt a run.

These are checks, not judgment: duplicate detection, payee-eligibility guards,
and FX-table completeness. Run via `icm validate` or validate_run(). Issues are
returned (never raised) so the caller decides how to react.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from icm_engine.models import Payee, Period, Plan, Transaction


@dataclass
class ValidationIssue:
    severity: str  # "error" | "warning"
    code: str      # "duplicate" | "ineligible" | "missing_fx"
    message: str


def find_duplicates(transactions: list[Transaction]) -> list[ValidationIssue]:
    """Flag likely double-entries: identical (payee, period, amount), and
    transaction IDs that differ by a single character (edit distance 1)."""
    issues: list[ValidationIssue] = []

    seen: dict[tuple[str, str, Decimal], str] = {}
    for t in transactions:
        key = (t.payee_id, t.period, t.amount)
        if key in seen:
            issues.append(ValidationIssue(
                "warning", "duplicate",
                f"Transactions {seen[key]!r} and {t.id!r} share payee {t.payee_id!r}, "
                f"period {t.period}, and amount {t.amount} — possible double entry.",
            ))
        else:
            seen[key] = t.id

    # Near-duplicate IDs (edit distance 1) — catches a planted near-identical row.
    from rapidfuzz.distance import Levenshtein

    ids = [t.id for t in transactions]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if ids[i] != ids[j] and Levenshtein.distance(ids[i], ids[j]) <= 1:
                issues.append(ValidationIssue(
                    "warning", "duplicate",
                    f"Transaction IDs {ids[i]!r} and {ids[j]!r} differ by one character "
                    f"— possible duplicate.",
                ))
    return issues


def find_ineligible(
    transactions: list[Transaction], payees: list[Payee],
) -> list[ValidationIssue]:
    """Flag transactions credited to a payee outside their employment window.

    Only the end of the window (effective_to) is checked — it catches a
    terminated rep earning on post-termination bookings. The start bound is not
    checked here because effective_from defaults when absent (see F2).
    """
    issues: list[ValidationIssue] = []
    pmap = {p.id: p for p in payees}
    for t in transactions:
        p = pmap.get(t.payee_id)
        if p is None or p.effective_to is None:
            continue
        d = t.close_date
        if d is None and t.period:
            d = Period(t.period).start_date
        if d is None:
            continue
        if d > p.effective_to:
            issues.append(ValidationIssue(
                "warning", "ineligible",
                f"Transaction {t.id!r} ({d}) is after payee {p.id!r}'s end date "
                f"({p.effective_to}) — earning after termination.",
            ))
    return issues


def check_fx_completeness(
    plan: Plan, rates: dict[str, Decimal] | None,
) -> list[ValidationIssue]:
    """Flag a plan that reports in another currency without the rates to convert."""
    issues: list[ValidationIssue] = []
    rc = (plan.reporting_currency or "").strip().upper()
    sc = (plan.currency or "").strip().upper()
    if not rc or rc == sc:
        return issues
    have = {k.upper() for k in (rates or {})}
    for cur in (sc, rc):
        if cur and cur != "USD" and cur not in have:
            issues.append(ValidationIssue(
                "error", "missing_fx",
                f"Plan reports in {rc} but no exchange rate is configured for {cur}.",
            ))
    return issues


def validate_run(
    plan: Plan,
    transactions: list[Transaction],
    payees: list[Payee],
    rates: dict[str, Decimal] | None = None,
) -> list[ValidationIssue]:
    """Run all ingestion checks and return the combined issue list."""
    issues: list[ValidationIssue] = []
    issues += find_duplicates(transactions)
    issues += find_ineligible(transactions, payees)
    issues += check_fx_completeness(plan, rates)
    return issues

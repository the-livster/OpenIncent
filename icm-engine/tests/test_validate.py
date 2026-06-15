"""Ingestion validation layer (D-series): duplicates, eligibility, FX completeness."""

from datetime import date
from decimal import Decimal

from icm_engine.models import Payee, Plan, Transaction
from icm_engine.validate import (
    check_fx_completeness,
    find_duplicates,
    find_ineligible,
    validate_run,
)


def _txn(tid: str, payee: str = "R1", amount: str = "1000", period: str = "2026-06", **kw):
    return Transaction(id=tid, payee_id=payee, amount=Decimal(amount), period=period, **kw)


class TestDuplicates:
    def test_exact_duplicate_flagged(self) -> None:
        issues = find_duplicates([_txn("ALPHA"), _txn("BETA")])  # same payee/period/amount
        assert any(i.code == "duplicate" and "double entry" in i.message for i in issues)

    def test_distinct_not_flagged(self) -> None:
        issues = find_duplicates([_txn("ALPHA", amount="1000"), _txn("OMEGA", amount="2000")])
        assert issues == []

    def test_near_duplicate_ids_flagged(self) -> None:
        # different amounts (no exact dup) but IDs differ by one char
        issues = find_duplicates([_txn("T1001", amount="1000"), _txn("T1002", amount="2000")])
        assert any("differ by one character" in i.message for i in issues)


class TestEligibility:
    def test_after_termination_flagged(self) -> None:
        payees = [Payee(id="R1", name="R1", quota=Decimal("0"), plan_id="p",
                        effective_from=date(2026, 1, 1), effective_to=date(2026, 3, 31))]
        issues = find_ineligible([_txn("D1", period="2026-06")], payees)  # after March
        assert any(i.code == "ineligible" and "after termination" in i.message for i in issues)

    def test_within_window_ok(self) -> None:
        payees = [Payee(id="R1", name="R1", quota=Decimal("0"), plan_id="p",
                        effective_from=date(2026, 1, 1), effective_to=date(2026, 12, 31))]
        assert find_ineligible([_txn("D1", period="2026-06")], payees) == []

    def test_no_end_date_no_flag(self) -> None:
        payees = [Payee(id="R1", name="R1", quota=Decimal("0"), plan_id="p",
                        effective_from=date(2026, 1, 1))]  # effective_to None
        assert find_ineligible([_txn("D1", period="2026-06")], payees) == []


class TestFx:
    def _plan(self, currency: str = "CAD", reporting: str = "USD") -> Plan:
        return Plan(plan_id="p", name="P", period_type="monthly",
                    currency=currency, reporting_currency=reporting)

    def test_missing_rate_is_error(self) -> None:
        issues = check_fx_completeness(self._plan(), None)
        assert any(i.code == "missing_fx" and i.severity == "error" for i in issues)

    def test_rate_present_ok(self) -> None:
        assert check_fx_completeness(self._plan(), {"CAD": Decimal("1.35")}) == []

    def test_no_reporting_currency_ok(self) -> None:
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD")
        assert check_fx_completeness(plan, None) == []


def test_validate_run_aggregates() -> None:
    plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD")
    payees = [Payee(id="R1", name="R1", quota=Decimal("0"), plan_id="p",
                    effective_from=date(2026, 1, 1), effective_to=date(2026, 3, 31))]
    txns = [_txn("D1", period="2026-06"), _txn("D2", period="2026-06")]  # dup + both ineligible
    issues = validate_run(plan, txns, payees)
    codes = {i.code for i in issues}
    assert "duplicate" in codes
    assert "ineligible" in codes

"""Year-to-date attainment: bookings and quota accumulate across the fiscal year.

Default stays "period" — attainment resets every window — so existing plans are
untouched. Only rules that read attainment change: tiered, accelerator, and any
min_attainment_pct gate.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from icm_engine.engine import CommissionEngine
from icm_engine.models import (
    AcceleratorRule,
    Payee,
    Plan,
    Tier,
    TieredRule,
    Transaction,
)


def _plan(basis: str = "period", period_type: str = "monthly") -> Plan:
    return Plan(
        plan_id="p", name="P", currency="USD", period_type=period_type,
        attainment_basis=basis,
        rules=[TieredRule(type="tiered", id="R1", tiers=[
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("1.5"), rate=Decimal("0.10")),
        ])],
    )


def _payee(quota: str = "10000") -> Payee:
    return Payee(id="P1", name="Rep", quota=Decimal(quota), plan_id="p")


def _txn(tid: str, period: str, amount: str) -> Transaction:
    return Transaction(id=tid, payee_id="P1", period=period, amount=Decimal(amount))


def _totals(plan: Plan, txns: list[Transaction]) -> dict[str, Decimal]:
    res = CommissionEngine().calculate(plan, txns, [_payee()])
    out: dict[str, Decimal] = {}
    for c in res.commissions:
        out[c.period] = out.get(c.period, Decimal("0")) + c.commission_amount
    return out


class TestDefaultIsUnchanged:
    def test_basis_defaults_to_period(self) -> None:
        assert Plan(
            plan_id="p", name="P", currency="USD", period_type="monthly",
        ).attainment_basis == "period"

    def test_period_basis_resets_each_window(self) -> None:
        # Feb starts again at tier 1 despite a big January.
        totals = _totals(_plan("period"), [
            _txn("T1", "2026-01", "20000"),
            _txn("T2", "2026-02", "10000"),
        ])
        assert totals["2026-02"] == Decimal("500.00")


class TestCumulative:
    def test_tier_position_carries_into_the_next_window(self) -> None:
        # January's 20,000 leaves the rep at 100% of the YTD quota, so
        # February's 10,000 falls in the 100-150% band at 10%, not tier 1.
        totals = _totals(_plan("cumulative"), [
            _txn("T1", "2026-01", "20000"),
            _txn("T2", "2026-02", "10000"),
        ])
        assert totals["2026-01"] == Decimal("1500.000")
        assert totals["2026-02"] == Decimal("1000.00")

    def test_bookings_and_quota_both_accumulate(self) -> None:
        res = CommissionEngine().calculate(_plan("cumulative"), [
            _txn("T1", "2026-01", "6000"),
            _txn("T2", "2026-02", "6000"),
        ], [_payee()])
        att = {a.period: (a.bookings, a.quota) for a in res.attainment}
        assert att["2026-01"] == (Decimal("6000"), Decimal("10000"))
        assert att["2026-02"] == (Decimal("12000"), Decimal("20000"))

    def test_quiet_window_still_advances_the_quota(self) -> None:
        # No February deals, but February's quota still accrues.
        res = CommissionEngine().calculate(_plan("cumulative"), [
            _txn("T1", "2026-01", "6000"),
            _txn("T2", "2026-03", "6000"),
        ], [_payee()])
        att = {a.period: (a.bookings, a.quota) for a in res.attainment}
        assert att["2026-03"] == (Decimal("12000"), Decimal("30000"))

    def test_new_fiscal_year_resets(self) -> None:
        res = CommissionEngine().calculate(_plan("cumulative"), [
            _txn("T1", "2026-12", "20000"),
            _txn("T2", "2027-01", "5000"),
        ], [_payee()])
        att = {a.period: (a.bookings, a.quota) for a in res.attainment}
        assert att["2027-01"] == (Decimal("5000"), Decimal("10000"))

    def test_annual_period_type_is_a_no_op(self) -> None:
        # One window per year, so there is nothing to accumulate.
        txns = [_txn("T1", "2026-01", "6000"), _txn("T2", "2026-07", "6000")]
        per = _totals(_plan("period", "annual"), txns)
        cum = _totals(_plan("cumulative", "annual"), txns)
        assert per == cum

    def test_accelerator_threshold_uses_ytd(self) -> None:
        plan = Plan(
            plan_id="p", name="P", currency="USD", period_type="monthly",
            attainment_basis="cumulative",
            rules=[AcceleratorRule(
                type="accelerator", id="A1", rate=Decimal("0.05"),
                threshold_pct=Decimal("1.0"), multiplier=Decimal("2"),
            )],
        )
        # January clears the YTD quota, so February's deal is entirely
        # above threshold and accelerates from its first pound.
        res = CommissionEngine().calculate(plan, [
            _txn("T1", "2026-01", "20000"),
            _txn("T2", "2026-02", "10000"),
        ], [_payee()])
        feb = sum(c.commission_amount for c in res.commissions if c.period == "2026-02")
        assert feb == Decimal("10000") * Decimal("0.05") * Decimal("2")


@pytest.mark.parametrize("basis", ["period", "cumulative"])
def test_single_window_agrees_across_bases(basis: str) -> None:
    # With one window there is nothing to accumulate; both must match.
    assert _totals(_plan(basis), [_txn("T1", "2026-01", "12000")])["2026-01"] == Decimal("700.000")

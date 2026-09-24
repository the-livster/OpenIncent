"""Negative lines on tiered and accelerator rules: fall-offs, refunds, credit notes.

Those rules pay a deal by where it lands in the payee's attainment. A reversal
used to be dropped outright on a tiered rule (no line, no ledger entry, no
change to attainment) and never took back accelerated commission, so a
recruiter kept the full commission on a placement that fell through.

A reversal in the same window as its deal now steps back down through the
bands, so the window pays exactly what it would have without the deal. A
reversal the rule cannot price - its deal is in another window, or not in the
run, or it names no deal - stops the run with a message saying what to do.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from icm_engine.engine import CalculationResult, CommissionEngine
from icm_engine.exceptions import ReversalError
from icm_engine.models import (
    AcceleratorRule,
    FlatRateRule,
    Payee,
    Plan,
    Tier,
    TieredRule,
    Transaction,
)


def _perm_plan(basis: str = "period") -> Plan:
    # 10% up to quota, 15% on everything above it.
    return Plan(
        plan_id="perm", name="Perm desk", currency="GBP", period_type="monthly",
        attainment_basis=basis,
        rules=[TieredRule(type="tiered", id="fee", tiers=[
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.10")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.15")),
        ])],
    )


def _rep(pid: str = "P1", quota: str = "20000", plan_id: str = "perm") -> Payee:
    return Payee(id=pid, name=pid, quota=Decimal(quota), plan_id=plan_id)


def _txn(
    tid: str, amount: str, *, deal: str = "", period: str = "2026-05", day: int = 10,
    payee: str = "P1", **extra: object,
) -> Transaction:
    year, month = (int(x) for x in period.split("-"))
    return Transaction(
        id=tid, payee_id=payee, deal_id=deal, period=period, amount=Decimal(amount),
        close_date=date(year, month, day), **extra,
    )


def _total(result: CalculationResult, payee: str = "P1") -> Decimal:
    return sum(
        (c.commission_amount for c in result.commissions if c.payee_id == payee),
        Decimal("0"),
    )


def _run(plan: Plan, txns: list[Transaction], payees: list[Payee] | None = None) -> CalculationResult:
    return CommissionEngine().calculate(plan, txns, payees or [_rep()])


class TestTieredReversalInTheSameMonth:
    def test_booked_and_reversed_pays_as_if_never_booked(self) -> None:
        # 18,000 + 7,200 takes the rep to 126% of quota, into the 15% band.
        # Reversing the 7,200 must take back 780 + 200, not nothing.
        with_fall_off = _run(_perm_plan(), [
            _txn("T1", "18000", deal="D1", day=6),
            _txn("T2", "7200", deal="D2", day=12),
            _txn("T2-CB", "-7200", deal="D2", day=28),
        ])
        never_booked = _run(_perm_plan(), [_txn("T1", "18000", deal="D1", day=6)])
        assert _total(with_fall_off) == _total(never_booked) == Decimal("1800")

    def test_reversal_comes_off_the_top_band_first(self) -> None:
        result = _run(_perm_plan(), [
            _txn("T1", "18000", deal="D1", day=6),
            _txn("T2", "7200", deal="D2", day=12),
            _txn("T2-CB", "-7200", deal="D2", day=28),
        ])
        lines = [c for c in result.commissions if c.transaction_id == "T2-CB"]
        assert [(c.base_amount, c.rate, c.commission_amount) for c in lines] == [
            (Decimal("-5200"), Decimal("0.15"), Decimal("-780")),
            (Decimal("-2000"), Decimal("0.10"), Decimal("-200")),
        ]
        assert "came off between 100% and 126% of quota" in lines[0].notes
        assert "came off between 90% and 100% of quota" in lines[1].notes

    def test_reversal_is_in_the_ledger(self) -> None:
        result = _run(_perm_plan(), [
            _txn("T1", "25200", deal="D1", day=6),
            _txn("T1-CB", "-7200", deal="D1", day=28),
        ])
        # As on the way up, the crossing is recorded ahead of the slice that makes it.
        events = [e for e in result.ledger if e.transaction_id == "T1-CB"]
        assert [e.event_type for e in events if e.event_type != "credit_allocated"] == [
            "tier_crossed_down", "commission_computed", "commission_computed",
        ]

    def test_order_within_the_month_does_not_matter(self) -> None:
        # A reversal dated ahead of its deal still nets the month out exactly.
        result = _run(_perm_plan(), [
            _txn("T2-CB", "-7200", deal="D2", day=1),
            _txn("T1", "18000", deal="D1", day=6),
            _txn("T2", "7200", deal="D2", day=12),
        ])
        assert _total(result) == Decimal("1800")

    def test_later_deals_price_from_the_reduced_position(self) -> None:
        # After the reversal the rep is back at 90%, so the next 3,000 pays
        # 2,000 at 10% and 1,000 at 15% - not all at 15% from 126%.
        result = _run(_perm_plan(), [
            _txn("T1", "18000", deal="D1", day=6),
            _txn("T2", "7200", deal="D2", day=12),
            _txn("T2-CB", "-7200", deal="D2", day=20),
            _txn("T3", "3000", deal="D3", day=25),
        ])
        t3 = sum((c.commission_amount for c in result.commissions if c.transaction_id == "T3"), Decimal("0"))
        assert t3 == Decimal("350")

    def test_split_reversal_mirrors_the_split_deal(self) -> None:
        payees = [_rep("P1"), _rep("P2")]
        with_fall_off = _run(_perm_plan(), [
            _txn("T1", "18000", deal="D1", day=6),
            _txn("T3", "13000", deal="D3", payee="P2", day=9),
            _txn("T2", "12000", deal="D2", day=12, credits="P1:0.6;P2:0.4"),
            _txn("T2-CB", "-12000", deal="D2", day=28, credits="P1:0.6;P2:0.4"),
        ], payees)
        assert _total(with_fall_off, "P1") == Decimal("1800")
        assert _total(with_fall_off, "P2") == Decimal("1300")

    def test_partial_reversal_takes_back_the_top_of_the_deal(self) -> None:
        # A 50% rebate on the 7,200: 3,600 comes off the 15% band.
        result = _run(_perm_plan(), [
            _txn("T1", "18000", deal="D1", day=6),
            _txn("T2", "7200", deal="D2", day=12),
            _txn("T2-CB", "-3600", deal="D2", day=28),
        ])
        assert _total(result) == Decimal("2780") - Decimal("540")

    def test_zero_rate_band_reverses_to_zero_not_negative_zero(self) -> None:
        # "Nothing below 50% of quota": the slice coming off that band is 0,
        # and must not print as -0.00 on a statement.
        plan = Plan(
            plan_id="gate", name="Gated", currency="GBP", period_type="monthly",
            rules=[TieredRule(type="tiered", id="fee", tiers=[
                Tier(threshold_pct=Decimal("0.5"), rate=Decimal("0")),
                Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
            ])],
        )
        result = _run(plan, [
            _txn("T1", "15000", deal="D1", day=6),
            _txn("T1-CB", "-15000", deal="D1", day=20),
        ], [_rep(plan_id="gate")])
        amounts = [c.commission_amount for c in result.commissions if c.transaction_id == "T1-CB"]
        assert amounts == [Decimal("-500"), Decimal("0")]
        assert not amounts[1].is_signed()

    def test_original_without_a_deal_id_is_found_by_its_own_id(self) -> None:
        result = _run(_perm_plan(), [
            _txn("T1", "18000", day=6),
            _txn("T2", "7200", day=12),
            _txn("T2-CB", "-7200", deal="T2", day=28),
        ])
        assert _total(result) == Decimal("1800")

    def test_margin_credit_note_comes_off_the_band_it_landed_in(self) -> None:
        # Contract desk paid on gross profit: 60/hr billed, 40/hr paid.
        plan = Plan(
            plan_id="contract", name="Contract desk", currency="GBP", period_type="monthly",
            rules=[TieredRule(type="tiered", id="gp", base="margin", tiers=[
                Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.15")),
                Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.225")),
            ])],
        )
        rates = {"bill_rate": Decimal("60"), "pay_rate": Decimal("40")}
        result = _run(plan, [
            _txn("CT-1", "28800", deal="CT-1", day=8, units=Decimal("480"), **rates),
            _txn("CT-1-CN", "-2400", deal="CT-1", day=28, units=Decimal("-40"), **rates),
        ], [_rep(quota="8000", plan_id="contract")])
        # 8,800 GP: 8,000 at 15% + 800 at 22.5%.
        assert _total(result) == Decimal("1380")

    def test_year_to_date_reversal_in_the_same_month_nets_out(self) -> None:
        result = _run(_perm_plan("cumulative"), [
            _txn("T1", "25000", deal="D1", period="2026-01"),
            _txn("T1-CB", "-5000", deal="D1", period="2026-01", day=20),
        ])
        assert _total(result) == Decimal("2000")


class TestTieredReversalRefused:
    def test_reversal_of_an_earlier_month_is_refused(self) -> None:
        with pytest.raises(ReversalError, match="was booked to P1 in 2026-05"):
            _run(_perm_plan(), [
                _txn("T1", "7200", deal="D1"),
                _txn("T1-CB", "-7200", deal="D1", period="2026-06"),
            ])

    def test_reversal_of_a_deal_not_in_the_run_is_refused(self) -> None:
        # The June-only run that used to leave the recruiter holding the money.
        with pytest.raises(ReversalError, match="not booked to P1 anywhere in this run") as exc:
            _run(_perm_plan(), [
                _txn("T4", "5000", deal="D4", period="2026-06", day=3),
                _txn("T2-CB", "-7200", deal="D2", period="2026-06", day=20),
            ])
        assert "re-run the period the deal was booked in" in str(exc.value)

    def test_year_to_date_reversal_of_an_earlier_month_is_refused(self) -> None:
        # January paid the top 5,000 at 15%; February's scale would put it at 10%.
        with pytest.raises(ReversalError, match="its own year-to-date quota"):
            _run(_perm_plan("cumulative"), [
                _txn("T1", "25000", deal="D1", period="2026-01"),
                _txn("T1-CB", "-5000", deal="D1", period="2026-02"),
            ])

    def test_reversal_without_a_deal_id_is_refused(self) -> None:
        with pytest.raises(ReversalError, match="does not name the deal it reverses"):
            _run(_perm_plan(), [
                _txn("T1", "7200", deal="D1"),
                _txn("REFUND", "-7200", day=20),
            ])

    def test_reversal_whose_id_matches_its_deal_is_linked(self) -> None:
        # A reversal row keyed by the placement id is still a real link.
        result = _run(_perm_plan(), [
            _txn("NW-1087", "7200", deal="PL-2"),
            _txn("PL-2", "-7200", deal="PL-2", day=20),
        ])
        assert _total(result) == Decimal("0")

    def test_reversal_larger_than_the_deal_is_refused(self) -> None:
        with pytest.raises(ReversalError, match="take back 10000 but only 7200 was booked"):
            _run(_perm_plan(), [
                _txn("T1", "7200", deal="D1"),
                _txn("T1-CB", "-10000", deal="D1", day=20),
            ])

    def test_reversal_split_differently_from_its_deal_is_refused(self) -> None:
        with pytest.raises(ReversalError, match="split the same way"):
            _run(_perm_plan(), [
                _txn("T2", "12000", deal="D2", credits="P1:0.6;P2:0.4"),
                _txn("T2-CB", "-12000", deal="D2", day=20),
            ], [_rep("P1"), _rep("P2")])

    def test_every_problem_is_reported_at_once(self) -> None:
        with pytest.raises(ReversalError) as exc:
            _run(_perm_plan(), [
                _txn("A-CB", "-100", deal="A", period="2026-06"),
                _txn("B-CB", "-200", deal="B", payee="P2", period="2026-06"),
            ], [_rep("P1"), _rep("P2")])
        assert len(exc.value.problems) == 2

    def test_zero_quota_payee_reverses_at_the_single_rate(self) -> None:
        # With no quota every deal pays the top rate, so the month a reversal
        # lands in cannot change what it is worth: nothing to refuse.
        result = _run(_perm_plan(), [
            _txn("T1", "10000", deal="D1"),
            _txn("T1-CB", "-10000", deal="D1", period="2026-06"),
        ], [_rep(quota="0")])
        by_period = {c.period: c.commission_amount for c in result.commissions}
        assert by_period == {"2026-05": Decimal("1500"), "2026-06": Decimal("-1500")}

    def test_flat_rate_reversals_are_unaffected(self) -> None:
        plan = Plan(
            plan_id="flat", name="Flat", currency="GBP", period_type="monthly",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
        )
        result = _run(plan, [_txn("REFUND", "-5000", period="2026-06")], [_rep(plan_id="flat")])
        assert _total(result) == Decimal("-500")


def _accel_plan() -> Plan:
    # 20% on the part of bookings above 100% of quota.
    return Plan(
        plan_id="acc", name="Accelerator", currency="USD", period_type="monthly",
        rules=[AcceleratorRule(
            type="accelerator", id="acc", rate=Decimal("0.10"),
            threshold_pct=Decimal("1.0"), multiplier=Decimal("2"),
        )],
    )


class TestAcceleratorReversal:
    def _run(self, txns: list[Transaction]) -> CalculationResult:
        return CommissionEngine().calculate(_accel_plan(), txns, [_rep(quota="10000", plan_id="acc")])

    def test_refund_takes_back_the_accelerated_part(self) -> None:
        result = self._run([
            _txn("T1", "15000", deal="D1", day=6),
            _txn("T1-CB", "-8000", deal="D1", day=20),
        ])
        # 7,000 net never reaches quota: the 1,000 paid on the top 5,000 comes back.
        assert _total(result) == Decimal("0")
        cb = [c for c in result.commissions if c.transaction_id == "T1-CB"]
        assert [(c.base_amount, c.commission_amount) for c in cb] == [
            (Decimal("-5000"), Decimal("-1000")),
        ]
        assert "taken back on 5000" in cb[0].notes

    def test_partial_refund_only_takes_back_what_fell_below(self) -> None:
        result = self._run([
            _txn("T1", "15000", deal="D1", day=6),
            _txn("T1-CB", "-3000", deal="D1", day=20),
        ])
        assert _total(result) == Decimal("400")

    def test_order_within_the_month_does_not_matter(self) -> None:
        result = self._run([
            _txn("T1-CB", "-3000", deal="D1", day=1),
            _txn("T1", "15000", deal="D1", day=6),
        ])
        assert _total(result) == Decimal("400")

    def test_reversal_of_an_earlier_month_is_refused(self) -> None:
        with pytest.raises(ReversalError, match="was booked to P1 in 2026-05"):
            self._run([
                _txn("T1", "15000", deal="D1"),
                _txn("T1-CB", "-3000", deal="D1", period="2026-06"),
            ])

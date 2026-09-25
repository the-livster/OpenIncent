"""Mid-year plan changes: dated versions of a plan, each paying its own periods.

A plan's own fields apply from the start; each entry in `changes` applies from
its effective_from period and replaces only the fields it sets. Every period is
paid under the version in force for it, and what carries across a change -
year-to-date attainment, draw balances - carries.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import yaml

from icm_engine.engine import CalculationResult, CommissionEngine
from icm_engine.models import Commission, Draw, ManualAdjustment, Payee, Plan, Transaction
from icm_engine.plan_check import check_plan


def _plan(text: str) -> Plan:
    return Plan.model_validate(yaml.safe_load(text))


FLAT = """
plan_id: desk
name: Desk
period_type: monthly
currency: GBP
rules: [{type: flat_rate, id: fee, rate: '0.10'}]
changes:
  - effective_from: '2026-07'
    reason: Q3 rate rise approved by the MD on 20 June
    rules: [{type: flat_rate, id: fee, rate: '0.12'}]
"""


def _rep(**kw: object) -> Payee:
    return Payee(id="P1", name="Rep", quota=Decimal("10000"), plan_id="desk", **kw)


def _deal(tid: str, period: str, amount: str) -> Transaction:
    y, m = (int(x) for x in period.split("-"))
    return Transaction(id=tid, payee_id="P1", period=period, amount=Decimal(amount), close_date=date(y, m, 10))


def _totals(result: CalculationResult) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for c in result.commissions:
        out[c.period] = out.get(c.period, Decimal("0")) + c.commission_amount
    return out


class TestVersionsPayTheirOwnPeriods:
    def test_each_period_uses_the_version_in_force(self) -> None:
        result = CommissionEngine().calculate(
            _plan(FLAT), [_deal("A", "2026-06", "10000"), _deal("B", "2026-07", "10000")], [_rep()],
        )
        assert _totals(result) == {"2026-06": Decimal("1000"), "2026-07": Decimal("1200")}

    def test_the_change_and_its_reason_are_in_the_ledger(self) -> None:
        result = CommissionEngine().calculate(
            _plan(FLAT), [_deal("A", "2026-06", "10000"), _deal("B", "2026-07", "10000")], [_rep()],
        )
        events = [e for e in result.ledger if e.event_type == "plan_version"]
        assert [e.inputs["effective_from"] for e in events] == ["start", "2026-07"]
        assert "Q3 rate rise" in events[1].human_readable

    def test_a_plan_without_changes_is_untouched(self) -> None:
        plan = _plan(FLAT.split("changes:")[0])
        result = CommissionEngine().calculate(plan, [_deal("A", "2026-07", "10000")], [_rep()])
        assert _totals(result) == {"2026-07": Decimal("1000")}
        assert not [e for e in result.ledger if e.event_type == "plan_version"]

    def test_fields_not_set_by_a_change_carry_over(self) -> None:
        plan = _plan(FLAT + "payout_cap: '1100'\n")
        result = CommissionEngine().calculate(plan, [_deal("B", "2026-07", "10000")], [_rep()])
        assert _totals(result) == {"2026-07": Decimal("1100")}  # 1,200 capped by the original cap


class TestWhatCarriesAcrossAChange:
    def test_year_to_date_position_carries_into_the_new_rates(self) -> None:
        plan = _plan("""
plan_id: desk
name: Desk
period_type: monthly
currency: GBP
attainment_basis: cumulative
rules:
  - {type: tiered, id: fee, tiers: [{threshold_pct: '1.0', rate: '0.05'}, {threshold_pct: '100.0', rate: '0.10'}]}
changes:
  - effective_from: '2026-02'
    reason: new rates from February
    rules:
      - {type: tiered, id: fee, tiers: [{threshold_pct: '1.0', rate: '0.06'}, {threshold_pct: '100.0', rate: '0.12'}]}
""")
        result = CommissionEngine().calculate(
            plan, [_deal("J", "2026-01", "15000"), _deal("F", "2026-02", "10000")], [_rep()],
        )
        # January: 10,000 @ 5% + 5,000 @ 10%. February opens at 15,000 of a
        # 20,000 year-to-date quota: 5,000 @ 6% to target, 5,000 @ 12% above it.
        assert _totals(result) == {"2026-01": Decimal("1000"), "2026-02": Decimal("900")}
        assert [a.period for a in result.attainment] == ["2026-01", "2026-02"]

    def test_a_draw_balance_carries_into_the_new_version(self) -> None:
        payee = _rep(draw=Draw(amount=Decimal("1000"), recoverable=True))
        result = CommissionEngine().calculate(
            _plan(FLAT), [_deal("A", "2026-06", "4000"), _deal("B", "2026-07", "20000")], [payee],
        )
        # June: 400 earned, topped up to 1,000 (600 owed back). July: 2,400
        # under the new rate, less the 600 recovered.
        assert _totals(result) == {"2026-06": Decimal("1000"), "2026-07": Decimal("1800")}
        assert result.draw_balances["P1"] == Decimal("0")

    def test_a_late_deal_for_a_closed_month_is_priced_under_that_months_version(self) -> None:
        prior = [Commission(transaction_id="A", payee_id="P1", period="2026-05", rule_id="fee",
                            base_amount=Decimal("10000"), rate=Decimal("0.10"),
                            commission_amount=Decimal("1000"))]
        result = CommissionEngine().calculate(
            _plan(FLAT),
            [_deal("A", "2026-05", "10000"), _deal("LATE", "2026-05", "5000"), _deal("C", "2026-07", "10000")],
            [_rep()],
            locked_periods={"2026-05"}, effective_period="2026-07", prior_commissions=prior,
        )
        # The late May deal pays May's 10%, as a correction in July.
        assert _totals(result) == {"2026-07": Decimal("500") + Decimal("1200")}
        late = [c for c in result.commissions if c.transaction_id == "LATE"]
        assert late[0].commission_amount == Decimal("500")
        assert late[0].origin_period == "2026-05"

    def test_adjustments_follow_their_own_period(self) -> None:
        result = CommissionEngine().calculate(
            _plan(FLAT), [_deal("A", "2026-06", "10000"), _deal("B", "2026-07", "10000")], [_rep()],
            adjustments=[ManualAdjustment(payee_id="P1", period="2026-07", amount=Decimal("-50"), reason="x")],
        )
        assert _totals(result) == {"2026-06": Decimal("1000"), "2026-07": Decimal("1150")}

    def test_multi_plan_runs_use_versions_too(self) -> None:
        result = CommissionEngine().calculate_run(
            {"desk": _plan(FLAT)}, [_deal("A", "2026-06", "10000"), _deal("B", "2026-07", "10000")], [_rep()],
        )
        assert _totals(result) == {"2026-06": Decimal("1000"), "2026-07": Decimal("1200")}


class TestCheckingChanges:
    def test_assertions_can_test_each_version_by_period(self) -> None:
        plan = _plan(FLAT + """
assertions:
  - {name: old rate, quota: '10000', deals: ['10000'], expect_total: '1000', period: '2026-06'}
  - {name: new rate, quota: '10000', deals: ['10000'], expect_total: '1200', period: '2026-07'}
""")
        assert [r.passed for r in check_plan(plan)] == [True, True]

    @pytest.mark.parametrize("change, message", [
        ("  - {effective_from: '2026-07', rules: []}", "reason"),
        ("  - {effective_from: '2026-02', reason: r}\n  - {effective_from: '2026-01', reason: r}", "oldest first"),
    ])
    def test_bad_changes_are_refused(self, change: str, message: str) -> None:
        text = FLAT.split("changes:")[0] + "changes:\n" + change + "\n"
        with pytest.raises(ValueError, match=message):
            _plan(text)

    def test_a_quarterly_change_must_start_a_quarter(self) -> None:
        text = FLAT.replace("monthly", "quarterly").replace("'2026-07'", "'2026-08'")
        with pytest.raises(ValueError, match="first month of a period"):
            _plan(text)

    def test_each_version_must_be_a_valid_plan(self) -> None:
        text = FLAT.split("changes:")[0] + """changes:
  - effective_from: '2026-07'
    reason: add a kicker
    rules:
      - {type: flat_rate, id: kicker, rate: '0.2', on_rule: base}
"""
        with pytest.raises(ValueError, match="on_rule"):
            _plan(text)

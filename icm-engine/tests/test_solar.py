"""Solar sales: redline pay, milestone payouts, cancellations, carried deficits.

A solar rep is paid on the price sold above a redline, per watt, usually half
when the contract is signed (M1) and half at install (M2). A cancellation
arrives months later and takes back what the paid milestones paid; a month
where that exceeds new commission can carry the deficit forward instead of
paying the rep a negative amount.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from icm_engine.cli import app
from icm_engine.database import Database
from icm_engine.engine import CalculationResult, CommissionEngine
from icm_engine.exceptions import PlanDataError
from icm_engine.models import Payee, Plan, RedlineRule, Transaction
from icm_engine.run import RunContext, execute, persist


def _plan(negative_balance: str = "pay", **rule: object) -> Plan:
    rule = {"redline": Decimal("2.90"), "milestones": {"M1": Decimal("0.5"), "M2": Decimal("0.5")}, **rule}
    return Plan(
        plan_id="solar", name="Solar", currency="USD", period_type="monthly",
        negative_balance=negative_balance,
        rules=[RedlineRule(type="redline", id="redline", **rule)],
    )


def _rep(pid: str = "C1") -> Payee:
    return Payee(id=pid, name=pid, quota=Decimal("0"), plan_id="solar")


def _row(
    tid: str, period: str, milestone: str = "", *, deal: str = "S1", payee: str = "C1",
    ppw: str = "3.40", watts: str = "8000", credits: str | None = None, **meta: str,
) -> Transaction:
    year, month = (int(x) for x in period.split("-"))
    fields = {"ppw": ppw, "watts": watts, **meta}
    if milestone:
        fields["milestone"] = milestone
    return Transaction(
        id=tid, payee_id=payee, deal_id=deal, period=period, amount=Decimal("0"),
        close_date=date(year, month, 15), metadata=fields, credits=credits,
    )


def _run(plan: Plan, rows: list[Transaction], payees: list[Payee] | None = None) -> CalculationResult:
    return CommissionEngine().calculate(plan, rows, payees or [_rep()])


def _totals(result: CalculationResult, payee: str = "C1") -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for c in result.commissions:
        if c.payee_id == payee:
            out[c.period] = out.get(c.period, Decimal("0")) + c.commission_amount
    return out


class TestRedlinePay:
    def test_each_milestone_pays_its_share(self) -> None:
        # (3.40 - 2.90) x 8,000 W = 4,000: half at signing, half at install.
        result = _run(_plan(), [_row("S1-M1", "2026-07", "M1"), _row("S1-M2", "2026-08", "M2")])
        assert _totals(result) == {"2026-07": Decimal("2000"), "2026-08": Decimal("2000")}
        note = result.commissions[0].notes
        assert note.startswith("M1 pays 50%.")
        assert "8000 watts sold at 3.40 against a 2.90 redline = 4000" in note

    def test_setter_and_closer_split_the_pay(self) -> None:
        result = _run(
            _plan(), [_row("S1-M1", "2026-07", "M1", credits="C1:0.7;SET:0.3")],
            [_rep("C1"), _rep("SET")],
        )
        assert _totals(result, "C1") == {"2026-07": Decimal("1400")}
        assert _totals(result, "SET") == {"2026-07": Decimal("600")}

    def test_adders_come_off_before_the_split(self) -> None:
        plan = _plan(deductions_field="adders", milestones=None)
        result = _run(plan, [_row("S1", "2026-07", adders="500")])
        assert _totals(result) == {"2026-07": Decimal("3500")}

    def test_selling_below_redline_pays_nothing_by_default(self) -> None:
        result = _run(_plan(milestones=None), [_row("S1", "2026-07", ppw="2.80")])
        assert result.commissions == []
        assert any(e.event_type == "commission_computed" for e in result.ledger)

    def test_selling_below_redline_can_charge_the_difference(self) -> None:
        result = _run(_plan(milestones=None, floor_at_zero=False), [_row("S1", "2026-07", ppw="2.80")])
        assert _totals(result) == {"2026-07": Decimal("-800")}

    def test_each_deal_can_carry_its_own_redline(self) -> None:
        plan = _plan(redline=None, redline_field="redline", milestones=None)
        result = _run(plan, [_row("S1", "2026-07", redline="3.00")])
        assert _totals(result) == {"2026-07": Decimal("3200")}


class TestCancellation:
    def test_cancel_takes_back_the_signing_milestone_in_a_later_month(self) -> None:
        result = _run(_plan(), [
            _row("S1-M1", "2026-07", "M1", credits="C1:0.7;SET:0.3"),
            _row("S1-X", "2026-09", "cancel", credits="C1:0.7;SET:0.3"),
        ], [_rep("C1"), _rep("SET")])
        assert _totals(result, "C1") == {"2026-07": Decimal("1400"), "2026-09": Decimal("-1400")}
        assert _totals(result, "SET") == {"2026-07": Decimal("600"), "2026-09": Decimal("-600")}
        cancel = [c for c in result.commissions if c.transaction_id == "S1-X" and c.payee_id == "C1"][0]
        assert cancel.notes.startswith("Cancelled: M1 (50%) taken back.")

    def test_cancel_after_install_names_the_paid_milestones(self) -> None:
        result = _run(_plan(), [_row("S1-X", "2026-10", "cancel", paid_milestones="M1;M2")])
        assert _totals(result) == {"2026-10": Decimal("-4000")}

    def test_plan_can_set_what_a_cancellation_takes_back(self) -> None:
        plan = _plan(clawback_on_cancel=["M1", "M2"])
        assert _totals(_run(plan, [_row("S1-X", "2026-10", "cancel")])) == {"2026-10": Decimal("-4000")}

    def test_without_milestones_a_cancellation_takes_back_the_whole_deal(self) -> None:
        result = _run(_plan(milestones=None), [_row("S1", "2026-07"), _row("S1-X", "2026-09", "cancel")])
        assert _totals(result) == {"2026-07": Decimal("4000"), "2026-09": Decimal("-4000")}


class TestBadRows:
    def test_missing_price_stops_the_run(self) -> None:
        with pytest.raises(PlanDataError, match="has no usable ppw"):
            _run(_plan(), [_row("S1-M1", "2026-07", "M1", ppw="")])

    def test_unknown_milestone_stops_the_run(self) -> None:
        with pytest.raises(PlanDataError, match="has milestone 'PTO'"):
            _run(_plan(), [_row("S1-P", "2026-07", "PTO")])

    def test_milestone_rows_without_milestones_on_the_rule_stop_the_run(self) -> None:
        # Otherwise every milestone row would pay the whole deal again.
        with pytest.raises(PlanDataError, match="defines no milestones"):
            _run(_plan(milestones=None), [_row("S1-M1", "2026-07", "M1")])

    def test_cancel_naming_an_unknown_milestone_stops_the_run(self) -> None:
        with pytest.raises(PlanDataError, match="does not define"):
            _run(_plan(), [_row("S1-X", "2026-09", "cancel", paid_milestones="M3")])

    def test_rule_needs_exactly_one_redline_source(self) -> None:
        with pytest.raises(ValueError, match="exactly one of"):
            RedlineRule(type="redline", id="r", redline=Decimal("2.9"), redline_field="redline")
        with pytest.raises(ValueError, match="exactly one of"):
            RedlineRule(type="redline", id="r")

    def test_milestone_shares_must_add_up_to_one(self) -> None:
        with pytest.raises(ValueError, match="add up to 1"):
            RedlineRule(type="redline", id="r", redline=Decimal("2.9"),
                        milestones={"M1": Decimal("0.5"), "M2": Decimal("0.4")})


class TestNegativeBalance:
    # September: a 1,400 clawback against 900 of new commission.
    ROWS = [
        _row("S1-M1", "2026-07", "M1", credits="C1:0.7;SET:0.3"),
        _row("S2-M1", "2026-09", "M1", deal="S2", ppw="3.20", watts="6000"),
        _row("S1-X", "2026-09", "cancel", credits="C1:0.7;SET:0.3"),
        _row("S2-M2", "2026-10", "M2", deal="S2", ppw="3.20", watts="6000"),
    ]
    PAYEES = [_rep("C1"), _rep("SET")]

    def test_default_pays_the_negative_month(self) -> None:
        result = _run(_plan(), self.ROWS, self.PAYEES)
        assert _totals(result)["2026-09"] == Decimal("-500")

    def test_carry_forward_pays_zero_then_recovers(self) -> None:
        result = _run(_plan("carry_forward"), self.ROWS, self.PAYEES)
        assert _totals(result) == {
            "2026-07": Decimal("1400"),
            "2026-09": Decimal("0"),    # 900 - 1,400, with 500 carried forward
            "2026-10": Decimal("400"),  # 900, less the 500 recovered
        }
        assert result.draw_balances["C1"] == Decimal("0")
        # The setter had nothing to net against: 600 carried, nothing recovered yet.
        assert _totals(result, "SET")["2026-09"] == Decimal("0")
        assert result.draw_balances["SET"] == Decimal("600")
        rules = {(c.period, c.rule_id) for c in result.commissions if c.payee_id == "C1"}
        assert ("2026-09", "balance") in rules and ("2026-10", "balance") in rules

    def test_deficit_carries_across_separate_runs(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "t.db"))
        db.init()

        def run(rows: list[Transaction]) -> CalculationResult:
            ctx = RunContext(plan_library={"solar": _plan("carry_forward")}, transactions=rows,
                             payees=self.PAYEES, db=db, single_plan_mode=True)
            ctx.resolve()
            result = execute(ctx)
            persist(ctx, result)
            return result

        run(self.ROWS[:1])
        september = run(self.ROWS[1:3])
        assert _totals(september) == {"2026-09": Decimal("0")}
        october = run(self.ROWS[3:])
        assert _totals(october) == {"2026-10": Decimal("400")}


def test_csv_columns_reach_the_rule(tmp_path: Path) -> None:
    plan = tmp_path / "plan.yaml"
    plan.write_text(
        "plan_id: solar\nname: Solar\ncurrency: USD\nperiod_type: monthly\n"
        "rules:\n  - type: redline\n    id: redline\n    redline: '2.90'\n"
        "    milestones: {M1: '0.5', M2: '0.5'}\n"
    )
    payees = tmp_path / "payees.csv"
    payees.write_text("id,name,quota,plan_id\nC1,Casey,0,solar\n")
    deals = tmp_path / "deals.csv"
    deals.write_text(
        "id,payee_id,deal_id,period,amount,PPW,Watts,Milestone\n"
        "S1-M1,C1,S1,2026-07,27200,3.40,8000,M1\n"
    )
    out = tmp_path / "out"
    result = CliRunner().invoke(app, [
        "--plan", str(plan), "--transactions", str(deals), "--payees", str(payees),
        "--output", str(out), "--csv", "--no-db",
    ])
    assert result.exit_code == 0, result.output
    assert "C1,2026-07,2000" in (out / "summary.csv").read_text()


def test_solar_template_proves_itself() -> None:
    from icm_engine.loader import load_plan
    from icm_engine.plan_check import all_passed, check_plan

    results = check_plan(load_plan(Path("examples/templates/solar_redline.yaml")))
    assert len(results) == 4
    assert all_passed(results), [(r.name, r.actual, r.detail) for r in results if not r.passed]

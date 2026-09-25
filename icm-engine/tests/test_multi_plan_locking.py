"""Locking and re-running a month that more than one plan paid.

Regression: persist() saved a multi-plan month as a single calculation,
labelled with the plan of its first line. Locking the month locked that one
plan; the others had nothing to lock. Re-running the locked month then
reversed every line of the other plans' payees as a true-up, while their
supposedly locked month was quietly recalculated. A desk manager on her own
override plan was shown -760 for June where -140 was owed.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from icm_engine.database import Database
from icm_engine.engine import CalculationResult
from icm_engine.models import FlatRateRule, Payee, Plan, Tier, TieredRule, Transaction
from icm_engine.run import RunContext, execute, persist

PLANS = {
    "perm": Plan(
        plan_id="perm", name="Perm desk", currency="GBP", period_type="monthly",
        rules=[TieredRule(type="tiered", id="fee", tiers=[
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.10")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.15")),
        ])],
    ),
    # The 2% override rate lives on each report; this pays it straight through.
    "mgmt": Plan(
        plan_id="mgmt", name="Desk manager", currency="GBP", period_type="monthly",
        rules=[FlatRateRule(type="flat_rate", id="override", rate=Decimal("1.0"))],
    ),
}

PAYEES = [
    Payee(id="P1", name="Priya", quota=Decimal("20000"), plan_id="perm",
          manager_id="M", manager_override=Decimal("0.02")),
    Payee(id="P2", name="Tom", quota=Decimal("20000"), plan_id="perm",
          manager_id="M", manager_override=Decimal("0.02")),
    Payee(id="M", name="Dana", quota=Decimal("0"), plan_id="mgmt"),
]


def _deal(tid: str, payee: str, amount: str, period: str, day: int, credits: str | None = None) -> Transaction:
    year, month = (int(x) for x in period.split("-"))
    return Transaction(
        id=tid, payee_id=payee, period=period, amount=Decimal(amount),
        close_date=date(year, month, day), credits=credits,
    )


MAY = [
    _deal("PL-1", "P1", "18000", "2026-05", 6),
    _deal("PL-2", "P1", "12000", "2026-05", 12, credits="P1:0.6;P2:0.4"),
    _deal("PL-3", "P2", "13000", "2026-05", 9),
]


def _run(
    db: Database, txns: list[Transaction], effective_period: str | None = None,
) -> tuple[CalculationResult, dict[str, str]]:
    ctx = RunContext(
        plan_library=PLANS, transactions=txns, payees=PAYEES, db=db,
        effective_period=effective_period,
    )
    ctx.resolve()
    result = execute(ctx)
    return result, persist(ctx, result)


def _totals(result: CalculationResult, period: str) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for c in result.commissions:
        if c.period == period:
            out[c.payee_id] = out.get(c.payee_id, Decimal("0")) + c.commission_amount
    return out


def _db(tmp_path: Path) -> Database:
    db = Database(str(tmp_path / "t.db"))
    db.init()
    return db


class TestMultiPlanLocking:
    def test_each_plans_month_is_saved_as_its_own_calculation(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        _, ids = _run(db, MAY)
        assert set(ids) == {"perm:2026-05", "mgmt:2026-05"}
        payees_in = {
            key: {line["payee_id"] for line in db.get_commission_lines(cid)}
            for key, cid in ids.items()
        }
        assert payees_in == {"perm:2026-05": {"P1", "P2"}, "mgmt:2026-05": {"M"}}
        assert {(c["plan_id"], c["period"]) for c in db.list_calculations()} == {
            ("perm", "2026-05"), ("mgmt", "2026-05"),
        }

    def test_fall_off_after_the_lock_claws_back_from_everyone_once(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        may, ids = _run(db, MAY)
        assert _totals(may, "2026-05") == {
            "P1": Decimal("2780"), "P2": Decimal("1780"), "M": Decimal("860"),
        }
        for key, calculation_id in ids.items():
            plan_id, period = key.split(":")
            assert db.lock_period(plan_id, period, calculation_id)

        # June: PL-2 fell off inside its guarantee. May is re-supplied without
        # it, and the difference is paid back in June.
        june_file = [t for t in MAY if t.id != "PL-2"] + [_deal("PL-4", "P1", "5000", "2026-06", 3)]
        june, _ = _run(db, june_file, effective_period="2026-06")

        assert _totals(june, "2026-06") == {
            "P1": Decimal("-480"),  # 500 on PL-4, less the 980 paid on her share
            "P2": Decimal("-480"),  # the 480 paid on his share
            "M": Decimal("-140"),   # 100 override on PL-4, less 240 on PL-2
        }
        # The locked May stays as paid: nobody's May is recalculated.
        assert _totals(june, "2026-05") == {}

    def test_single_plan_run_is_saved_under_the_plan_it_ran(self, tmp_path: Path) -> None:
        # A roster with no plan column must not file the run under plan "".
        db = _db(tmp_path)
        ctx = RunContext(
            plan_library={"perm": PLANS["perm"]},
            transactions=[_deal("PL-1", "P1", "18000", "2026-05", 6)],
            payees=[Payee(id="P1", name="Priya", quota=Decimal("20000"))],
            db=db, single_plan_mode=True,
        )
        ctx.resolve()
        ids = persist(ctx, execute(ctx))
        assert list(ids) == ["2026-05"]
        assert db.get_calculation(ids["2026-05"])["plan_id"] == "perm"

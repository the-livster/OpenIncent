import os
import tempfile
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

import icm_engine.api as api_module
from icm_engine.api import app

_db_dir = tempfile.mkdtemp(prefix="icm_test_db_")
_db_path = os.path.join(_db_dir, "test.db")
os.environ["ICM_DB_PATH"] = _db_path

client = TestClient(app)

V = "/v1"


def _reset() -> None:
    api_module._db = None
    api_module._db_path = None
    if os.path.exists(_db_path):
        os.remove(_db_path)


class TestPeriodLocks:
    def test_lock_and_check(self) -> None:
        _reset()
        # Save a plan
        resp = client.post(f"{V}/plans", json={"name": "P", "yaml_content": "rules: []"})
        plan_id = resp.json()["id"]
        # Run a calculation (use the API calculate endpoint)
        from icm_engine.engine import CommissionEngine
        from icm_engine.models import FlatRateRule, Payee, Plan, Transaction
        plan = Plan(plan_id=plan_id, name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [Payee(id="P1", name="A", quota=Decimal("0"), plan_id=plan_id, effective_from=date(2026, 1, 1))]
        txns = [Transaction(
            id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
            close_date=date(2026, 1, 10),
        )]
        result = CommissionEngine().calculate(plan, txns, payees)
        # Get the calculation ID from the API (we used the engine directly, so get from DB)
        db = api_module._get_db()
        calc_id = db.record_calculation(plan_id, period="2026-01")
        db.save_commission_lines(calc_id, result.commissions)

        # Lock
        resp = client.post(f"{V}/periods/{plan_id}/2026-01/lock?calculation_id={calc_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "locked"

        # Check status
        resp = client.get(f"{V}/periods/{plan_id}/2026-01/status")
        assert resp.json()["locked"] is True
        assert resp.json()["official_calculation_id"] == calc_id

    def test_lock_twice_fails(self) -> None:
        _reset()
        resp = client.post(f"{V}/plans", json={"name": "P", "yaml_content": "rules: []"})
        plan_id = resp.json()["id"]
        db = api_module._get_db()
        calc_id = db.record_calculation(plan_id, period="2026-01")
        client.post(f"{V}/periods/{plan_id}/2026-01/lock?calculation_id={calc_id}")
        resp = client.post(f"{V}/periods/{plan_id}/2026-01/lock?calculation_id={calc_id}")
        assert resp.status_code == 409

    def test_unlock_then_relock(self) -> None:
        _reset()
        resp = client.post(f"{V}/plans", json={"name": "P", "yaml_content": "rules: []"})
        plan_id = resp.json()["id"]
        db = api_module._get_db()
        calc_id = db.record_calculation(plan_id, period="2026-01")
        client.post(f"{V}/periods/{plan_id}/2026-01/lock?calculation_id={calc_id}")
        resp = client.delete(f"{V}/periods/{plan_id}/2026-01/lock")
        assert resp.status_code == 200
        resp = client.post(f"{V}/periods/{plan_id}/2026-01/lock?calculation_id={calc_id}")
        assert resp.status_code == 200

    def test_rerun_after_lock_new_version(self) -> None:
        """Rerunning after lock creates a new draft version without touching lock."""
        _reset()
        db = api_module._get_db()
        resp = client.post(f"{V}/plans", json={"name": "P", "yaml_content": "rules: []"})
        plan_id = resp.json()["id"]

        calc1 = db.record_calculation(plan_id, period="2026-01")
        client.post(f"{V}/periods/{plan_id}/2026-01/lock?calculation_id={calc1}")

        # Rerun
        db.record_calculation(plan_id, period="2026-01")

        calcs = db.list_calculations(plan_id=plan_id, limit=10)
        versions = sorted([c["version"] for c in calcs if c["period"] == "2026-01"])
        assert versions == [1, 2]  # first run = v1, rerun = v2

        # Lock still holds
        assert db.is_locked(plan_id, "2026-01") is True
        official = db.get_official_calculation(plan_id, "2026-01")
        assert official["id"] == calc1

    def test_locked_true_up_flat_new_deal(self) -> None:
        """March locked with T1=$500. Recalc [T1, T2($5k)] → delta=$250 to June."""
        from icm_engine.engine import CommissionEngine
        from icm_engine.models import Commission, FlatRateRule, Payee, Plan, Transaction

        plan = Plan(plan_id="TU1", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [Payee(id="P1", name="A", quota=Decimal("0"), plan_id="TU1", effective_from=date(2026, 1, 1))]

        # Prior: locked March — just T1
        prior = [Commission(
            transaction_id="T1", payee_id="P1", period="2026-03", rule_id="R1",
            base_amount=Decimal("10000"), rate=Decimal("0.05"),
            commission_amount=Decimal("500"), notes="",
        )]

        engine = CommissionEngine()
        txns = [
            Transaction(id="T1", payee_id="P1", period="2026-03",
                        amount=Decimal("10000"), close_date=date(2026, 3, 10)),
            Transaction(id="T2", payee_id="P1", period="2026-03",
                        amount=Decimal("5000"), close_date=date(2026, 3, 20)),
        ]
        result = engine.calculate(plan, txns, payees,
                                  locked_periods={"2026-03"}, effective_period="2026-06",
                                  prior_commissions=prior)

        # Only delta commissions emitted — no full-amount March lines
        june_lines = [c for c in result.commissions if c.period == "2026-06"]
        march_lines = [c for c in result.commissions if c.period == "2026-03"]
        assert len(march_lines) == 0
        assert len(june_lines) == 1
        assert june_lines[0].commission_amount == Decimal("250")  # T2 only, NOT $750
        assert june_lines[0].origin_period == "2026-03"

    def test_locked_true_up_tiered_attainment_shift(self) -> None:
        """Quota $10k, tiers 0-100%@5%, >100%@10%. Prior[T1 $8k]=$400. Recalc[T1+T2 $4k] → delta=$300."""
        from icm_engine.engine import CommissionEngine
        from icm_engine.models import Commission, Payee, Plan, Tier, TieredRule, Transaction

        plan = Plan(plan_id="TU2", name="Test", period_type="monthly", currency="USD",
                    rules=[TieredRule(type="tiered", id="R1", tiers=[
                        Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
                        Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
                    ])])
        payees = [Payee(id="P1", name="A", quota=Decimal("10000"), plan_id="TU2", effective_from=date(2026, 1, 1))]

        # Prior: locked March — T1 $8k, attainment 80%, all at 5% → $400
        prior = [Commission(
            transaction_id="T1", payee_id="P1", period="2026-03", rule_id="R1",
            base_amount=Decimal("8000"), rate=Decimal("0.05"),
            commission_amount=Decimal("400"), notes="",
        )]

        engine = CommissionEngine()
        txns = [
            Transaction(id="T1", payee_id="P1", period="2026-03", amount=Decimal("8000"), close_date=date(2026, 3, 10)),
            Transaction(id="T2", payee_id="P1", period="2026-03", amount=Decimal("4000"), close_date=date(2026, 3, 20)),
        ]
        result = engine.calculate(plan, txns, payees,
                                  locked_periods={"2026-03"}, effective_period="2026-06",
                                  prior_commissions=prior)

        march_lines = [c for c in result.commissions if c.period == "2026-03"]
        june_lines = [c for c in result.commissions if c.period == "2026-06"]
        assert len(march_lines) == 0
        june_total = sum(c.commission_amount for c in june_lines)
        # Full recompute = 10000@5% + 2000@10% = 700. Prior = 400. Delta = 300.
        # NOT 700 (double-pay) and not 200 (wrongly scoped).
        assert june_total == Decimal("300")
        for c in june_lines:
            assert c.origin_period == "2026-03"

    def test_locked_true_up_clawback(self) -> None:
        """Prior has T1=$500 locked. Current omits T1. Assert -$500 true-up."""
        from icm_engine.engine import CommissionEngine
        from icm_engine.models import Commission, FlatRateRule, Payee, Plan, Transaction

        plan = Plan(plan_id="TU3", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [Payee(id="P1", name="A", quota=Decimal("0"), plan_id="TU3", effective_from=date(2026, 1, 1))]

        prior = [Commission(
            transaction_id="T1", payee_id="P1", period="2026-03", rule_id="R1",
            base_amount=Decimal("10000"), rate=Decimal("0.05"),
            commission_amount=Decimal("500"), notes="",
        )]

        engine = CommissionEngine()
        txns: list[Transaction] = []  # T1 removed
        result = engine.calculate(plan, txns, payees,
                                  locked_periods={"2026-03"}, effective_period="2026-06",
                                  prior_commissions=prior)

        june_lines = [c for c in result.commissions if c.period == "2026-06"]
        assert len(june_lines) == 1
        assert june_lines[0].commission_amount == Decimal("-500")
        assert june_lines[0].origin_period == "2026-03"

    def test_locked_true_up_noop(self) -> None:
        """Identical prior and current → no true-up lines emitted."""
        from icm_engine.engine import CommissionEngine
        from icm_engine.models import Commission, FlatRateRule, Payee, Plan, Transaction

        plan = Plan(plan_id="TU4", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [Payee(id="P1", name="A", quota=Decimal("0"), plan_id="TU4", effective_from=date(2026, 1, 1))]

        prior = [Commission(
            transaction_id="T1", payee_id="P1", period="2026-03", rule_id="R1",
            base_amount=Decimal("10000"), rate=Decimal("0.05"),
            commission_amount=Decimal("500"), notes="",
        )]

        engine = CommissionEngine()
        txns = [
            Transaction(id="T1", payee_id="P1", period="2026-03",
                        amount=Decimal("10000"), close_date=date(2026, 3, 10)),
        ]
        result = engine.calculate(plan, txns, payees,
                                  locked_periods={"2026-03"}, effective_period="2026-06",
                                  prior_commissions=prior)

        june_lines = [c for c in result.commissions if c.period == "2026-06"]
        assert len(june_lines) == 0  # delta = 0, nothing to true up

    def test_locked_true_up_mixed_periods_no_double_pay(self) -> None:
        """Regression: locked March (T1=$500) + non-locked June (T2=$1000) in one call.
        Prior has March only. Non-locked lines must NOT be double-counted. June == $1000 (T2 only)."""
        from icm_engine.engine import CommissionEngine
        from icm_engine.models import Commission, FlatRateRule, Payee, Plan, Transaction

        plan = Plan(plan_id="TU5", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [Payee(id="P1", name="A", quota=Decimal("0"), plan_id="TU5", effective_from=date(2026, 1, 1))]

        prior = [Commission(
            transaction_id="T1", payee_id="P1", period="2026-03", rule_id="R1",
            base_amount=Decimal("10000"), rate=Decimal("0.05"),
            commission_amount=Decimal("500"), notes="",
        )]

        engine = CommissionEngine()
        txns = [
            Transaction(id="T1", payee_id="P1", period="2026-03",
                        amount=Decimal("10000"), close_date=date(2026, 3, 10)),
            Transaction(id="T2", payee_id="P1", period="2026-06",
                        amount=Decimal("20000"), close_date=date(2026, 6, 15)),
        ]
        result = engine.calculate(plan, txns, payees,
                                  locked_periods={"2026-03"}, effective_period="2026-06",
                                  prior_commissions=prior)

        june_lines = [c for c in result.commissions if c.period == "2026-06"]
        march_lines = [c for c in result.commissions if c.period == "2026-03"]
        # No March lines — locked period fully redirected
        assert len(march_lines) == 0
        # Exactly one June line: T2 at $1000 (5% of $20k)
        assert len(june_lines) == 1
        assert june_lines[0].transaction_id == "T2"
        june_total = sum(c.commission_amount for c in june_lines)
        assert june_total == Decimal("1000")

    def test_locked_recalculate_remaps_origin_period(self) -> None:
        """Without prior_commissions, locked-period lines get full-amount remap (new-period scenario)."""
        _reset()
        from icm_engine.engine import CommissionEngine
        from icm_engine.models import FlatRateRule, Payee, Plan, Transaction

        plan = Plan(plan_id="P1", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [Payee(id="P1", name="Alice", quota=Decimal("0"), plan_id="P1", effective_from=date(2026, 1, 1))]
        txns = [
            Transaction(id="T1", payee_id="P1", period="2026-03",
                        amount=Decimal("10000"), close_date=date(2026, 3, 10)),
            Transaction(id="T2", payee_id="P1", period="2026-03",
                        amount=Decimal("5000"), close_date=date(2026, 3, 20)),
        ]
        engine = CommissionEngine()
        result = engine.calculate(plan, txns, payees,
                                  locked_periods={"2026-03"}, effective_period="2026-06")

        # Without prior, all locked-period commissions go to effective period at full amount
        for c in result.commissions:
            assert c.period == "2026-06"
            assert c.origin_period == "2026-03"
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("750")  # full amount, no diff

    def test_locked_recalculate_blocks_when_disallowed(self) -> None:
        """When allow_recalculate_locked=False, locked periods reject the request."""
        _reset()
        plan_id = "P-locked-test"
        resp = client.post(f"{V}/plans", json={
            "name": "P", "yaml_content": "rules: []", "plan_id": plan_id,
        })
        db = api_module._get_db()

        # Lock March
        calc1 = db.record_calculation(plan_id, period="2026-03")
        client.post(f"{V}/periods/{plan_id}/2026-03/lock?calculation_id={calc1}")

        # Try to calculate with a March transaction and allow_recalculate_locked=false
        plan_yaml = (
            f"plan_id: {plan_id}\n"
            "name: Test\n"
            "period_type: monthly\n"
            "currency: USD\n"
            "rules:\n"
            "  - type: flat_rate\n"
            "    id: R1\n"
            "    rate: 0.05\n"
        )
        txn_csv = "id,payee_id,period,amount,close_date\nT1,P1,2026-03,10000,2026-03-10\n"
        payee_csv = f"id,name,quota,plan_id,effective_from\nP1,Alice,0,{plan_id},2026-01-01\n"

        resp = client.post(
            f"{V}/calculate?allow_recalculate_locked=false",
            files={
                "plan": ("plan.yaml", plan_yaml.encode(), "application/x-yaml"),
                "transactions": ("transactions.csv", txn_csv.encode(), "text/csv"),
                "payees": ("payees.csv", payee_csv.encode(), "text/csv"),
            },
        )
        assert resp.status_code == 409
        assert "locked_periods" in resp.json()["detail"]

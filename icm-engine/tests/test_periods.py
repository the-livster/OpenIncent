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

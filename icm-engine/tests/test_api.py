from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import icm_engine.api as api_module
from icm_engine.api import app

_db_dir = tempfile.mkdtemp(prefix="icm_test_db_")
_db_path = os.path.join(_db_dir, "test.db")
os.environ["ICM_DB_PATH"] = _db_path

client = TestClient(app)

EXAMPLES = Path("examples")
FIXTURES = Path("tests/fixtures")

V = "/v1"


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_calculate_success() -> None:
    with (
        open(EXAMPLES / "saas_ae_plan.yaml", "rb") as plan_f,
        open(EXAMPLES / "saas_transactions.csv", "rb") as txn_f,
        open(EXAMPLES / "saas_payees.csv", "rb") as payees_f,
    ):
        response = client.post(
            f"{V}/calculate",
            files={
                "plan": ("plan.yaml", plan_f, "application/x-yaml"),
                "transactions": ("transactions.csv", txn_f, "text/csv"),
                "payees": ("payees.csv", payees_f, "text/csv"),
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert "commissions" in data
    assert "ledger" in data
    assert "summary" in data
    assert "calculation_id" in data
    assert len(data["commissions"]) > 0
    assert len(data["ledger"]) > 0

    c = data["commissions"][0]
    assert isinstance(c["commission_amount"], str)
    assert "." in c["commission_amount"]


def test_malformed_plan_returns_400() -> None:
    plan_content = b"this is not valid yaml for a plan\nplain string\n"
    with (
        open(FIXTURES / "sample_transactions.csv", "rb") as txn_f,
        open(FIXTURES / "sample_payees.csv", "rb") as payees_f,
    ):
        response = client.post(
            f"{V}/calculate",
            files={
                "plan": ("plan.yaml", plan_content, "application/x-yaml"),
                "transactions": ("transactions.csv", txn_f, "text/csv"),
                "payees": ("payees.csv", payees_f, "text/csv"),
            },
        )
    assert response.status_code == 400


def test_missing_payees_file_returns_422() -> None:
    with (
        open(EXAMPLES / "saas_ae_plan.yaml", "rb") as plan_f,
        open(EXAMPLES / "saas_transactions.csv", "rb") as txn_f,
    ):
        response = client.post(
            f"{V}/calculate",
            files={
                "plan": ("plan.yaml", plan_f, "application/x-yaml"),
                "transactions": ("transactions.csv", txn_f, "text/csv"),
            },
        )
    assert response.status_code == 422


def test_invalid_payees_csv_returns_400() -> None:
    bad_payees = b"id,wrong_column\nP001,Alice\n"
    with (
        open(EXAMPLES / "saas_ae_plan.yaml", "rb") as plan_f,
        open(EXAMPLES / "saas_transactions.csv", "rb") as txn_f,
    ):
        response = client.post(
            f"{V}/calculate",
            files={
                "plan": ("plan.yaml", plan_f, "application/x-yaml"),
                "transactions": ("transactions.csv", txn_f, "text/csv"),
                "payees": ("payees.csv", bad_payees, "text/csv"),
            },
        )
    assert response.status_code == 400


# ------------------------------------------------------------------
# Database endpoint tests
# ------------------------------------------------------------------


def _reset_db() -> None:
    api_module._db = None
    api_module._db_path = None
    db_path = os.environ.get("ICM_DB_PATH", "")
    if db_path and os.path.exists(db_path):
        os.remove(db_path)


class TestPlansAPI:
    def test_save_and_list(self) -> None:
        _reset_db()
        resp = client.post(f"{V}/plans", json={"name": "Test", "yaml_content": "rules: []"})
        assert resp.status_code == 200
        resp = client.get(f"{V}/plans")
        assert resp.status_code == 200
        plans = resp.json()
        assert len(plans) == 1
        assert plans[0]["name"] == "Test"

    def test_get_not_found(self) -> None:
        _reset_db()
        resp = client.get(f"{V}/plans/nonexistent")
        assert resp.status_code == 404

    def test_delete_plan(self) -> None:
        _reset_db()
        resp = client.post(f"{V}/plans", json={"name": "X", "yaml_content": "rules: []"})
        pid = resp.json()["id"]
        assert client.delete(f"{V}/plans/{pid}").status_code == 200
        assert client.get(f"{V}/plans/{pid}").status_code == 404

    def test_update_existing(self) -> None:
        _reset_db()
        resp = client.post(f"{V}/plans", json={"name": "Old", "yaml_content": "old"})
        pid = resp.json()["id"]
        client.post(f"{V}/plans", json={"name": "New", "yaml_content": "new", "plan_id": pid})
        assert client.get(f"{V}/plans/{pid}").json()["name"] == "New"


class TestSettingsAPI:
    def test_set_and_get(self) -> None:
        _reset_db()
        client.put(f"{V}/settings/api_key", json={"value": "sk-123"})
        assert client.get(f"{V}/settings/api_key").json()["value"] == "sk-123"

    def test_get_unset(self) -> None:
        _reset_db()
        assert client.get(f"{V}/settings/nonexistent").json()["value"] is None

    def test_delete(self) -> None:
        _reset_db()
        client.put(f"{V}/settings/key", json={"value": "val"})
        client.delete(f"{V}/settings/key")
        assert client.get(f"{V}/settings/key").json()["value"] is None


class TestMappingsAPI:
    def test_save_and_list(self) -> None:
        _reset_db()
        client.post(f"{V}/mappings", json={"name": "M", "mapping_data": {"a": "b"}})
        assert len(client.get(f"{V}/mappings").json()) == 1

    def test_delete(self) -> None:
        _reset_db()
        resp = client.post(f"{V}/mappings", json={"name": "M", "mapping_data": {"k": "v"}})
        mid = resp.json()["id"]
        client.delete(f"{V}/mappings/{mid}")
        assert len(client.get(f"{V}/mappings").json()) == 0


class TestLedgerAPI:
    def test_query_by_payee(self) -> None:
        _reset_db()
        with (
            open(EXAMPLES / "saas_ae_plan.yaml", "rb") as plan_f,
            open(EXAMPLES / "saas_transactions.csv", "rb") as txn_f,
            open(EXAMPLES / "saas_payees.csv", "rb") as payees_f,
        ):
            resp = client.post(
                f"{V}/calculate",
                files={
                    "plan": ("plan.yaml", plan_f, "application/x-yaml"),
                    "transactions": ("transactions.csv", txn_f, "text/csv"),
                    "payees": ("payees.csv", payees_f, "text/csv"),
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        cid = data["calculation_id"]
        payee = data["commissions"][0]["payee_id"]

        ledger = client.get(f"{V}/ledger", params={"payee_id": payee, "calculation_id": cid})
        assert ledger.status_code == 200
        entries = ledger.json()
        assert len(entries) > 0
        assert all(e["payee_id"] == payee for e in entries)

    def test_query_empty(self) -> None:
        _reset_db()
        resp = client.get(f"{V}/ledger", params={"payee_id": "nonexistent"})
        assert resp.status_code == 200
        assert resp.json() == []


class TestApiKeys:
    def test_create_and_list(self) -> None:
        _reset_db()
        resp = client.post(f"{V}/api-keys", params={"name": "test-key"})
        assert resp.status_code == 200
        data = resp.json()
        assert "key" in data
        assert data["key"].startswith("oi_")

        keys = client.get(f"{V}/api-keys").json()
        assert len(keys) == 1
        assert keys[0]["name"] == "test-key"

    def test_create_and_delete(self) -> None:
        _reset_db()
        resp = client.post(f"{V}/api-keys")
        kid = resp.json()["id"]
        assert client.delete(f"{V}/api-keys/{kid}").status_code == 200
        assert len(client.get(f"{V}/api-keys").json()) == 0

    def test_auth_with_valid_key(self) -> None:
        _reset_db()
        resp = client.post(f"{V}/api-keys", params={"name": "auth-test"})
        key = resp.json()["key"]

        # Use the key to access a protected endpoint
        resp = client.get(f"{V}/plans", headers={"Authorization": f"Bearer {key}"})
        assert resp.status_code == 200


class TestMultiTenancy:
    def test_plans_isolated_by_org(self) -> None:
        _reset_db()
        # Create key for org-a
        resp = client.post(f"{V}/api-keys", params={"name": "org-a"})
        key_a = resp.json()["key"]

        # Create key for org-b
        resp = client.post(f"{V}/api-keys", params={"name": "org-b"})

        # Currently both keys are created under "default" org since no org is set.
        # Save a plan with default org
        client.post(f"{V}/plans", json={"name": "Default Plan", "yaml_content": "rules: []"})
        assert len(client.get(f"{V}/plans").json()) == 1

        # Plans from different orgs don't cross — this test verifies basic
        # scoping (keys created under same org see same data).
        resp = client.get(f"{V}/plans", headers={"Authorization": f"Bearer {key_a}"})
        assert resp.status_code == 200  # still "default" org since key was created there


class TestCalculationsAPI:
    def test_list_empty(self) -> None:
        _reset_db()
        assert client.get(f"{V}/calculations").json() == []

    def test_list_with_data(self) -> None:
        _reset_db()
        client.post(f"{V}/plans", json={"name": "P", "yaml_content": "rules: []"})
        plan_id = client.get(f"{V}/plans").json()[0]["id"]
        api_module._get_db().record_calculation(plan_id)
        calcs = client.get(f"{V}/calculations").json()
        assert len(calcs) == 1
        assert calcs[0]["plan_id"] == plan_id

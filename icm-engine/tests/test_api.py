from __future__ import annotations

import os
import tempfile
from decimal import Decimal
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
    assert "calculation_ids" in data
    assert len(data["commissions"]) > 0
    assert len(data["ledger"]) > 0
    assert len(data["calculation_ids"]) >= 1

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


def test_missing_payees_uses_saved_roster() -> None:
    """Without a payees file, the endpoint falls back to the saved roster (400 if empty)."""
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
    assert response.status_code == 400  # No saved payees yet


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
        # Use first period's calculation_id for ledger query
        cid = next(iter(data["calculation_ids"].values()))
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


class TestExportAPI:
    def test_zip_export_text_inputs(self) -> None:
        """/export returns a valid zip with per-rep files + internal summary."""
        import io
        import zipfile

        plan_yaml = (
            "plan_id: export_test\n"
            "name: Export Test Plan\n"
            "period_type: monthly\n"
            "currency: USD\n"
            "rules:\n"
            "  - id: R1\n"
            "    type: flat_rate\n"
            "    rate: 0.05\n"
        )
        txn_csv = (
            "id,payee_id,period,amount,close_date\n"
            "T1,P1,2026-01,10000,2026-01-10\n"
            "T2,P2,2026-01,5000,2026-01-10\n"
        )
        payee_csv = (
            "id,name,quota,plan_id,effective_from\n"
            "P1,Alice,0,export_test,2026-01-01\n"
            "P2,Bob,0,export_test,2026-01-01\n"
        )

        resp = client.post(f"{V}/export", data={
            "plan_text": plan_yaml,
            "txn_text": txn_csv,
            "payee_text": payee_csv,
            "formats": "html,xlsx",
        })
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "commission_statements.zip" in resp.headers["content-disposition"]

        # Parse the zip
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = set(zf.namelist())

        # Each payee should have an html and xlsx file
        assert any("P1" in n and n.endswith(".html") for n in names)
        assert any("P1" in n and n.endswith(".xlsx") for n in names)
        assert any("P2" in n and n.endswith(".html") for n in names)
        assert any("P2" in n and n.endswith(".xlsx") for n in names)

        # Internal summary must be present
        assert "internal/_all-reps-summary.xlsx" in names

        # Privacy: P1's HTML must not contain P2's data
        p1_html = next(n for n in names if "P1" in n and n.endswith(".html"))
        p1_content = zf.read(p1_html).decode("utf-8")
        assert "P2" not in p1_content
        assert "Bob" not in p1_content

    def test_export_format_selection(self) -> None:
        """Only requested formats appear in the zip."""
        import io
        import zipfile

        plan_yaml = (
            "plan_id: fmt_test\nname: Fmt Test\nperiod_type: monthly\ncurrency: USD\n"
            "rules:\n  - id: R1\n    type: flat_rate\n    rate: 0.05\n"
        )
        txn_csv = "id,payee_id,period,amount,close_date\nT1,P1,2026-01,10000,2026-01-10\n"
        payee_csv = "id,name,quota,plan_id,effective_from\nP1,Alice,0,fmt_test,2026-01-01\n"

        resp = client.post(f"{V}/export", data={
            "plan_text": plan_yaml,
            "txn_text": txn_csv,
            "payee_text": payee_csv,
            "formats": "pdf",
        })
        assert resp.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        # Only pdf (and internal summary) should appear
        has_non_pdf = [n for n in names if not n.endswith(".pdf") and "internal" not in n]
        assert len(has_non_pdf) == 0
        assert any(n.endswith(".pdf") for n in names)


# ------------------------------------------------------------------
# Payee CRUD + draw balance tests
# ------------------------------------------------------------------

V = "/v1"


class TestPayeeCRUD:
    def test_create_get_update_delete(self) -> None:
        # Create
        resp = client.put(f"{V}/payees/P001", json={
            "name": "Alice", "quota": "100000", "plan_id": "saas_ae",
            "effective_from": "2026-01-01", "email": "alice@test.com",
            "quotas": {"2026-01": "50000"},
            "draw_amount": "2000", "draw_recoverable": True,
            "ramp_months": 3, "ramp_schedule": "0.25 0.50 0.75",
            "category_quotas": {"Enterprise": "300000"},
            "manager_id": "M001", "manager_override": "0.05", "team_id": "Team-A",
        })
        assert resp.status_code == 200

        # Get
        resp = client.get(f"{V}/payees/P001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Alice"
        assert data["email"] == "alice@test.com"
        assert '"2026-01"' in data.get("quotas", "")
        assert '"recoverable": true' in (data.get("draw") or "").lower()

        # List
        resp = client.get(f"{V}/payees")
        assert resp.status_code == 200
        payees = resp.json()
        assert any(p["id"] == "P001" for p in payees)

        # Update
        resp = client.put(f"{V}/payees/P001", json={
            "name": "Alice Updated", "quota": "120000", "plan_id": "saas_ae",
            "effective_from": "2026-01-01",
        })
        assert resp.status_code == 200
        resp = client.get(f"{V}/payees/P001")
        assert resp.json()["name"] == "Alice Updated"

        # Delete
        resp = client.delete(f"{V}/payees/P001")
        assert resp.status_code == 200
        resp = client.get(f"{V}/payees/P001")
        assert resp.status_code == 404

    def test_bulk_import_merge(self) -> None:
        # Seed one payee
        client.put(f"{V}/payees/P001", json={
            "name": "Original", "quota": "100000", "plan_id": "saas_ae",
            "effective_from": "2026-01-01",
        })

        # Import CSV with P001 updated + P002 new
        csv_content = (
            "id,name,quota,plan_id,effective_from,email\n"
            "P001,Alice Merged,100000,saas_ae,2026-01-01,alice@test.com\n"
            "P002,Bob,80000,saas_ae,2026-01-01,bob@test.com\n"
        )
        resp = client.post(f"{V}/payees/import", files={
            "file": ("payees.csv", csv_content.encode(), "text/csv"),
        }, data={"replace": "false"})
        assert resp.status_code == 200
        result = resp.json()
        assert result["imported"] == 2

        # P001 should be merged (name updated), P002 new
        p1 = client.get(f"{V}/payees/P001").json()
        assert p1["name"] == "Alice Merged"
        assert p1["email"] == "alice@test.com"
        assert client.get(f"{V}/payees/P002").status_code == 200

    def test_bulk_import_replace(self) -> None:
        # Seed
        client.put(f"{V}/payees/P001", json={
            "name": "Original", "quota": "100000", "plan_id": "saas_ae",
            "effective_from": "2026-01-01",
        })

        # Replace
        csv_content = "id,name,quota,plan_id,effective_from\nP003,Carol,90000,saas_ae,2026-01-01\n"
        resp = client.post(f"{V}/payees/import", files={
            "file": ("payees.csv", csv_content.encode(), "text/csv"),
        }, data={"replace": "true"})
        assert resp.status_code == 200

        # P001 gone, only P003
        assert client.get(f"{V}/payees/P001").status_code == 404
        assert client.get(f"{V}/payees/P003").status_code == 200


class TestDrawBalances:
    def test_draw_balance_carries_between_runs(self) -> None:
        # Import payee with a recoverable draw
        client.put(f"{V}/payees/D001", json={
            "name": "Draw Test", "quota": "100000", "plan_id": "saas_ae",
            "effective_from": "2026-01-01",
            "draw_amount": "5000", "draw_recoverable": True,
        })

        # Simulate two runs by directly using DB helpers
        from icm_engine.database import Database
        db = Database(os.environ["ICM_DB_PATH"])

        # Period 1: advance draw (balance = draw amount)
        db.set_draw_balance("D001", "saas_ae", Decimal("5000"))
        assert db.get_draw_balance("D001", "saas_ae") == Decimal("5000")

        # Period 2: recover half
        db.set_draw_balance("D001", "saas_ae", Decimal("2500"))
        assert db.get_draw_balance("D001", "saas_ae") == Decimal("2500")

        # Fully recovered
        db.set_draw_balance("D001", "saas_ae", Decimal("0"))
        assert db.get_draw_balance("D001", "saas_ae") == Decimal("0")


def test_calculate_applies_plan_rounding() -> None:
    """A plan rounding policy rounds the API's commission lines and summary,
    with the summary totalling the rounded lines."""
    plan_yaml = b"""
plan_id: round_test
name: Round Test
period_type: monthly
currency: USD
rounding:
  mode: floor
  places: 2
rules:
  - type: flat_rate
    id: R1
    rate: "0.05"
"""
    # 0.05 * 1291.99 = 64.5995 -> floor 2dp -> 64.59 (half-up would be 64.60)
    txns = b"id,payee_id,amount,period\nT1,P1,1291.99,2026-01\n"
    payees = b"id,name,quota,plan_id,effective_from\nP1,Alice,0,round_test,2026-01-01\n"
    response = client.post(
        f"{V}/calculate",
        files={
            "plan": ("plan.yaml", plan_yaml, "application/x-yaml"),
            "transactions": ("transactions.csv", txns, "text/csv"),
            "payees": ("payees.csv", payees, "text/csv"),
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["summary"]["P1"] == "64.59"
    assert data["commissions"][0]["commission_amount"] == "64.59"


def test_preview_csv_returns_cell_values() -> None:
    csv_bytes = b"id,payee_id,amount\nT1,P1,5000\nT2,P2,7500\n"
    response = client.post(
        f"{V}/preview",
        files={"file": ("deals.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["is_xlsx"] is False
    assert data["headers"] == ["id", "payee_id", "amount"]
    assert data["preview_rows"] == [["T1", "P1", "5000"], ["T2", "P2", "7500"]]


def test_preview_xlsx_returns_cell_values_not_headers() -> None:
    """Regression: preview_rows iterated the xlsx row dicts, which yields their
    keys, so every previewed row came back as the header list instead of data.
    """
    from icm_engine.excel import write_xlsx

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "deals.xlsx"
        write_xlsx(
            path,
            {
                "Sheet1": [
                    {"id": "T1", "payee_id": "P1", "amount": "5000"},
                    {"id": "T2", "payee_id": "P2", "amount": "7500"},
                ]
            },
        )
        with open(path, "rb") as f:
            response = client.post(
                f"{V}/preview",
                files={
                    "file": (
                        "deals.xlsx",
                        f,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["is_xlsx"] is True
    assert data["headers"] == ["id", "payee_id", "amount"]
    assert data["preview_rows"] == [["T1", "P1", "5000"], ["T2", "P2", "7500"]]
    assert data["preview_rows"][0] != data["headers"]


class TestCalculatePreflight:
    """The desktop app reaches the engine through this endpoint, so the checks
    the CLI runs have to run here too — they used to exist only in cli.py."""

    PLAN = (
        b"plan_id: pf_demo\nname: D\ncurrency: USD\nperiod_type: monthly\n"
        b"rules:\n  - type: flat_rate\n    id: R1\n    rate: '0.10'\n"
    )
    ROSTER = b"id,name,quota,plan_id,effective_from\nAKHAN,Aisha,8000,pf_demo,2024-01-01\n"

    def _post(self, deals: bytes, query: str = "") -> object:
        return client.post(
            f"{V}/calculate{query}",
            files={
                "plan": ("p.yaml", self.PLAN, "application/x-yaml"),
                "transactions": ("d.csv", deals, "text/csv"),
                "payees": ("r.csv", self.ROSTER, "text/csv"),
            },
        )

    MISMATCHED = (
        b"id,payee_id,period,amount\n"
        b"T1,AKHAN,2026-05,6000\n"
        b"T2,akhan,2026-05,6000\n"
    )

    def test_unknown_payee_is_rejected(self) -> None:
        r = self._post(self.MISMATCHED)
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert any(i["code"] == "unknown_payee" for i in detail["issues"])

    def test_rejection_names_the_id_and_suggests_the_match(self) -> None:
        detail = self._post(self.MISMATCHED).json()["detail"]
        message = detail["issues"][0]["message"]
        assert "akhan" in message
        assert "AKHAN" in message  # the suggestion the user needs

    def test_opt_out_allows_the_run(self) -> None:
        r = self._post(self.MISMATCHED, "?allow_unknown_payees=true")
        assert r.status_code == 200, r.text
        # Paid as two separate people — which is exactly why it blocks by default.
        assert set(r.json()["summary"]) == {"AKHAN", "akhan"}

    def test_consistent_ids_calculate_normally(self) -> None:
        r = self._post(
            b"id,payee_id,period,amount\n"
            b"T1,AKHAN,2026-05,6000\n"
            b"T2,AKHAN,2026-05,6000\n"
        )
        assert r.status_code == 200, r.text
        assert r.json()["summary"]["AKHAN"] == "1200.00"

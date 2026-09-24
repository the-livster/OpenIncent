"""Reading a past calculation back without recalculating it."""
import io
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from icm_engine import api

SAAS = {
    "plan": ("plan.yaml", Path("examples/saas_ae_plan.yaml").read_bytes()),
    "payees": ("payees.csv", Path("examples/saas_payees.csv").read_bytes()),
    "transactions": ("transactions.csv", Path("examples/saas_transactions.csv").read_bytes()),
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ICM_DB_PATH", str(tmp_path / "history.db"))
    monkeypatch.delenv("ICM_DESKTOP", raising=False)
    monkeypatch.setattr(api.limiter, "enabled", False)
    return TestClient(api.app)


def calculate(client, **kwargs):
    response = client.post("/v1/calculate", files=SAAS, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def only_id(data):
    """The single calculation id of a run that covered one period."""
    ids = list(data["calculation_ids"].values())
    assert len(ids) == 1, ids
    return ids[0]


def any_id(data):
    """One period's calculation id from a run that may span several."""
    return sorted(data["calculation_ids"].items())[0][1]


def lines_for(data, period):
    return [c for c in data["commissions"] if c["period"] == period]


def test_each_stored_period_matches_that_slice_of_the_original_response(client):
    live = calculate(client)
    assert len(live["calculation_ids"]) > 1, "this example should span several periods"

    rebuilt: dict[str, Decimal] = {}
    for period, calculation_id in sorted(live["calculation_ids"].items()):
        response = client.get("/v1/calculations/" + calculation_id + "/result")
        assert response.status_code == 200, response.text
        stored = response.json()

        assert stored["period"] == period
        assert stored["commissions"] == lines_for(live, period)
        assert stored["attainment"] == live["attainment"]
        assert stored["ledger_truncated"] is False
        for payee_id, total in stored["summary"].items():
            rebuilt[payee_id] = rebuilt.get(payee_id, Decimal(0)) + Decimal(total)

    # Every period put back together must reconcile to the run the user reviewed.
    assert {k: str(v) for k, v in sorted(rebuilt.items())} == live["summary"]


def test_stored_result_ignores_later_roster_and_plan_edits(client):
    live = calculate(client)
    calculation_id = any_id(live)
    before = client.get("/v1/calculations/" + calculation_id + "/result").json()

    client.post(
        "/v1/payees/import",
        params={"replace": "true"},
        files={"file": ("payees.csv", "id,name,quota,plan_id\nP_NEW,Nobody,1,saas_ae\n")},
    )
    client.post("/v1/plans", json={
        "plan_id": "saas_ae",
        "name": "Rewritten",
        "yaml_content": (
            "plan_id: saas_ae\nname: Rewritten\nperiod_type: monthly\ncurrency: USD\n"
            "rules:\n  - {id: r, type: flat_rate, rate: 0.99}\n"
        ),
    })

    stored = client.get("/v1/calculations/" + calculation_id + "/result").json()
    assert stored["commissions"] == lines_for(live, stored["period"])
    assert stored["payouts"] == before["payouts"]


def test_stored_result_carries_the_header_fields(client):
    live = calculate(client)
    calculation_id = any_id(live)
    stored = client.get("/v1/calculations/" + calculation_id + "/result").json()

    listed = next(c for c in client.get("/v1/calculations").json() if c["id"] == calculation_id)
    assert stored["calculation_id"] == calculation_id
    assert stored["plan_id"] == listed["plan_id"]
    assert stored["period"] == listed["period"]
    assert stored["version"] == listed["version"]
    assert stored["status"] == listed["status"]
    assert stored["created_at"] == listed["created_at"]
    assert stored["locked"] is False
    assert stored["calculation_ids"] == {stored["period"]: calculation_id}


def test_stored_result_rejects_an_unknown_calculation(client):
    assert client.get("/v1/calculations/does-not-exist/result").status_code == 404


def test_stored_result_reports_a_calculation_without_a_snapshot(client):
    calculation_id = api._get_db().record_calculation("legacy", period="2026-01")
    response = client.get("/v1/calculations/" + calculation_id + "/result")
    assert response.status_code == 409, response.text
    assert "snapshot" in response.json()["detail"]["error"]


def test_stored_result_applies_each_plans_own_rounding(client):
    for plan_id, currency, mode in [("A", "USD", "half-up"), ("B", "CAD", "floor")]:
        yaml = (
            "plan_id: " + plan_id + "\nname: Plan " + plan_id + "\nperiod_type: monthly\n"
            "currency: " + currency + "\nrounding: {mode: " + mode + ", places: 2}\n"
            # 1234.56 * 0.05 = 61.728, so floor and half-up must disagree.
            "rules:\n  - {id: rate, type: flat_rate, rate: 0.05}\n"
        )
        response = client.post("/v1/plans", json={
            "plan_id": plan_id, "name": "Plan " + plan_id, "yaml_content": yaml,
        })
        assert response.status_code in (200, 201), response.text

    payees = "id,name,quota,plan_id\nPA,Ann,1000,A\nPB,Ben,1000,B\n"
    transactions = (
        "id,payee_id,amount,period,close_date\n"
        "T1,PA,1234.56,2026-01,2026-01-15\n"
        "T2,PB,1234.56,2026-01,2026-01-15\n"
    )
    response = client.post("/v1/calculate", files={
        "payees": ("payees.csv", payees),
        "transactions": ("transactions.csv", transactions),
    })
    assert response.status_code == 200, response.text
    live = response.json()

    stored = client.get("/v1/calculations/" + only_id(live) + "/result").json()
    assert stored["summary"] == live["summary"]
    assert stored["payout_totals"] == live["payout_totals"]
    # floor and half-up must differ here, or the test proves nothing about rounding.
    assert stored["summary"]["PA"] != stored["summary"]["PB"]


def test_calculation_inputs_return_the_linked_transactions(client):
    live = calculate(client)
    response = client.get("/v1/calculations/" + any_id(live) + "/inputs")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert rows
    paid = {c["transaction_id"] for c in live["commissions"]}
    assert paid <= {r["id"] for r in rows}


def test_export_works_from_a_stored_calculation_id_alone(client, monkeypatch):
    calculate(client)
    calculation_id = client.get("/v1/calculations").json()[0]["id"]

    def cannot_calculate(*args, **kwargs):
        pytest.fail("Viewing history must never run the engine again")

    monkeypatch.setattr(api, "execute", cannot_calculate)
    response = client.get("/v1/calculations/" + calculation_id + "/result")
    assert response.status_code == 200, response.text

    response = client.post("/v1/calculations/export", json={
        "calculation_ids": list(response.json()["calculation_ids"].values()),
        "formats": ["xlsx", "html"],
    })
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert any(name.endswith(".xlsx") for name in archive.namelist())


def test_stored_ledger_belongs_only_to_that_calculation(client):
    first = any_id(calculate(client))
    second = any_id(calculate(client))
    assert first != second

    entries = client.get("/v1/calculations/" + second + "/result").json()["ledger"]
    assert entries
    assert {e["calculation_id"] for e in entries} == {second}


def test_stored_result_flags_a_truncated_ledger(client, monkeypatch):
    live = calculate(client)
    monkeypatch.setattr(api, "LEDGER_TRACE_LIMIT", 3)
    stored = client.get("/v1/calculations/" + any_id(live) + "/result").json()
    assert stored["ledger_truncated"] is True
    assert len(stored["ledger"]) == 3


def test_sample_results_stay_in_the_sample_database(client):
    samples = Path("../icm-ui/public/sample")
    response = client.post("/v1/calculate", data={"sample": "true"}, files={
        "plan": ("sample.yaml", (samples / "openincent_sample.yaml").read_bytes()),
        "payees": ("payees.csv", (samples / "payees.csv").read_bytes()),
        "transactions": ("transactions.csv", (samples / "transactions.csv").read_bytes()),
    })
    assert response.status_code == 200, response.text
    calculation_id = only_id(response.json())

    assert client.get("/v1/calculations/" + calculation_id + "/result").status_code == 404
    response = client.get(
        "/v1/calculations/" + calculation_id + "/result", params={"sample": "true"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["payout_totals"] == {"USD": "3500.00"}

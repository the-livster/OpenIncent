"""Regression checks for complete imports and exporting the reviewed calculation."""
import csv
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from icm_engine import api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ICM_DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.delenv("ICM_DESKTOP", raising=False)
    monkeypatch.setattr(api.limiter, "enabled", False)
    return TestClient(api.app)


def test_import_reads_every_payee_without_saving_roster(client):
    csv = "id,name,quota,plan_id,effective_from\n" + "".join(
        f'P{i},"Person {i}, Sales",10000,sample,2026-01-01\n' for i in range(125)
    )
    response = client.post("/v1/payees/parse", files={"file": ("payees.csv", csv)})
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 125
    assert rows[-1]["id"] == "P124"
    assert rows[-1]["name"] == "Person 124, Sales"
    assert client.get("/v1/payees").json() == []


def test_import_rejects_bad_rows_after_the_preview(client):
    csv = "id,name,quota,plan_id\n" + "".join(
        f"P{i},Person,{10000 if i != 80 else 'invalid'},sample\n" for i in range(100)
    )
    response = client.post("/v1/payees/parse", files={"file": ("payees.csv", csv)})
    assert response.status_code == 400
    assert "quota" in response.text.lower()


def test_export_uses_saved_results_without_calculating_again(client, monkeypatch):
    response = client.post("/v1/calculate", files={
        "plan": ("plan.yaml", Path("examples/saas_ae_plan.yaml").read_bytes()),
        "payees": ("payees.csv", Path("examples/saas_payees.csv").read_bytes()),
        "transactions": ("transactions.csv", Path("examples/saas_transactions.csv").read_bytes()),
    })
    assert response.status_code == 200, response.text
    data = response.json()
    before = client.get("/v1/calculations").json()

    def cannot_calculate(*args, **kwargs):
        pytest.fail("Export must use the reviewed result, not run the engine again")

    monkeypatch.setattr(api, "execute", cannot_calculate)
    response = client.post("/v1/calculations/export", json={
        "calculation_ids": list(data["calculation_ids"].values()), "formats": ["xlsx", "html"],
    })
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert any(name.endswith(".html") for name in archive.namelist())
        assert any(name.endswith(".xlsx") for name in archive.namelist())
        assert "internal/payout-summary.csv" in archive.namelist()
    assert client.get("/v1/calculations").json() == before


def test_export_rejects_unknown_calculation(client):
    response = client.post("/v1/calculations/export", json={
        "calculation_ids": ["does-not-exist"], "formats": ["html"],
    })
    assert response.status_code == 404


def test_roster_preserves_extended_fields_and_period_quotas(client):
    content = io.StringIO()
    writer = csv.writer(content)
    writer.writerow(["id", "name", "quota", "period", "plan_id", "effective_from", "effective_to",
                     "ramp_months", "ramp_schedule", "draw_amount", "draw_recoverable",
                     "category_quotas", "manager_id", "manager_override", "team_id"])
    for quota, period in [("10000.123", ""), ("8000.456", "2026-08")]:
        writer.writerow(["P1", 'Alex "Ace", Sales', quota, period, "plan", "2026-01-01", "2026-12-31",
                         "2", "0.5 1", "1000.23", "true", '{"Enterprise":"2500.125"}',
                         "M1", "0.02", "TEAM"])
    response = client.post("/v1/payees/parse", files={"file": ("payees.csv", content.getvalue())})
    assert response.status_code == 200, response.text
    row = response.json()[0]
    assert row["quota"] == "10000.123"
    assert row["quotas"] == {"2026-08": "8000.456"}
    assert row["name"] == 'Alex "Ace", Sales'
    assert row["ramp_schedule"] == "0.5 1"
    assert row["draw_amount"] == "1000.23"
    assert row["draw_recoverable"] == "true"
    assert json.loads(row["category_quotas"]) == {"Enterprise": "2500.125"}
    assert (row["manager_id"], row["manager_override"], row["team_id"]) == ("M1", "0.02", "TEAM")


def test_xlsx_import_is_not_truncated(client):
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["id", "name", "quota", "plan_id", "effective_from"])
    for i in range(125):
        sheet.append([f"P{i}", f"Person {i}", 10000, "plan", "2026-01-01"])
    content = io.BytesIO()
    workbook.save(content)
    response = client.post("/v1/payees/parse", files={"file": ("payees.xlsx", content.getvalue())})
    assert response.status_code == 200, response.text
    assert len(response.json()) == 125


def test_sample_journey_is_isolated_and_exports_all_formats(client):
    samples = Path("../icm-ui/public/sample")
    response = client.post("/v1/calculate", data={"sample": "true"}, files={
        "plan": ("sample.yaml", (samples / "openincent_sample.yaml").read_bytes()),
        "payees": ("payees.csv", (samples / "payees.csv").read_bytes()),
        "transactions": ("transactions.csv", (samples / "transactions.csv").read_bytes()),
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["payout_totals"] == {"USD": "3500.00"}
    assert client.get("/v1/calculations").json() == []
    payload = {"calculation_ids": list(data["calculation_ids"].values()),
               "formats": ["pdf", "xlsx", "html"], "sample": True}
    response = client.post("/v1/calculations/export", json=payload)
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        for extension in (".pdf", ".xlsx", ".html"):
            assert sum(name.endswith(extension) for name in archive.namelist()) == 3
        rows = list(csv.DictReader(io.StringIO(archive.read("internal/payout-summary.csv").decode())))
        assert rows == data["payouts"]
    payload["sample"] = False
    assert client.post("/v1/calculations/export", json=payload).status_code == 404


def test_multi_plan_export_keeps_original_names_currency_and_rounding(client):
    for plan_id, currency, mode in [("A", "USD", "half-up"), ("B", "CAD", "floor")]:
        yaml = (f"plan_id: {plan_id}\nname: Original {plan_id}\nperiod_type: monthly\ncurrency: {currency}\n"
                f"rounding: {{mode: {mode}, places: 2}}\nrules:\n  - {{id: rate, type: flat_rate, rate: 0.05}}\n")
        assert client.post("/v1/plans", json={"plan_id": plan_id, "name": f"Original {plan_id}",
                                            "yaml_content": yaml}).status_code == 200
    response = client.post("/v1/calculate", files={
        "payees": ("payees.csv", "id,name,quota,plan_id,effective_from\n"
                   "P1,Original Alex,100,A,2026-01-01\nP2,Original Sam,100,B,2026-01-01\n"),
        "transactions": ("transactions.csv", "id,payee_id,period,amount\n"
                         "T1,P1,2026-08,0.10\nT2,P1,2026-08,0.10\nT3,P2,2026-08,0.10\n"),
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["payout_totals"] == {"USD": "0.02", "CAD": "0.00"}
    # Updating the library after review must not affect statements.
    client.post("/v1/plans", json={"plan_id": "A", "name": "Changed", "yaml_content": "invalid"})
    response = client.post("/v1/calculations/export", json={
        "calculation_ids": list(data["calculation_ids"].values()), "formats": ["html", "xlsx"],
    })
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("internal/payout-summary.csv").decode())))
        assert rows == data["payouts"]
        html = "".join(archive.read(name).decode() for name in archive.namelist() if name.endswith(".html"))
        assert "Original Alex" in html and "Original Sam" in html
        assert "Original A" in html and "Original B" in html
        assert "0.02" in html and "Changed" not in html


@pytest.mark.parametrize("filename,content", [
    ("bad.xlsx", b"not an Excel file"),
    ("empty.csv", b"id,name,quota,plan_id\n"),
    ("duplicate.csv", b"id,name,quota,plan_id\nP1,Alex,10,A\nP1,Sam,10,A\n"),
])
def test_bad_roster_is_reported_as_an_import_error(client, filename, content):
    response = client.post("/v1/payees/parse", files={"file": (filename, content)})
    assert response.status_code == 400


def test_exports_are_scoped_to_organization_and_run(client):
    samples = Path("../icm-ui/public/sample")

    def calculate():
        response = client.post("/v1/calculate", files={
            "plan": ("sample.yaml", (samples / "openincent_sample.yaml").read_bytes()),
            "payees": ("payees.csv", (samples / "payees.csv").read_bytes()),
            "transactions": ("transactions.csv", (samples / "transactions.csv").read_bytes()),
        })
        assert response.status_code == 200, response.text
        return next(iter(response.json()["calculation_ids"].values()))

    first, second = calculate(), calculate()
    assert client.post("/v1/calculations/export", json={"calculation_ids": [first, second]}).status_code == 409
    assert client.post("/v1/calculations/export", json={"calculation_ids": [first, first]}).status_code == 400
    api.app.dependency_overrides[api.get_org] = lambda: "another-org"
    try:
        assert client.post("/v1/calculations/export", json={"calculation_ids": [first]}).status_code == 404
    finally:
        api.app.dependency_overrides.pop(api.get_org)

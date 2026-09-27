"""The interactive HTML statement, the PDF layout, and what a saved run's
statement is built from."""

import io
import json
import re
import sqlite3
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from icm_engine import api
from icm_engine.engine import CommissionEngine
from icm_engine.models import Commission, FlatRateRule, Payee, Plan, Tier, TieredRule, Transaction
from icm_engine.statements import (
    StatementTheme,
    _accent_palette,
    _contrast,
    _money,
    generate_statements,
)


def _payee(pid: str = "P1", name: str = "Alice", quota: str = "0") -> Payee:
    return Payee(id=pid, name=name, quota=Decimal(quota), plan_id="p", effective_from=date(2026, 1, 1))


def _flat_plan() -> Plan:
    return Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])


def _line(tid: str = "T1", amount: str = "500", notes: str = "", **extra: str) -> Commission:
    return Commission(transaction_id=tid, payee_id="P1", period=extra.pop("period", "2026-01"),
                      rule_id=extra.pop("rule_id", "R1"), base_amount=Decimal(amount) * 20,
                      rate=Decimal("0.05"), commission_amount=Decimal(amount), notes=notes, **extra)


def _page(files: list, pid: str = "P1") -> str:
    path = next(f.path for f in files if f.payee_id == pid and f.fmt == "html")
    return str(path.read_text(encoding="utf-8"))


def _data(page: str) -> dict:
    m = re.search(r'<script type="application/json" id="stmt-data">(.*?)</script>', page, re.S)
    assert m, "statement has no data island"
    return dict(json.loads(m.group(1)))


class TestInteractiveHtml:
    def test_a_deal_crossing_a_tier_is_one_item_with_each_band_explained(self, tmp_path: Path) -> None:
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD", rules=[
            TieredRule(type="tiered", id="core", tiers=[
                Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
                Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
            ]),
        ])
        payees = [_payee(quota="100000")]
        txns = [Transaction(id="D1", payee_id="P1", period="2026-06", amount=Decimal("120000"))]
        result = CommissionEngine().calculate(plan, txns, payees)
        page = _page(generate_statements(result.commissions, payees, out_dir=tmp_path, formats=("html",)))

        assert page.count("<details class=\"item\"") == 1
        assert page.count('class="slice"') == 2
        assert "$100,000.00 × 5%" in page and "= $5,000.00" in page
        assert "$20,000.00 × 10%" in page and "= $2,000.00" in page
        assert "$7,000.00" in page

    def test_controls_stay_hidden_without_scripts(self, tmp_path: Path) -> None:
        # An email preview runs no scripts: a search box that cannot search
        # would be a broken promise, so every control starts hidden.
        result = CommissionEngine().calculate(_flat_plan(), [
            Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000")),
        ], [_payee()])
        page = _page(generate_statements(result.commissions, [_payee()], out_dir=tmp_path, formats=("html",)))

        controls = re.findall(r"<[^>]*\bdata-js\b[^>]*>", page)
        assert controls
        assert all(" hidden" in tag for tag in controls)
        assert "<details class=\"item\"" in page and " open>" in page  # short statements start expanded

    def test_nothing_in_a_line_can_escape_the_data_island(self, tmp_path: Path) -> None:
        notes = 'Deal "A&B" </script><script>alert(1)</script>'
        page = _page(generate_statements([_line(notes=notes)], [_payee()], out_dir=tmp_path, formats=("html",)))

        assert "</script><script>alert(1)" not in page
        assert _data(page)["rows"][0][-1] == notes  # still intact for the CSV

    def test_csv_data_holds_display_amounts_not_raw_ones(self, tmp_path: Path) -> None:
        page = _page(generate_statements([_line(amount="64.595")], [_payee()], out_dir=tmp_path,
                                         formats=("html",)))
        row = _data(page)["rows"][0]
        assert row[8] == "64.60"
        assert "64.595" not in page

    def test_deal_details_come_from_the_payees_own_transactions_only(self, tmp_path: Path) -> None:
        payees = [_payee("P1", "Alice"), _payee("P2", "Bob")]
        txns = [
            Transaction(id="T1", payee_id="P1", deal_id="D-ALICE", period="2026-01", amount=Decimal("10000"),
                        product="Enterprise", close_date=date(2026, 1, 10)),
            Transaction(id="T2", payee_id="P2", deal_id="D-BOB", period="2026-01", amount=Decimal("5000"),
                        product="Secret Widget", close_date=date(2026, 1, 11)),
        ]
        result = CommissionEngine().calculate(_flat_plan(), txns, payees)
        files = generate_statements(result.commissions, payees, out_dir=tmp_path, formats=("html",),
                                    transactions=txns)
        page = _page(files, "P1")

        assert "D-ALICE" in page and "Enterprise" in page and "Closed 10 January 2026" in page
        for other in ("D-BOB", "Secret Widget", "Bob", "P2", "T2"):
            assert other not in page

    def test_money_is_grouped_and_signed(self) -> None:
        assert _money("$", "1234567.5") == "$1,234,567.5"
        assert _money("£", "-300.00") == "−£300.00"
        assert _money("$", "-0.00") == "$0.00"
        assert _money("USD ", "65") == "USD 65"
        assert _money("$", "-12.5", minus="-") == "-$12.5"

    def test_a_pale_brand_colour_still_gets_legible_text(self, tmp_path: Path) -> None:
        palette = _accent_palette("#fde047")
        assert _contrast(palette.ink, "#ffffff") >= 4.5
        assert _contrast(palette.ink_dark, "#12151c") >= 4.5
        page = _page(generate_statements([_line()], [_payee()], out_dir=tmp_path, formats=("html",),
                                         theme=StatementTheme(accent_color="#fde047")))
        assert "--accent:#fde047" in page  # bars keep the brand colour itself


class TestAttainment:
    def test_a_saved_runs_attainment_shows_its_percentage(self, tmp_path: Path) -> None:
        # Saved runs keep attainment as dicts of strings; they used to print "N/A".
        attainment = [{"payee_id": "P1", "period": "2026-01", "bookings": "12000",
                       "quota": "10000", "attainment_pct": "1.2"}]
        files = generate_statements([_line()], [_payee()], out_dir=tmp_path, period="2026-01",
                                    formats=("html", "xlsx"), attainment=attainment)
        page = _page(files)
        assert "120%" in page and "Quota reached" in page and "N/A" not in page

        from icm_engine.excel import read_xlsx_rows
        xlsx = next(f.path for f in files if f.fmt == "xlsx")
        _, rows = read_xlsx_rows(xlsx, sheet="Info")
        assert any("(120.0%)" in str(row.values()) for row in rows)

    def test_each_period_shows_its_own_attainment(self, tmp_path: Path) -> None:
        attainment = [
            {"payee_id": "P1", "period": "2026-01", "bookings": "3700", "quota": "10000", "attainment_pct": "0.37"},
            {"payee_id": "P1", "period": "2026-02", "bookings": "13000", "quota": "10000", "attainment_pct": "1.3"},
            {"payee_id": "P9", "period": "2026-02", "bookings": "9000", "quota": "10000", "attainment_pct": "0.9"},
        ]
        lines = [_line("T1", period="2026-01"), _line("T2", period="2026-02")]
        page = _page(generate_statements(lines, [_payee()], out_dir=tmp_path, period="2026-02",
                                         formats=("html",), attainment=attainment))
        assert "130%" in page
        assert "37%" not in page and "90%" not in page

    def test_no_quota_means_no_attainment_card(self, tmp_path: Path) -> None:
        attainment = [{"payee_id": "P1", "period": "2026-01", "bookings": "500", "quota": "0",
                       "attainment_pct": "None"}]
        page = _page(generate_statements([_line()], [_payee()], out_dir=tmp_path, formats=("html",),
                                         attainment=attainment))
        assert "Quota attainment" not in page

    def test_a_statement_for_every_period_charts_each_one(self, tmp_path: Path) -> None:
        attainment = [
            {"payee_id": "P1", "period": "2026-02", "bookings": "13000", "quota": "10000", "attainment_pct": "1.3"},
            {"payee_id": "P1", "period": "2026-01", "bookings": "3700", "quota": "10000", "attainment_pct": "0.37"},
        ]
        lines = [_line("T1", period="2026-01"), _line("T2", period="2026-02")]
        page = _page(generate_statements(lines, [_payee()], out_dir=tmp_path, formats=("html",),
                                         attainment=attainment))
        assert page.index("Jan 2026</span>") < page.index("Feb 2026</span>")  # oldest first
        assert 'data-period-btn="2026-01"' in page and 'data-period-btn="2026-02"' in page


class TestPdf:
    def test_text_outside_the_pdf_font_does_not_stop_the_run(self, tmp_path: Path) -> None:
        # The staffing example's own theme has an em dash in its contact line,
        # which used to raise FPDFUnicodeEncodingException.
        theme = StatementTheme(contact_line="Questions? Email pay@example.com — thanks",
                               footer_note="Paid ✓ → June", currency_symbol="₹")
        files = generate_statements([_line()], [_payee(name="张伟 Zhang")], out_dir=tmp_path,
                                    formats=("pdf",), theme=theme)
        assert files[0].path.stat().st_size > 0

    def test_a_long_statement_runs_onto_more_pages(self, tmp_path: Path) -> None:
        lines = [_line(f"T{i}", notes="A long plain-English explanation of this deal. " * 3) for i in range(80)]
        files = generate_statements(lines, [_payee()], out_dir=tmp_path, formats=("pdf",))
        pages = re.findall(rb"/Type /Page\b(?!s)", files[0].path.read_bytes())
        assert len(pages) > 1


# ------------------------------------------------------------------
# Saved runs: export and in-app preview
# ------------------------------------------------------------------

PLAN = ("plan_id: p\nname: Flat\nperiod_type: monthly\ncurrency: USD\n"
        "rules:\n  - {id: base, type: flat_rate, rate: 0.05}\n")
PAYEES = "id,name,quota,plan_id,effective_from\nP1,Alice,10000,p,2026-01-01\nP2,Bob,10000,p,2026-01-01\n"


def _txns(product: str) -> str:
    return ("id,payee_id,deal_id,period,amount,product,close_date\n"
            f"T1,P1,D-100,2026-01,10000,{product},2026-01-05\n"
            "T2,P2,D-200,2026-01,5000,Hidden,2026-01-06\n")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ICM_DB_PATH", str(tmp_path / "statements.db"))
    monkeypatch.delenv("ICM_DESKTOP", raising=False)
    monkeypatch.setattr(api.limiter, "enabled", False)
    api._db_cache.clear()
    return TestClient(api.app)


def _calculate(client: TestClient, product: str = "Pro") -> list[str]:
    response = client.post("/v1/calculate", files={
        "plan": ("plan.yaml", PLAN), "payees": ("payees.csv", PAYEES),
        "transactions": ("transactions.csv", _txns(product)),
    })
    assert response.status_code == 200, response.text
    return list(response.json()["calculation_ids"].values())


def _preview(client: TestClient, ids: list[str], payee: str = "P1", period: str = "2026-01"):
    return client.post("/v1/calculations/statement", json={
        "calculation_ids": ids, "payee_id": payee, "period": period,
    })


def test_preview_is_the_exported_statement(client):
    ids = _calculate(client)
    preview = _preview(client, ids)
    assert preview.status_code == 200, preview.text
    assert preview.headers["content-type"].startswith("text/html")
    assert preview.headers["cache-control"] == "no-store"

    export = client.post("/v1/calculations/export", json={"calculation_ids": ids, "formats": ["html"]})
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        exported = [archive.read(n).decode() for n in archive.namelist() if n.endswith("statement_P1_2026-01.html")]
    assert exported == [preview.text]


def test_preview_shows_one_payee_and_their_own_deals(client):
    page = _preview(client, _calculate(client)).text
    assert "Alice" in page and "D-100" in page and "Closed 5 January 2026" in page
    for other in ("Bob", "D-200", "Hidden", "P2"):
        assert other not in page


def test_deal_details_are_frozen_with_the_run(client):
    first = _calculate(client, product="Pro")
    _calculate(client, product="Renamed")  # re-upload overwrites the stored transaction
    page = _preview(client, first).text
    assert "Pro" in page and "Renamed" not in page


def test_runs_saved_before_deal_details_still_preview(client, tmp_path):
    ids = _calculate(client)
    with sqlite3.connect(tmp_path / "statements.db") as conn:
        for cid in ids:
            (summary,) = conn.execute("SELECT input_summary FROM calculations WHERE id=?", (cid,)).fetchone()
            data = json.loads(summary)
            data.pop("statement_deals")
            conn.execute("UPDATE calculations SET input_summary=? WHERE id=?", (json.dumps(data), cid))
    page = _preview(client, ids).text
    assert "T1" in page and "D-100" not in page


def test_preview_of_someone_not_in_the_run_is_not_found(client):
    ids = _calculate(client)
    assert _preview(client, ids, payee="P404").status_code == 404
    assert _preview(client, ids, period="2025-12").status_code == 404
    assert _preview(client, ["no-such-run"]).status_code == 404

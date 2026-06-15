from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from icm_engine.cli import app
from icm_engine.models import Commission
from icm_engine.reconcile import (
    _parse_money,
    commissions_to_totals,
    load_commission_totals_csv,
    load_paid_csv,
    reconcile_commissions,
    reconcile_totals,
    write_report_csv,
)

runner = CliRunner()


def _c(payee: str, period: str, amount: str) -> Commission:
    return Commission(
        transaction_id="t", payee_id=payee, period=period, rule_id="r",
        base_amount=Decimal(amount), rate=Decimal("1"),
        commission_amount=Decimal(amount),
    )


# --- core classification -------------------------------------------------


def test_match_within_tolerance() -> None:
    rep = reconcile_totals(
        {("p1", "2026-01"): Decimal("100.00")},
        {("p1", "2026-01"): Decimal("100.005")},
    )
    assert rep.lines[0].status == "match"
    assert not rep.has_discrepancies


def test_underpaid() -> None:
    rep = reconcile_totals(
        {("p1", "2026-01"): Decimal("1219.00")},
        {("p1", "2026-01"): Decimal("1000.00")},
    )
    ln = rep.lines[0]
    assert ln.status == "underpaid"
    assert ln.delta == Decimal("219.00")
    assert rep.total_owed_to_payees == Decimal("219.00")
    assert rep.total_overpaid == Decimal("0")
    assert rep.net_delta == Decimal("219.00")


def test_overpaid() -> None:
    rep = reconcile_totals(
        {("p1", "2026-01"): Decimal("900.00")},
        {("p1", "2026-01"): Decimal("1000.00")},
    )
    ln = rep.lines[0]
    assert ln.status == "overpaid"
    assert ln.delta == Decimal("-100.00")
    assert rep.total_overpaid == Decimal("100.00")


def test_missing_payment() -> None:
    rep = reconcile_totals({("p1", "2026-01"): Decimal("500.00")}, {})
    assert rep.lines[0].status == "missing"
    assert rep.total_owed_to_payees == Decimal("500.00")


def test_unexpected_payment() -> None:
    rep = reconcile_totals({}, {("p1", "2026-01"): Decimal("500.00")})
    assert rep.lines[0].status == "unexpected"
    assert rep.total_overpaid == Decimal("500.00")


def test_counts_and_discrepancies() -> None:
    computed = {
        ("a", "2026-01"): Decimal("100"),
        ("b", "2026-01"): Decimal("200"),
        ("c", "2026-01"): Decimal("300"),
    }
    paid = {
        ("a", "2026-01"): Decimal("100"),
        ("b", "2026-01"): Decimal("150"),
        ("d", "2026-01"): Decimal("50"),
    }
    rep = reconcile_totals(computed, paid)
    counts = rep.counts()
    assert counts["match"] == 1       # a
    assert counts["underpaid"] == 1   # b (200 vs 150)
    assert counts["missing"] == 1     # c (computed only)
    assert counts["unexpected"] == 1  # d (paid only)
    assert len(rep.discrepancies) == 3


def test_tolerance_param() -> None:
    computed = {("p", "2026-01"): Decimal("100.00")}
    paid = {("p", "2026-01"): Decimal("100.50")}
    assert reconcile_totals(computed, paid, tolerance=Decimal("0.01")).lines[0].status == "overpaid"
    assert reconcile_totals(computed, paid, tolerance=Decimal("1.00")).lines[0].status == "match"


# --- commissions aggregation ---------------------------------------------


def test_commissions_to_totals_aggregates_lines() -> None:
    comms = [_c("p1", "2026-01", "100"), _c("p1", "2026-01", "50"), _c("p2", "2026-01", "200")]
    totals = commissions_to_totals(comms)
    assert totals[("p1", "2026-01")] == Decimal("150")
    assert totals[("p2", "2026-01")] == Decimal("200")


def test_reconcile_commissions_convenience() -> None:
    comms = [_c("p1", "2026-01", "219"), _c("p1", "2026-01", "1000")]
    rep = reconcile_commissions(comms, {("p1", "2026-01"): Decimal("1000")})
    assert rep.lines[0].status == "underpaid"
    assert rep.lines[0].delta == Decimal("219.00")


# --- money parsing -------------------------------------------------------


def test_parse_money_variants() -> None:
    assert _parse_money("$1,234.50") == Decimal("1234.50")
    assert _parse_money("£219") == Decimal("219")
    assert _parse_money("(50.00)") == Decimal("-50.00")
    assert _parse_money("") == Decimal("0")
    assert _parse_money(None) == Decimal("0")
    assert _parse_money("  1 000.00 ") == Decimal("1000.00")


# --- file loaders --------------------------------------------------------


def test_load_paid_csv_fuzzy_headers(tmp_path: Path) -> None:
    p = tmp_path / "paid.csv"
    p.write_text('Rep,Month,Amount Paid\nalice,2026-01,"$1,000.00"\nbob,2026-01,500\n')
    totals = load_paid_csv(p)
    assert totals[("alice", "2026-01")] == Decimal("1000.00")
    assert totals[("bob", "2026-01")] == Decimal("500")


def test_load_paid_csv_default_period(tmp_path: Path) -> None:
    p = tmp_path / "paid.csv"
    p.write_text("payee,paid\nalice,100\n")
    totals = load_paid_csv(p, default_period="2026-03")
    assert totals[("alice", "2026-03")] == Decimal("100")


def test_load_paid_csv_missing_period_errors(tmp_path: Path) -> None:
    p = tmp_path / "paid.csv"
    p.write_text("payee,paid\nalice,100\n")
    with pytest.raises(ValueError):
        load_paid_csv(p)


def test_load_commission_totals_csv(tmp_path: Path) -> None:
    p = tmp_path / "commissions.csv"
    p.write_text(
        "transaction_id,payee_id,period,rule_id,base_amount,rate,commission_amount,notes\n"
        "D1,alice,2026-01,r,100,0.1,10.00,n\n"
        "D2,alice,2026-01,r,100,0.1,5.00,n\n"
        "D3,bob,2026-01,r,100,0.1,20.00,n\n"
    )
    totals = load_commission_totals_csv(p)
    assert totals[("alice", "2026-01")] == Decimal("15.00")
    assert totals[("bob", "2026-01")] == Decimal("20.00")


def test_write_report_csv_roundtrip(tmp_path: Path) -> None:
    rep = reconcile_totals(
        {("p1", "2026-01"): Decimal("100")},
        {("p1", "2026-01"): Decimal("80")},
    )
    out = tmp_path / "report.csv"
    write_report_csv(rep, out)
    text = out.read_text()
    assert "payee_id,period,computed,paid,delta,status" in text
    assert "p1,2026-01,100.00,80.00,20.00,underpaid" in text


# --- CLI -----------------------------------------------------------------


def test_cli_reconcile_clean(tmp_path: Path) -> None:
    comm = tmp_path / "commissions.csv"
    comm.write_text(
        "transaction_id,payee_id,period,rule_id,base_amount,rate,commission_amount,notes\n"
        "D1,alice,2026-01,r,100,0.1,100.00,n\n"
    )
    paid = tmp_path / "paid.csv"
    paid.write_text("payee,period,paid\nalice,2026-01,100.00\n")
    result = runner.invoke(app, ["reconcile", "--commissions", str(comm), "--paid", str(paid)])
    assert result.exit_code == 0, result.stdout
    assert "reconcile" in result.stdout.lower()


def test_cli_reconcile_discrepancy_strict(tmp_path: Path) -> None:
    comm = tmp_path / "commissions.csv"
    comm.write_text(
        "transaction_id,payee_id,period,rule_id,base_amount,rate,commission_amount,notes\n"
        "D1,alice,2026-01,r,100,0.1,1219.00,n\n"
    )
    paid = tmp_path / "paid.csv"
    paid.write_text("payee,period,paid\nalice,2026-01,1000.00\n")
    out = tmp_path / "report.csv"
    result = runner.invoke(app, [
        "reconcile", "--commissions", str(comm), "--paid", str(paid),
        "--strict", "--output", str(out),
    ])
    assert result.exit_code == 1
    assert out.exists()
    assert "underpaid" in result.stdout

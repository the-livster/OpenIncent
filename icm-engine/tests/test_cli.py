from pathlib import Path

from typer.testing import CliRunner

from icm_engine.cli import app

runner = CliRunner()


def test_calculate_end_to_end(tmp_path: Path) -> None:
    plan = "examples/flat_rate_plan.yaml"
    txns = "tests/fixtures/sample_transactions.csv"
    payees = "tests/fixtures/sample_payees.csv"
    out = str(tmp_path / "output")

    result = runner.invoke(
        app,
        [
            "--plan", plan,
            "--transactions", txns,
            "--payees", payees,
            "--output", out,
            "--no-db",
        ],
    )

    assert result.exit_code == 0, f"CLI failed: {result.stderr}"
    out_dir = Path(out)
    assert (out_dir / "commissions.csv").exists()
    assert (out_dir / "summary.csv").exists()
    assert (out_dir / "ledger.jsonl").exists()

    # commissions.csv has header + rows
    commissions_lines = (
        (out_dir / "commissions.csv").read_text().strip().split("\n")
    )
    assert len(commissions_lines) >= 2  # header + at least 1 commission

    # ledger.jsonl has entries
    ledger_lines = (
        (out_dir / "ledger.jsonl").read_text().strip().split("\n")
    )
    assert len(ledger_lines) >= 1


def test_calculate_missing_plan(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "--plan", "nonexistent.yaml",
            "--transactions", "tests/fixtures/sample_transactions.csv",
            "--payees", "tests/fixtures/sample_payees.csv",
            "--output", str(tmp_path / "output"),
            "--no-db",
        ],
    )
    assert result.exit_code != 0


def test_statements_multi_plan_routes_payees_to_their_plan(tmp_path: Path) -> None:
    """--plans generates statements for a multi-plan run: each payee is paid
    under their own plan (contract desk on GP, not the first plan's rules)."""
    ref = "examples/staffing_reference"
    out = str(tmp_path / "stmts")

    result = runner.invoke(app, [
        "statements",
        "--plans", f"{ref}/perm_plan.yaml",
        "--plans", f"{ref}/contract_plan.yaml",
        "--plans", f"{ref}/mgmt_plan.yaml",
        "--transactions", f"{ref}/transactions.csv",
        "--payees", f"{ref}/payees.csv",
        "--adjustments", f"{ref}/adjustments.csv",
        "--output", out,
        "--period", "2026-05",
        "--format", "html",
        "--theme", f"{ref}/theme.yaml",
    ])
    assert result.exit_code == 0, f"CLI failed: {result.output}"

    files = sorted(Path(out).glob("*.html"))
    assert len(files) == 6

    # Perm desk: Priya's reconciled total, from the tiered perm plan
    priya = (Path(out) / "statement_P-101_2026-05.html").read_text(encoding="utf-8")
    assert "2480.00" in priya
    assert "Northwind Recruitment" in priya  # theme applied

    # Contract desk: Aisha's GP-based total — only correct if she was routed
    # to the contract plan rather than computed against the first plan
    aisha = (Path(out) / "statement_P-110_2026-05.html").read_text(encoding="utf-8")
    assert "1897.50" in aisha

    # Manager override plan
    dana = (Path(out) / "statement_P-201_2026-05.html").read_text(encoding="utf-8")
    assert "1050.00" in dana


def test_statements_requires_a_plan(tmp_path: Path) -> None:
    result = runner.invoke(app, [
        "statements",
        "--transactions", "tests/fixtures/sample_transactions.csv",
        "--payees", "tests/fixtures/sample_payees.csv",
        "--output", str(tmp_path / "x"),
    ])
    assert result.exit_code == 1

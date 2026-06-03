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
        ],
    )
    assert result.exit_code != 0

from datetime import date
from decimal import Decimal
from pathlib import Path

from icm_engine.engine import CommissionEngine
from icm_engine.models import (
    FlatRateRule,
    Payee,
    Plan,
    Transaction,
)
from icm_engine.statements import generate_statements


def _payee(**overrides):
    defaults = dict(
        id="P001", name="Alice", quota=Decimal("0"), plan_id="p",
        effective_from=date(2026, 1, 1),
    )
    return Payee(**(defaults | overrides))


class TestGenerateStatements:
    def test_two_payees_two_files_each(self) -> None:
        """Two payees get one file each per format."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [
            _payee(id="P1", name="Alice"),
            _payee(id="P2", name="Bob"),
        ]
        txns = [
            Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                        close_date=date(2026, 1, 10)),
            Transaction(id="T2", payee_id="P2", period="2026-01", amount=Decimal("5000"),
                        close_date=date(2026, 1, 10)),
        ]
        result = CommissionEngine().calculate(plan, txns, payees)
        out = Path("tests/fixtures/_stmt_test_1")
        out.mkdir(parents=True, exist_ok=True)

        files = generate_statements(
            result.commissions, payees, out_dir=out, formats=("xlsx", "html"),
        )
        assert len(files) == 4  # 2 payees × 2 formats
        pids = {f.payee_id for f in files}
        assert pids == {"P1", "P2"}

    def test_isolation_xlsx(self) -> None:
        """CRITICAL: P1's XLSX file contains NONE of P2's data."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [
            _payee(id="P1", name="Alice"),
            _payee(id="P2", name="Bob"),
        ]
        txns = [
            Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                        close_date=date(2026, 1, 10)),
            Transaction(id="T2", payee_id="P2", period="2026-01", amount=Decimal("5000"),
                        close_date=date(2026, 1, 10)),
        ]
        result = CommissionEngine().calculate(plan, txns, payees)
        out = Path("tests/fixtures/_stmt_iso")
        out.mkdir(parents=True, exist_ok=True)

        files = generate_statements(result.commissions, payees, out_dir=out, formats=("xlsx",))
        p1_file = next(f.path for f in files if f.payee_id == "P1")

        # Read the XLSX and verify no P2 data
        from icm_engine.excel import read_xlsx_rows
        headers, rows = read_xlsx_rows(p1_file)
        all_text = " ".join(str(v) for row in rows for v in row.values())
        assert "P2" not in all_text
        assert "Bob" not in all_text
        assert "5000" not in all_text  # P2's amount
        assert "250.00" not in all_text  # P2's commission

    def test_isolation_html(self) -> None:
        """CRITICAL: P1's HTML file contains NONE of P2's data."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [
            _payee(id="P1", name="Alice"),
            _payee(id="P2", name="Bob"),
        ]
        txns = [
            Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                        close_date=date(2026, 1, 10)),
            Transaction(id="T2", payee_id="P2", period="2026-01", amount=Decimal("5000"),
                        close_date=date(2026, 1, 10)),
        ]
        result = CommissionEngine().calculate(plan, txns, payees)
        out = Path("tests/fixtures/_stmt_iso2")
        out.mkdir(parents=True, exist_ok=True)

        files = generate_statements(result.commissions, payees, out_dir=out, formats=("html",))
        p1_file = next(f.path for f in files if f.payee_id == "P1")
        content = p1_file.read_text(encoding="utf-8")
        assert "P2" not in content
        assert "Bob" not in content
        assert "250.00" not in content

    def test_totals_match(self) -> None:
        """Each statement's total equals the sum of that payee's commissions."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="Alice")]
        txns = [
            Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                        close_date=date(2026, 1, 10)),
            Transaction(id="T2", payee_id="P1", period="2026-01", amount=Decimal("5000"),
                        close_date=date(2026, 1, 10)),
        ]
        result = CommissionEngine().calculate(plan, txns, payees)
        out = Path("tests/fixtures/_stmt_total")
        out.mkdir(parents=True, exist_ok=True)

        files = generate_statements(result.commissions, payees, out_dir=out, formats=("html",))
        content = files[0].path.read_text(encoding="utf-8")
        assert "$750.00" in content  # 500 + 250

    def test_period_filter(self) -> None:
        """Only the requested period's lines appear."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="Alice")]
        txns = [
            Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                        close_date=date(2026, 1, 10)),
            Transaction(id="T2", payee_id="P1", period="2026-02", amount=Decimal("5000"),
                        close_date=date(2026, 2, 10)),
        ]
        result = CommissionEngine().calculate(plan, txns, payees)
        out = Path("tests/fixtures/_stmt_period")
        out.mkdir(parents=True, exist_ok=True)

        files = generate_statements(
            result.commissions, payees, out_dir=out, period="2026-01", formats=("html",),
        )
        content = files[0].path.read_text(encoding="utf-8")
        assert "T1" in content
        assert "T2" not in content
        assert "$500.00" in content  # only T1's commission

    def test_deterministic(self) -> None:
        """Same inputs produce byte-identical files."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="Alice")]
        txns = [
            Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                        close_date=date(2026, 1, 10)),
        ]
        result = CommissionEngine().calculate(plan, txns, payees)
        out = Path("tests/fixtures/_stmt_det")
        out.mkdir(parents=True, exist_ok=True)

        f1 = generate_statements(result.commissions, payees, out_dir=out, formats=("html",))
        f2 = generate_statements(result.commissions, payees, out_dir=out, formats=("html",))
        assert f1[0].path.read_bytes() == f2[0].path.read_bytes()

    def test_emit_zero_false_skips(self) -> None:
        """emit_zero=False (default) skips payees with no lines."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="Alice"), _payee(id="P2", name="Bob")]
        txns = [Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                            close_date=date(2026, 1, 10))]
        result = CommissionEngine().calculate(plan, txns, payees)
        out = Path("tests/fixtures/_stmt_zero")
        out.mkdir(parents=True, exist_ok=True)

        files = generate_statements(result.commissions, payees, out_dir=out, formats=("html",))
        pids = {f.payee_id for f in files}
        assert pids == {"P1"}  # P2 skipped

    def test_emit_zero_true_includes(self) -> None:
        """emit_zero=True includes $0 statements for payees with no lines."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="Alice"), _payee(id="P2", name="Bob")]
        txns = [Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                            close_date=date(2026, 1, 10))]
        result = CommissionEngine().calculate(plan, txns, payees)
        out = Path("tests/fixtures/_stmt_zero2")
        out.mkdir(parents=True, exist_ok=True)

        files = generate_statements(
            result.commissions, payees, out_dir=out, formats=("html",), emit_zero=True,
        )
        pids = {f.payee_id for f in files}
        assert pids == {"P1", "P2"}

    def test_pdf_format(self) -> None:
        """PDF generates without error."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="Alice")]
        txns = [Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                            close_date=date(2026, 1, 10))]
        result = CommissionEngine().calculate(plan, txns, payees)
        out = Path("tests/fixtures/_stmt_pdf")
        out.mkdir(parents=True, exist_ok=True)

        files = generate_statements(result.commissions, payees, out_dir=out, formats=("pdf",))
        assert len(files) == 1
        assert files[0].path.exists()
        assert files[0].path.stat().st_size > 0

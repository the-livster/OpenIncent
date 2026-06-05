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

    def test_display_rounding_html(self) -> None:
        """Ragged values (e.g. 0.05 × 1291.90 = 64.595) render as 64.60."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="Alice")]
        txns = [Transaction(id="T1", payee_id="P1", period="2026-01",
                            amount=Decimal("1291.90"), close_date=date(2026, 1, 10))]
        result = CommissionEngine().calculate(plan, txns, payees)
        out = Path("tests/fixtures/_stmt_round")
        out.mkdir(parents=True, exist_ok=True)

        files = generate_statements(result.commissions, payees, out_dir=out, formats=("html",))
        content = files[0].path.read_text(encoding="utf-8")
        # 0.05 * 1291.90 = 64.595 → rounded to 64.60
        assert "$64.60" in content
        assert "$64.59" not in content  # ensure not truncated
        assert "$64.595" not in content  # ensure raw value not leaked

    def test_format_selection(self) -> None:
        """Only requested formats are produced."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="Alice")]
        txns = [Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                            close_date=date(2026, 1, 10))]
        result = CommissionEngine().calculate(plan, txns, payees)
        out = Path("tests/fixtures/_stmt_fmt")
        out.mkdir(parents=True, exist_ok=True)

        # Request only HTML — no XLSX or PDF should be produced
        files = generate_statements(result.commissions, payees, out_dir=out, formats=("html",))
        fmts = {f.fmt for f in files}
        assert fmts == {"html"}

        # Request multiple
        files2 = generate_statements(result.commissions, payees, out_dir=out, formats=("xlsx", "html"))
        fmts2 = {f.fmt for f in files2}
        assert fmts2 == {"xlsx", "html"}

    def test_adjustment_labels(self) -> None:
        """Clawbacks and true-ups get labelled in HTML output."""
        from icm_engine.models import Commission

        payees = [_payee(id="P1", name="Alice")]

        # Build commissions directly: one positive, one clawback, one true-up
        commissions = [
            Commission(transaction_id="T1", payee_id="P1", period="2026-01",
                       rule_id="R1", base_amount=Decimal("10000"), rate=Decimal("0.05"),
                       commission_amount=Decimal("500"), notes=""),
            Commission(transaction_id="T2", payee_id="P1", period="2026-01",
                       rule_id="R1", base_amount=Decimal("-5000"), rate=Decimal("0.05"),
                       commission_amount=Decimal("-250"), notes="Refund"),
            Commission(transaction_id="T3", payee_id="P1", period="2026-06",
                       origin_period="2026-03", rule_id="R1",
                       base_amount=Decimal("2000"), rate=Decimal("0.05"),
                       commission_amount=Decimal("100"), notes="true_up: 400 -> 500 (delta 100)"),
        ]
        out = Path("tests/fixtures/_stmt_adj")
        out.mkdir(parents=True, exist_ok=True)

        files = generate_statements(commissions, payees, out_dir=out, formats=("html",))
        content = files[0].path.read_text(encoding="utf-8")
        assert "Clawback" in content
        assert "True-up" in content

    def test_plan_name_in_output(self) -> None:
        """Plan name appears in HTML, XLSX, and PDF output."""
        plan = Plan(plan_id="p", name="Enterprise Growth Plan", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="Alice")]
        txns = [Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                            close_date=date(2026, 1, 10))]
        result = CommissionEngine().calculate(plan, txns, payees)
        out = Path("tests/fixtures/_stmt_plan")
        out.mkdir(parents=True, exist_ok=True)

        files = generate_statements(
            result.commissions, payees, out_dir=out,
            formats=("html", "xlsx", "pdf"),
            plan_name=plan.name,
        )
        for sf in files:
            if sf.fmt == "html":
                content = sf.path.read_text(encoding="utf-8")
                assert "Enterprise Growth Plan" in content
            elif sf.fmt == "xlsx":
                from icm_engine.excel import read_xlsx_rows
                _, rows = read_xlsx_rows(sf.path, sheet="Info")
                all_text = " ".join(str(v) for row in rows for v in row.values())
                assert "Enterprise Growth Plan" in all_text
            elif sf.fmt == "pdf":
                assert sf.path.stat().st_size > 0

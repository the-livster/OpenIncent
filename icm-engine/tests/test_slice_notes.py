"""E1: rep-facing slice math rendered in plain English on per-rep statements."""

from datetime import date
from decimal import Decimal

from icm_engine.engine import CommissionEngine
from icm_engine.models import Payee, Plan, Tier, TieredRule, Transaction


def _tiered_plan() -> Plan:
    return Plan(
        plan_id="p", name="P", period_type="monthly", currency="USD",
        rules=[TieredRule(type="tiered", id="t", tiers=[
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
        ])],
    )


def _payee() -> Payee:
    return Payee(id="R1", name="Riley", quota=Decimal("100000"),
                 plan_id="p", effective_from=date(2025, 1, 1))


class TestRepFacingSliceNotes:
    def test_intra_deal_split_notes_plain_english(self) -> None:
        # quota 100k; one 120k deal → 100k @5% (0–100%), 20k @10% (100–120%)
        plan = _tiered_plan()
        txns = [Transaction(id="D1", payee_id="R1", amount=Decimal("120000"), period="2026-06")]
        result = CommissionEngine().calculate(plan, txns, [_payee()])
        notes = " || ".join(c.notes for c in result.commissions)
        assert "between 0% and 100% of quota" in notes
        assert "between 100% and 120% of quota" in notes
        assert "5.00%" in notes and "10.00%" in notes
        assert sum((c.commission_amount for c in result.commissions), Decimal("0")) == Decimal("7000")

    def test_slice_notes_reach_the_html_statement(self, tmp_path) -> None:
        from icm_engine.statements import generate_statements
        plan = _tiered_plan()
        txns = [Transaction(id="D1", payee_id="R1", amount=Decimal("120000"), period="2026-06")]
        result = CommissionEngine().calculate(plan, txns, [_payee()])
        files = generate_statements(result.commissions, [_payee()], out_dir=tmp_path, formats=("html",))
        content = files[0].path.read_text(encoding="utf-8")
        assert "of quota" in content  # the plain-English band rendered onto the statement

"""Dual credit channels (B1): `quota_amount` decouples 'counts toward quota'
from 'pays commission'. Payment is unaffected; only attainment/the gate change."""

from datetime import date
from decimal import Decimal

from icm_engine.engine import CommissionEngine
from icm_engine.models import Credit, FlatRateRule, Payee, Plan, Transaction


def _plan(rules):
    return Plan(plan_id="p", name="P", period_type="monthly", currency="USD", rules=rules)


def _payee(pid="R1", quota="100000"):
    return Payee(id=pid, name=pid, quota=Decimal(quota), plan_id="p", effective_from=date(2025, 1, 1))


class TestDualCreditChannels:
    def test_quota_value_defaults_to_amount(self) -> None:
        t = Transaction(id="D1", payee_id="R1", amount=Decimal("1000"), period="2026-01")
        assert t.quota_value == Decimal("1000")
        t2 = Transaction(id="D2", payee_id="R1", amount=Decimal("1000"),
                         period="2026-01", quota_amount=Decimal("0"))
        assert t2.quota_value == Decimal("0")

    def test_non_retiring_deal_still_pays_but_excluded_from_attainment(self) -> None:
        plan = _plan([FlatRateRule(type="flat_rate", id="r", rate=Decimal("0.05"))])
        txns = [
            Transaction(id="A", payee_id="R1", amount=Decimal("60000"),
                        period="2026-01", quota_amount=Decimal("0")),   # SPIF-only, non-retiring
            Transaction(id="B", payee_id="R1", amount=Decimal("50000"), period="2026-01"),
        ]
        result = CommissionEngine().calculate(plan, txns, [_payee()])
        # Both deals pay (3,000 + 2,500)...
        total = sum((c.commission_amount for c in result.commissions), Decimal("0"))
        assert total == Decimal("5500")
        # ...but only B counts toward quota.
        att = {a.payee_id: a for a in result.attainment}
        assert att["R1"].bookings == Decimal("50000")

    def test_non_retiring_deal_cannot_clear_min_attainment_gate(self) -> None:
        plan = _plan([FlatRateRule(
            type="flat_rate", id="r", rate=Decimal("0.05"), min_attainment_pct=Decimal("0.5"))])
        txns = [Transaction(id="A", payee_id="R1", amount=Decimal("60000"),
                            period="2026-01", quota_amount=Decimal("0"))]
        result = CommissionEngine().calculate(plan, txns, [_payee()])
        # 0% attainment < 50% gate → gated out
        assert sum((c.commission_amount for c in result.commissions), Decimal("0")) == Decimal("0")
        assert any(
            e.event_type == "rule_skipped" and e.inputs.get("reason") == "below_threshold_gate"
            for e in result.ledger
        )

    def test_capped_quota_retirement(self) -> None:
        plan = _plan([FlatRateRule(type="flat_rate", id="r", rate=Decimal("0.05"))])
        # pays on 100k but retires only 40k of quota
        txns = [Transaction(id="A", payee_id="R1", amount=Decimal("100000"),
                            period="2026-01", quota_amount=Decimal("40000"))]
        result = CommissionEngine().calculate(plan, txns, [_payee()])
        att = {a.payee_id: a for a in result.attainment}
        assert att["R1"].bookings == Decimal("40000")
        assert att["R1"].attainment_pct == Decimal("0.4")
        assert sum((c.commission_amount for c in result.commissions), Decimal("0")) == Decimal("5000")

    def test_quota_amount_splits_proportionally(self) -> None:
        plan = _plan([FlatRateRule(type="flat_rate", id="r", rate=Decimal("0.05"))])
        txns = [Transaction(
            id="A", payee_id="R1", amount=Decimal("100000"), period="2026-01",
            quota_amount=Decimal("80000"),
            credits=[Credit(payee_id="R1", split_pct=Decimal("0.6")),
                     Credit(payee_id="R2", split_pct=Decimal("0.4"))],
        )]
        result = CommissionEngine().calculate(plan, txns, [_payee("R1"), _payee("R2")])
        att = {a.payee_id: a for a in result.attainment}
        assert att["R1"].bookings == Decimal("48000")  # 80k × 0.6
        assert att["R2"].bookings == Decimal("32000")  # 80k × 0.4

    def test_default_behavior_unchanged(self) -> None:
        # No quota_amount → attainment == amount (regression guard).
        plan = _plan([FlatRateRule(type="flat_rate", id="r", rate=Decimal("0.05"))])
        txns = [Transaction(id="A", payee_id="R1", amount=Decimal("75000"), period="2026-01")]
        result = CommissionEngine().calculate(plan, txns, [_payee()])
        att = {a.payee_id: a for a in result.attainment}
        assert att["R1"].bookings == Decimal("75000")

    def test_quota_amount_from_csv(self, tmp_path) -> None:
        from icm_engine.loader import load_transactions
        csv_path = tmp_path / "txns.csv"
        csv_path.write_text(
            "id,payee_id,amount,period,quota_amount\n"
            "A,R1,60000,2026-01,0\n"
            "B,R1,50000,2026-01,\n",
            encoding="utf-8",
        )
        txns, _ = load_transactions(csv_path)
        by_id = {t.id: t for t in txns}
        assert by_id["A"].quota_amount == Decimal("0")
        assert by_id["A"].quota_value == Decimal("0")
        assert by_id["B"].quota_amount is None
        assert by_id["B"].quota_value == Decimal("50000")

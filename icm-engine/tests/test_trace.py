from datetime import date
from decimal import Decimal

from icm_engine.engine import CommissionEngine
from icm_engine.models import (
    AcceleratorRule,
    FlatRateRule,
    Payee,
    Plan,
    Tier,
    TieredRule,
    Transaction,
)
from icm_engine.trace import build_order_trace


def _txn(**overrides):
    defaults = dict(
        id="T001", payee_id="P001", deal_id="D001", period="2026-01",
        amount=Decimal("1000"), product=None, close_date=date(2026, 4, 15),
    )
    return Transaction(**(defaults | overrides))


def _payee(**overrides):
    defaults = dict(
        id="P001", name="Alice", quota=Decimal("10000"), plan_id="PLAN-A",
        effective_from=date(2026, 1, 1),
    )
    return Payee(**(defaults | overrides))


class TestBuildOrderTrace:
    def test_tiered_crossing_boundary(self) -> None:
        """Tiered order crossing a boundary: trace shows matched, both pieces, correct total."""
        engine = CommissionEngine()
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[TieredRule(type="tiered", id="t", tiers=[
                        Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
                        Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
                    ])])
        payees = [_payee(id="P1", quota=Decimal("10000"), plan_id="p")]
        txns = [_txn(id="D1", payee_id="P1", amount=Decimal("15000"), close_date=date(2026, 4, 1))]
        result = engine.calculate(plan, txns, payees)

        trace = build_order_trace("D1", "P1", result.ledger, txns[0])
        assert trace.transaction_id == "D1"
        assert len(trace.steps) == 1
        step = trace.steps[0]
        assert step.status == "matched"
        assert step.reason is None
        comm_events = [e for e in step.events if e["event_type"] == "commission_computed"]
        assert len(comm_events) >= 1
        tier_events = [e for e in step.events if e["event_type"] == "tier_crossed"]
        assert len(tier_events) >= 1
        # Total from ledger matches
        expected = sum(Decimal(e["outputs"]["commission_amount"]) for e in comm_events)
        assert trace.total == expected
        assert trace.total > 0

    def test_skipped_rule(self) -> None:
        """Rule that excludes the order shows as skipped with reason."""
        engine = CommissionEngine()
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"),
                                        filter='product == "Enterprise"')])
        payees = [_payee(id="P1", plan_id="p")]
        txns = [_txn(id="D1", payee_id="P1", product="Standard", amount=Decimal("5000"))]
        result = engine.calculate(plan, txns, payees)

        trace = build_order_trace("D1", "P1", result.ledger, txns[0])
        assert len(trace.steps) == 1
        assert trace.steps[0].status == "skipped"
        assert trace.steps[0].reason == "filter_excluded"
        assert trace.total == Decimal("0")

    def test_multiple_rules(self) -> None:
        """Order touched by multiple rules: all appear in ledger order."""
        engine = CommissionEngine()
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[
                        FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05")),
                        AcceleratorRule(type="accelerator", id="R2", rate=Decimal("0.05"),
                                        threshold_pct=Decimal("1.0"), multiplier=Decimal("2.0")),
                    ])
        payees = [_payee(id="P1", quota=Decimal("10000"), plan_id="p")]
        txns = [_txn(id="D1", payee_id="P1", amount=Decimal("15000"), close_date=date(2026, 4, 1))]
        result = engine.calculate(plan, txns, payees)

        trace = build_order_trace("D1", "P1", result.ledger, txns[0])
        assert len(trace.steps) == 2
        assert trace.steps[0].rule_id == "R1"
        assert trace.steps[1].rule_id == "R2"

    def test_total_is_ledger_sum(self) -> None:
        """Total equals sum of commission_computed outputs — does not recompute."""
        engine = CommissionEngine()
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", plan_id="p")]
        txns = [_txn(id="D1", payee_id="P1", amount=Decimal("10000"), close_date=date(2026, 4, 1))]
        result = engine.calculate(plan, txns, payees)

        trace = build_order_trace("D1", "P1", result.ledger)
        # Manually compute expected from ledger
        expected = sum(
            Decimal(e.outputs["commission_amount"])
            for e in result.ledger
            if e.event_type == "commission_computed"
            and e.transaction_id == "D1" and e.payee_id == "P1"
        )
        assert trace.total == expected

    def test_deterministic(self) -> None:
        """Same inputs produce identical OrderTrace."""
        engine = CommissionEngine()
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", plan_id="p")]
        txns = [_txn(id="D1", payee_id="P1", amount=Decimal("10000"), close_date=date(2026, 4, 1))]
        result = engine.calculate(plan, txns, payees)

        t1 = build_order_trace("D1", "P1", result.ledger, txns[0])
        t2 = build_order_trace("D1", "P1", result.ledger, txns[0])
        assert t1.total == t2.total
        assert t1.summary == t2.summary
        assert len(t1.steps) == len(t2.steps)

    def test_order_header_from_transaction(self) -> None:
        """When transaction is supplied, order header is populated."""
        engine = CommissionEngine()
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", plan_id="p")]
        txn = _txn(id="D1", payee_id="P1", amount=Decimal("10000"), product="Enterprise",
                   close_date=date(2026, 4, 1))
        result = engine.calculate(plan, [txn], payees)

        trace = build_order_trace("D1", "P1", result.ledger, txn)
        assert trace.order["amount"] == "10000"
        assert trace.order["product"] == "Enterprise"

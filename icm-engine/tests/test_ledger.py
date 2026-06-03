from datetime import date
from decimal import Decimal
from pathlib import Path

from icm_engine.engine import CommissionEngine
from icm_engine.ledger import LedgerEntry, write_ledger_jsonl
from icm_engine.models import (
    FlatRateRule,
    Payee,
    Plan,
    Tier,
    TieredRule,
    Transaction,
)


def _txn(**overrides) -> Transaction:
    defaults = dict(
        id="T001",
        payee_id="P001",
        deal_id="D001",
        period="2026-04",
        amount=Decimal("1000"),
        product=None,
        close_date=date(2026, 4, 15),
    )
    return Transaction(**(defaults | overrides))


def _payee(**overrides) -> Payee:
    defaults = dict(
        id="P001",
        name="Alice",
        quota=Decimal("10000"),
        plan_id="PLAN-A",
        effective_from=date(2026, 1, 1),
    )
    return Payee(**(defaults | overrides))


class TestLedgerEntry:
    def test_to_dict(self) -> None:
        entry = LedgerEntry(
            transaction_id="T001",
            payee_id="P001",
            rule_id="flat_5",
            event_type="commission_computed",
            inputs={"amount": "1000", "rate": "0.05"},
            outputs={"commission": "50.00"},
            human_readable="Test entry",
        )
        d = entry.to_dict()
        assert d["transaction_id"] == "T001"
        assert d["event_type"] == "commission_computed"
        assert d["inputs"]["amount"] == "1000"
        assert d["outputs"]["commission"] == "50.00"
        assert "timestamp" in d

    def test_repr(self) -> None:
        entry = LedgerEntry(
            transaction_id="T001",
            payee_id="P001",
            rule_id="flat_5",
            event_type="commission_computed",
        )
        assert "commission_computed" in repr(entry)
        assert "T001" in repr(entry)


class TestLedgerIntegration:
    def test_flat_rate_produces_ledger(self) -> None:
        engine = CommissionEngine()
        plan = Plan(
            plan_id="test",
            name="Test",
            period_type="monthly",
            currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="flat", rate=Decimal("0.05"))],
        )
        payees = [_payee()]
        txns = [_txn()]
        result = engine.calculate(plan, txns, payees)
        assert len(result.commissions) == 1
        # Now includes credit_allocated + commission_computed
        comm_entries = [e for e in result.ledger if e.event_type == "commission_computed"]
        assert len(comm_entries) == 1
        entry = comm_entries[0]
        assert entry.event_type == "commission_computed"
        assert entry.rule_id == "flat"

    def test_property_every_commission_has_ledger_trail(self) -> None:
        """For every Commission output, there's a matching ledger entry."""
        engine = CommissionEngine()
        plan = Plan(
            plan_id="test",
            name="Test",
            period_type="monthly",
            currency="USD",
            rules=[
                FlatRateRule(type="flat_rate", id="flat", rate=Decimal("0.05")),
                TieredRule(
                    type="tiered",
                    id="tiered",
                    tiers=[
                        Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
                        Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
                    ],
                ),
            ],
        )
        payees = [_payee(quota=Decimal("10000"))]
        txns = [_txn(amount=Decimal("15000"))]
        result = engine.calculate(plan, txns, payees)
        for commission in result.commissions:
            matching = [
                e
                for e in result.ledger
                if e.event_type == "commission_computed"
                and e.rule_id == commission.rule_id
                and e.transaction_id == commission.transaction_id
            ]
            assert len(matching) > 0, (
                f"No ledger trail for commission: rule={commission.rule_id}, "
                f"txn={commission.transaction_id}"
            )

    def test_skipped_produces_ledger_entry(self) -> None:
        engine = CommissionEngine()
        plan = Plan(
            plan_id="test",
            name="Test",
            period_type="monthly",
            currency="USD",
            rules=[
                FlatRateRule(
                    type="flat_rate",
                    id="flat",
                    rate=Decimal("0.05"),
                    filter='product == "Enterprise"',
                ),
            ],
        )
        payees = [_payee()]
        txns = [_txn(product="Standard")]
        result = engine.calculate(plan, txns, payees)
        assert len(result.commissions) == 0
        assert any(e.event_type == "rule_skipped" for e in result.ledger)

    def test_tier_crossing_ledger_entry(self) -> None:
        engine = CommissionEngine()
        plan = Plan(
            plan_id="test",
            name="Test",
            period_type="monthly",
            currency="USD",
            rules=[
                TieredRule(
                    type="tiered",
                    id="tiered",
                    tiers=[
                        Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
                        Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
                    ],
                ),
            ],
        )
        payees = [_payee(quota=Decimal("10000"))]
        txns = [_txn(amount=Decimal("15000"))]  # crosses the 1.0 threshold
        result = engine.calculate(plan, txns, payees)
        tier_crosses = [e for e in result.ledger if e.event_type == "tier_crossed"]
        assert len(tier_crosses) > 0


class TestWriteLedgerJSONL:
    def test_write_and_read_back(self, tmp_path: Path) -> None:
        entries = [
            LedgerEntry(
                transaction_id="T001",
                payee_id="P001",
                rule_id="r1",
                event_type="commission_computed",
                outputs={"commission": "50.00"},
            ),
            LedgerEntry(
                transaction_id="T002",
                payee_id="P001",
                rule_id="r1",
                event_type="commission_computed",
                outputs={"commission": "100.00"},
            ),
        ]
        path = tmp_path / "ledger.jsonl"
        write_ledger_jsonl(entries, path)
        assert path.exists()
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert '"commission_computed"' in lines[0]
        assert '"50.00"' in lines[0]

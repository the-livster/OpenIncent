"""Tests for margin-based commission (gross profit as alternative commission base)."""

from datetime import date
from decimal import Decimal

from icm_engine.engine import CommissionEngine
from icm_engine.models import (
    FlatRateRule,
    Payee,
    Plan,
    Transaction,
)

# ------------------------------------------------------------------
# Transaction.margin_value
# ------------------------------------------------------------------


class TestMarginValue:
    def test_from_rates_and_units(self) -> None:
        t = Transaction(
            id="T1", payee_id="P1", amount=Decimal("0"), period="2026-06",
            bill_rate=Decimal("85"), pay_rate=Decimal("60"), units=Decimal("160"),
        )
        assert t.margin_value == Decimal("4000")  # (85 - 60) * 160 = 4000

    def test_from_direct_margin_override(self) -> None:
        t = Transaction(
            id="T1", payee_id="P1", amount=Decimal("0"), period="2026-06",
            bill_rate=Decimal("85"), pay_rate=Decimal("60"), units=Decimal("160"),
            margin=Decimal("5000"),
        )
        assert t.margin_value == Decimal("5000")  # explicit override wins

    def test_none_when_absent(self) -> None:
        t = Transaction(
            id="T1", payee_id="P1", amount=Decimal("1000"), period="2026-06",
        )
        assert t.margin_value is None

    def test_none_when_only_bill_rate(self) -> None:
        t = Transaction(
            id="T1", payee_id="P1", amount=Decimal("0"), period="2026-06",
            bill_rate=Decimal("85"),
        )
        assert t.margin_value is None

    def test_none_when_only_pay_rate(self) -> None:
        t = Transaction(
            id="T1", payee_id="P1", amount=Decimal("0"), period="2026-06",
            pay_rate=Decimal("60"),
        )
        assert t.margin_value is None

    def test_default_units_is_one(self) -> None:
        t = Transaction(
            id="T1", payee_id="P1", amount=Decimal("0"), period="2026-06",
            bill_rate=Decimal("100"), pay_rate=Decimal("50"),
        )
        assert t.margin_value == Decimal("50")  # (100 - 50) * 1


# ------------------------------------------------------------------
# FlatRateRule with base="margin"
# ------------------------------------------------------------------


def _make_plan(plan_id: str, rate: str) -> Plan:
    return Plan(
        plan_id=plan_id, name="Contract Desk Plan",
        period_type="monthly", currency="USD",
        rules=[FlatRateRule(type="flat_rate", id="margin_flat", rate=Decimal(rate), base="margin")],
    )


def _p(id: str) -> Payee:
    return Payee(id=id, name=id, quota=Decimal("100000"), plan_id="contract", effective_from=date.today())


class TestFlatRateMargin:
    def test_single_deal_gp(self) -> None:
        """One contract placement: bill $85/hr, pay $60/hr, 160 hrs → GP $4,000 → 10% = $400."""
        plan = _make_plan("contract", "0.10")
        txns = [Transaction(
            id="D1", payee_id="R1", amount=Decimal("0"), period="2026-06",
            bill_rate=Decimal("85"), pay_rate=Decimal("60"), units=Decimal("160"),
        )]
        payees = [_p("R1")]
        result = CommissionEngine().calculate(plan, txns, payees)
        assert len(result.commissions) == 1
        c = result.commissions[0]
        assert c.base_amount == Decimal("4000")  # GP
        assert c.commission_amount == Decimal("400")
        assert c.rule_id == "margin_flat"

    def test_explicit_margin_override(self) -> None:
        """Direct margin override: $5,000 GP → 10% = $500."""
        plan = _make_plan("contract", "0.10")
        txns = [Transaction(
            id="D1", payee_id="R1", amount=Decimal("0"), period="2026-06",
            margin=Decimal("5000"),
        )]
        payees = [_p("R1")]
        result = CommissionEngine().calculate(plan, txns, payees)
        c = result.commissions[0]
        assert c.base_amount == Decimal("5000")
        assert c.commission_amount == Decimal("500")

    def test_margin_with_splits(self) -> None:
        """Two recruiters split one placement 60/40. Each gets rate × (margin × split)."""
        plan = _make_plan("contract", "0.10")
        from icm_engine.models import Credit

        txns = [Transaction(
            id="D1", payee_id="R1", amount=Decimal("0"), period="2026-06",
            bill_rate=Decimal("100"), pay_rate=Decimal("50"), units=Decimal("200"),
            credits=[
                Credit(payee_id="R1", split_pct=Decimal("0.6"), kind="split"),
                Credit(payee_id="R2", split_pct=Decimal("0.4"), kind="split"),
            ],
        )]
        payees = [_p("R1"), _p("R2")]
        result = CommissionEngine().calculate(plan, txns, payees)

        assert len(result.commissions) == 2
        by_payee = {c.payee_id: c for c in result.commissions}
        # GP = (100 - 50) * 200 = 10,000
        # R1: 10,000 * 0.6 * 0.10 = 600
        # R2: 10,000 * 0.4 * 0.10 = 400
        assert by_payee["R1"].commission_amount == Decimal("600")
        assert by_payee["R2"].commission_amount == Decimal("400")
        # Splits must sum to 1.0
        assert by_payee["R1"].split_pct == Decimal("0.6")
        assert by_payee["R2"].split_pct == Decimal("0.4")

    def test_margin_skips_rows_with_no_margin_data(self) -> None:
        """A margin rule against a tx with no margin data skips that row and
        records a ledger entry, rather than aborting the entire run (one bad
        row must not zero out everyone else's commission)."""
        plan = _make_plan("contract", "0.10")
        txns = [Transaction(
            id="D1", payee_id="R1", amount=Decimal("5000"), period="2026-06",
            # no bill_rate, pay_rate, units, or margin
        )]
        payees = [_p("R1")]
        result = CommissionEngine().calculate(plan, txns, payees)  # must not raise
        assert result.commissions == []
        assert any(
            e.event_type == "rule_skipped" and e.inputs.get("reason") == "no_margin_data"
            for e in result.ledger
        )

    def test_margin_rule_with_amount_data_uses_margin(self) -> None:
        """A margin rule ignores amount — it uses margin exclusively."""
        plan = _make_plan("contract", "0.10")
        txns = [Transaction(
            id="D1", payee_id="R1", amount=Decimal("999999"), period="2026-06",
            margin=Decimal("2000"),
        )]
        payees = [_p("R1")]
        result = CommissionEngine().calculate(plan, txns, payees)
        c = result.commissions[0]
        assert c.base_amount == Decimal("2000")  # margin, not amount
        assert c.commission_amount == Decimal("200")

    def test_multiple_deal_reconciliation(self) -> None:
        """Multi-deal contract desk reconciles to the cent vs hand-computed total."""
        plan = _make_plan("contract", "0.15")
        txns = [
            Transaction(
                id="D1", payee_id="R1", amount=Decimal("0"), period="2026-06",
                bill_rate=Decimal("85"), pay_rate=Decimal("60"), units=Decimal("160"),
            ),
            Transaction(
                id="D2", payee_id="R1", amount=Decimal("0"), period="2026-06",
                bill_rate=Decimal("100"), pay_rate=Decimal("70"), units=Decimal("120"),
            ),
            Transaction(
                id="D3", payee_id="R1", amount=Decimal("0"), period="2026-06",
                margin=Decimal("750"),
            ),
        ]
        # D1: (85-60)*160 = 4000 * 0.15 = 600
        # D2: (100-70)*120 = 3600 * 0.15 = 540
        # D3: 750 * 0.15 = 112.5
        # Total: 1252.50
        payees = [_p("R1")]
        result = CommissionEngine().calculate(plan, txns, payees)
        total = sum(c.commission_amount for c in result.commissions)
        assert len(result.commissions) == 3
        assert total == Decimal("1252.50")

    def test_zero_margin(self) -> None:
        """Zero margin should produce zero commission (not an error)."""
        plan = _make_plan("contract", "0.10")
        txns = [Transaction(
            id="D1", payee_id="R1", amount=Decimal("0"), period="2026-06",
            bill_rate=Decimal("60"), pay_rate=Decimal("60"), units=Decimal("160"),
        )]
        payees = [_p("R1")]
        result = CommissionEngine().calculate(plan, txns, payees)
        c = result.commissions[0]
        assert c.base_amount == Decimal("0")
        assert c.commission_amount == Decimal("0")


# ------------------------------------------------------------------
# Backward compatibility: amount-based plans unaffected
# ------------------------------------------------------------------


class TestBackwardCompat:
    def test_amount_plan_unchanged(self) -> None:
        """An amount-based plan produces identical output after margin changes."""
        plan = Plan(
            plan_id="saas", name="SaaS Plan",
            period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="flat", rate=Decimal("0.10"))],
        )
        txns = [Transaction(
            id="D1", payee_id="R1", amount=Decimal("50000"), period="2026-06",
        )]
        payees = [Payee(id="R1", name="R1", quota=Decimal("200000"), plan_id="saas", effective_from=date.today())]
        result = CommissionEngine().calculate(plan, txns, payees)
        c = result.commissions[0]
        assert c.base_amount == Decimal("50000")
        assert c.commission_amount == Decimal("5000")

    def test_amount_plan_with_margin_data_ignores_margin(self) -> None:
        """An amount-based rule ignores margin fields on the transaction."""
        plan = Plan(
            plan_id="saas", name="SaaS Plan",
            period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="flat", rate=Decimal("0.10"))],
        )
        txns = [Transaction(
            id="D1", payee_id="R1", amount=Decimal("50000"), period="2026-06",
            bill_rate=Decimal("1000"), pay_rate=Decimal("500"), units=Decimal("40"),
        )]
        payees = [Payee(id="R1", name="R1", quota=Decimal("200000"), plan_id="saas", effective_from=date.today())]
        result = CommissionEngine().calculate(plan, txns, payees)
        c = result.commissions[0]
        assert c.base_amount == Decimal("50000")  # amount, not margin
        assert c.commission_amount == Decimal("5000")


# ------------------------------------------------------------------
# Tiered/Accelerator margin: error path
# ------------------------------------------------------------------


class TestMarginTieredAcceleratorCompute:
    """Margin base now computes for tiered + accelerator (these previously raised)."""

    def test_tiered_margin_computes(self) -> None:
        from icm_engine.models import Tier, TieredRule

        plan = Plan(
            plan_id="test", name="Test", period_type="monthly", currency="USD",
            rules=[TieredRule(
                type="tiered", id="t", base="margin",
                tiers=[Tier(threshold_pct=Decimal("1"), rate=Decimal("0.10"))],
            )],
        )
        txns = [Transaction(
            id="D1", payee_id="R1", amount=Decimal("0"), period="2026-06",
            margin=Decimal("5000"),
        )]
        payees = [Payee(id="R1", name="R1", quota=Decimal("100000"), plan_id="test", effective_from=date.today())]
        result = CommissionEngine().calculate(plan, txns, payees)
        total = sum((c.commission_amount for c in result.commissions), Decimal("0"))
        assert total == Decimal("500")  # 5,000 GP @ 10%

    def test_accelerator_margin_computes(self) -> None:
        from icm_engine.models import AcceleratorRule

        plan = Plan(
            plan_id="test", name="Test", period_type="monthly", currency="USD",
            rules=[AcceleratorRule(
                type="accelerator", id="a", base="margin",
                rate=Decimal("0.10"), threshold_pct=Decimal("1"), multiplier=Decimal("2"),
            )],
        )
        txns = [Transaction(
            id="D1", payee_id="R1", amount=Decimal("0"), period="2026-06",
            margin=Decimal("150000"),
        )]
        payees = [Payee(id="R1", name="R1", quota=Decimal("100000"), plan_id="test", effective_from=date.today())]
        result = CommissionEngine().calculate(plan, txns, payees)
        # GP above threshold = 150,000 - 100,000 = 50,000 ; 50,000 * 0.10 * 2 = 10,000
        total = sum((c.commission_amount for c in result.commissions), Decimal("0"))
        assert total == Decimal("10000")


# ------------------------------------------------------------------
# CSV ingestion
# ------------------------------------------------------------------


class TestCSVIngestion:
    def test_csv_with_margin_columns(self, tmp_path) -> None:
        """CSV with Bill Rate / Pay Rate / Hours headers maps and computes margin."""
        csv = tmp_path / "deals.csv"
        csv.write_text(
            "id,payee_id,Bill Rate,Pay Rate,Hours,amount,period\n"
            "D1,R1,100,50,200,0,2026-06\n"
        )
        from icm_engine.loader import load_transactions
        txns, _ = load_transactions(str(csv))
        assert len(txns) == 1
        t = txns[0]
        assert t.bill_rate == Decimal("100")
        assert t.pay_rate == Decimal("50")
        assert t.units == Decimal("200")
        assert t.margin_value == Decimal("10000")

        # Now run through engine
        plan = Plan(
            plan_id="contract", name="Contract Desk",
            period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="margin_flat", rate=Decimal("0.10"), base="margin")],
        )
        payees = [Payee(id="R1", name="R1", quota=Decimal("100000"), plan_id="contract", effective_from=date.today())]
        result = CommissionEngine().calculate(plan, txns, payees)
        assert result.commissions[0].commission_amount == Decimal("1000")

    def test_csv_with_margin_override(self, tmp_path) -> None:
        """CSV with explicit margin column uses it directly."""
        csv = tmp_path / "deals.csv"
        csv.write_text(
            "id,payee_id,amount,period,gross profit\n"
            "D1,R1,0,2026-06,3750\n"
        )
        from icm_engine.loader import load_transactions
        txns, _ = load_transactions(str(csv))
        assert txns[0].margin == Decimal("3750")
        assert txns[0].margin_value == Decimal("3750")

    def test_csv_without_margin_columns_unaffected(self, tmp_path) -> None:
        """A normal amount-based CSV still works after margin changes."""
        csv = tmp_path / "deals.csv"
        csv.write_text(
            "id,payee_id,amount,period\n"
            "D1,R1,50000,2026-06\n"
        )
        from icm_engine.loader import load_transactions
        txns, _ = load_transactions(str(csv))
        assert txns[0].amount == Decimal("50000")
        assert txns[0].margin_value is None
        assert txns[0].bill_rate is None
        assert txns[0].pay_rate is None


class TestMarginTieredAccelerator:
    """Margin base for tiered + accelerator rules (intra-deal GP threshold splitting)."""

    def _payee(self, pid: str, quota: str) -> Payee:
        return Payee(id=pid, name=pid, quota=Decimal(quota),
                     plan_id="contract", effective_from=date.today())

    def test_tiered_slices_on_gp(self) -> None:
        """A deal whose margin crosses a quota threshold is split across tier rates."""
        from icm_engine.models import Tier, TieredRule
        plan = Plan(
            plan_id="contract", name="Contract Tiered", period_type="monthly", currency="USD",
            rules=[TieredRule(type="tiered", id="t", base="margin", tiers=[
                Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
                Tier(threshold_pct=Decimal("2.0"), rate=Decimal("0.10")),
            ])],
        )
        payees = [self._payee("R1", "10000")]
        txns = [Transaction(id="D1", payee_id="R1", amount=Decimal("0"),
                            period="2026-06", margin=Decimal("15000"))]
        result = CommissionEngine().calculate(plan, txns, payees)
        # 10,000 GP @ 5% = 500 ; next 5,000 GP @ 10% = 500 → 1,000
        total = sum((c.commission_amount for c in result.commissions), Decimal("0"))
        assert total == Decimal("1000")
        # base_amounts are margin slices that sum to the deal's GP
        assert sum((c.base_amount for c in result.commissions), Decimal("0")) == Decimal("15000")
        assert any(e.event_type == "tier_crossed" for e in result.ledger)

    def test_accelerator_on_gp(self) -> None:
        """Accelerator pays a multiplier on margin above the threshold."""
        from icm_engine.models import AcceleratorRule
        plan = Plan(
            plan_id="contract", name="Contract Accel", period_type="monthly", currency="USD",
            rules=[AcceleratorRule(type="accelerator", id="a", base="margin",
                                   rate=Decimal("0.10"), threshold_pct=Decimal("1.0"),
                                   multiplier=Decimal("2.0"))],
        )
        payees = [self._payee("R1", "10000")]
        txns = [Transaction(id="D1", payee_id="R1", amount=Decimal("0"),
                            period="2026-06", margin=Decimal("15000"))]
        result = CommissionEngine().calculate(plan, txns, payees)
        # threshold = 10,000 GP; above = 5,000 GP; 5,000 * 0.10 * 2.0 = 1,000
        total = sum((c.commission_amount for c in result.commissions), Decimal("0"))
        assert total == Decimal("1000")
        assert result.commissions[0].base_amount == Decimal("5000")

    def test_tiered_skips_no_margin_row(self) -> None:
        """A no-margin row is skipped (ledger), not a crash, and others still pay."""
        from icm_engine.models import Tier, TieredRule
        plan = Plan(
            plan_id="contract", name="C", period_type="monthly", currency="USD",
            rules=[TieredRule(type="tiered", id="t", base="margin",
                              tiers=[Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05"))])],
        )
        payees = [self._payee("R1", "10000")]
        txns = [
            Transaction(id="D1", payee_id="R1", amount=Decimal("0"),
                        period="2026-06", margin=Decimal("5000")),
            Transaction(id="D2", payee_id="R1", amount=Decimal("9999"), period="2026-06"),
        ]
        result = CommissionEngine().calculate(plan, txns, payees)  # must not raise
        total = sum((c.commission_amount for c in result.commissions), Decimal("0"))
        assert total == Decimal("250")  # 5,000 GP @ 5%
        assert any(
            e.event_type == "rule_skipped" and e.inputs.get("reason") == "no_margin_data"
            for e in result.ledger
        )

    def test_min_attainment_gate_uses_margin(self) -> None:
        """A margin rule's min_attainment gate is measured in margin, not amount.

        Both deals have amount=0, so an amount-based gate would pay nobody."""
        from icm_engine.models import Tier, TieredRule
        plan = Plan(
            plan_id="contract", name="C", period_type="monthly", currency="USD",
            rules=[TieredRule(type="tiered", id="t", base="margin",
                              min_attainment_pct=Decimal("0.5"),
                              tiers=[Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05"))])],
        )
        payees = [self._payee("A", "10000"), self._payee("B", "10000")]
        txns = [
            Transaction(id="D1", payee_id="A", amount=Decimal("0"),
                        period="2026-06", margin=Decimal("6000")),  # 60% → passes
            Transaction(id="D2", payee_id="B", amount=Decimal("0"),
                        period="2026-06", margin=Decimal("4000")),  # 40% → gated
        ]
        result = CommissionEngine().calculate(plan, txns, payees)
        paid = {c.payee_id for c in result.commissions}
        assert "A" in paid
        assert "B" not in paid
        assert any(
            e.event_type == "rule_skipped" and e.inputs.get("reason") == "below_threshold_gate"
            for e in result.ledger
        )

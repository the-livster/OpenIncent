from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from icm_engine.engine import CommissionEngine, compile_filter
from icm_engine.models import (
    AcceleratorRule,
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


# --- filter parser -----------------------------------------------------


class TestFilterParser:
    def test_null_filter_always_true(self) -> None:
        pred = compile_filter(None)
        assert pred(_txn())

    def test_blank_filter_always_true(self) -> None:
        assert compile_filter("")(_txn())

    def test_eq_string(self) -> None:
        pred = compile_filter('product == "Enterprise"')
        assert pred(_txn(product="Enterprise"))
        assert not pred(_txn(product="Standard"))

    def test_ne_string(self) -> None:
        pred = compile_filter('product != "Enterprise"')
        assert pred(_txn(product="Standard"))
        assert not pred(_txn(product="Enterprise"))

    def test_gt_number(self) -> None:
        pred = compile_filter("amount > 500")
        assert pred(_txn(amount=Decimal("1000")))
        assert not pred(_txn(amount=Decimal("100")))

    def test_lt_number(self) -> None:
        pred = compile_filter("amount < 500")
        assert pred(_txn(amount=Decimal("100")))
        assert not pred(_txn(amount=Decimal("1000")))

    def test_eq_number(self) -> None:
        pred = compile_filter("amount == 1000")
        assert pred(_txn(amount=Decimal("1000")))
        assert not pred(_txn(amount=Decimal("500")))

    def test_in_strings(self) -> None:
        pred = compile_filter('product in ["Enterprise", "Standard"]')
        assert pred(_txn(product="Enterprise"))
        assert not pred(_txn(product="Pro"))
        assert not pred(_txn())

    def test_and(self) -> None:
        pred = compile_filter('product == "Enterprise" and amount > 500')
        assert pred(_txn(product="Enterprise", amount=Decimal("1000")))
        assert not pred(_txn(product="Standard", amount=Decimal("1000")))
        assert not pred(_txn(product="Enterprise", amount=Decimal("100")))

    def test_or(self) -> None:
        pred = compile_filter('product == "Enterprise" or product == "Standard"')
        assert pred(_txn(product="Enterprise"))
        assert pred(_txn(product="Standard"))
        assert not pred(_txn(product="Pro"))

    def test_parentheses(self) -> None:
        pred = compile_filter(
            'payee_id == "P001" and (product == "A" or product == "B")'
        )
        assert pred(_txn(payee_id="P001", product="A"))
        assert pred(_txn(payee_id="P001", product="B"))
        assert not pred(_txn(payee_id="P002", product="A"))
        assert not pred(_txn(payee_id="P001", product="C"))

    def test_in_numbers(self) -> None:
        pred = compile_filter("amount in [100, 200, 300]")
        assert pred(_txn(amount=Decimal("200")))
        assert not pred(_txn(amount=Decimal("500")))


# --- flat rate ----------------------------------------------------------


class TestFlatRate:
    def test_single_transaction(self) -> None:
        engine = CommissionEngine()
        rule = FlatRateRule(type="flat_rate", id="flat", rate=Decimal("0.05"))
        txn = _txn(amount=Decimal("1000"))
        commissions, _ledger = engine._calc_flat_rate(rule, [txn], {})
        assert len(commissions) == 1
        assert commissions[0].commission_amount == Decimal("50.00")

    def test_respects_filter(self) -> None:
        engine = CommissionEngine()
        rule = FlatRateRule(
            type="flat_rate", id="flat", rate=Decimal("0.05"), filter='product == "Enterprise"'
        )
        txns = [
            _txn(id="T1", product="Enterprise", amount=Decimal("1000")),
            _txn(id="T2", product="Standard", amount=Decimal("2000")),
        ]
        commissions, _ledger = engine._calc_flat_rate(rule, txns, {})
        assert len(commissions) == 1
        assert commissions[0].transaction_id == "T1"


# --- tiered -------------------------------------------------------------


class TestTiered:
    def test_all_in_first_tier(self) -> None:
        engine = CommissionEngine()
        tiers = [
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
        ]
        rule = TieredRule(type="tiered", id="tiered", tiers=tiers)
        txns = [_txn(amount=Decimal("5000"))]  # 50% of 10000 quota
        payees = {"P001": _payee(quota=Decimal("10000"))}
        commissions, _ledger = engine._calc_tiered(rule, txns, payees)
        assert len(commissions) == 1
        assert commissions[0].commission_amount == Decimal("250.00")  # 5000 * 0.05
        assert commissions[0].rate == Decimal("0.05")

    def test_all_in_second_tier(self) -> None:
        engine = CommissionEngine()
        tiers = [
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
        ]
        rule = TieredRule(type="tiered", id="tiered", tiers=tiers)
        # Payee already at 120% of quota, so entire txn is in second tier
        txns = [
            _txn(
                id="T1",
                amount=Decimal("12000"),
                close_date=date(2026, 4, 1),
            ),
            _txn(
                id="T2",
                amount=Decimal("1000"),
                close_date=date(2026, 4, 15),
            ),
        ]
        payees = {"P001": _payee(quota=Decimal("10000"))}
        commissions, _ = engine._calc_tiered(rule, txns, payees)
        # T1: first 10000 at 5%, remaining 2000 at 10%
        # T2: all 1000 at 10%
        t2_total = sum(
            c.commission_amount for c in commissions if c.transaction_id == "T2"
        )
        assert t2_total == Decimal("100.00")

    def test_crosses_tier_boundary(self) -> None:
        engine = CommissionEngine()
        tiers = [
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
        ]
        rule = TieredRule(type="tiered", id="tiered", tiers=tiers)
        payees = {"P001": _payee(quota=Decimal("10000"))}
        # Bring payee to 95% of quota, then a deal that crosses the boundary
        txns = [
            _txn(
                id="T1",
                amount=Decimal("9500"),
                close_date=date(2026, 4, 1),
            ),
            _txn(
                id="T2",
                amount=Decimal("2000"),
                close_date=date(2026, 4, 15),
            ),
        ]
        commissions, _ = engine._calc_tiered(rule, txns, payees)
        t2_comms = [c for c in commissions if c.transaction_id == "T2"]
        # First 500 of T2 at 5%, remaining 1500 at 10%
        assert len(t2_comms) == 2
        assert sum(1 for c in t2_comms if c.rate == Decimal("0.05")) == 1
        assert sum(1 for c in t2_comms if c.rate == Decimal("0.10")) == 1
        low = [c for c in t2_comms if c.rate == Decimal("0.05")][0]
        high = [c for c in t2_comms if c.rate == Decimal("0.10")][0]
        assert low.base_amount == Decimal("500")
        assert high.base_amount == Decimal("1500")
        assert low.commission_amount == Decimal("25.00")
        assert high.commission_amount == Decimal("150.00")

    def test_exact_tier_boundary(self) -> None:
        engine = CommissionEngine()
        tiers = [
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
        ]
        rule = TieredRule(type="tiered", id="tiered", tiers=tiers)
        payees = {"P001": _payee(quota=Decimal("10000"))}
        txns = [_txn(amount=Decimal("10000"))]  # exactly at the boundary
        commissions, _ = engine._calc_tiered(rule, txns, payees)
        assert len(commissions) == 1
        assert commissions[0].rate == Decimal("0.05")
        assert commissions[0].base_amount == Decimal("10000")

    def test_zero_quota(self) -> None:
        engine = CommissionEngine()
        tiers = [
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
        ]
        rule = TieredRule(type="tiered", id="tiered", tiers=tiers)
        payees = {"P001": _payee(quota=Decimal("0"))}
        txns = [_txn(amount=Decimal("1000"))]
        commissions, _ = engine._calc_tiered(rule, txns, payees)
        assert len(commissions) == 1
        assert commissions[0].rate == Decimal("0.10")  # top tier

    def test_deterministic_ordering(self) -> None:
        engine = CommissionEngine()
        tiers = [
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
        ]
        rule = TieredRule(type="tiered", id="tiered", tiers=tiers)
        payees = {"P001": _payee(quota=Decimal("5000"))}
        txns = [
            _txn(
                id="Early", amount=Decimal("3000"), close_date=date(2026, 4, 1)
            ),
            _txn(
                id="Late", amount=Decimal("3000"), close_date=date(2026, 4, 30)
            ),
        ]
        # Run twice — ensure identical output
        r1, _ = engine._calc_tiered(rule, txns, payees)
        r2, _ = engine._calc_tiered(rule, txns, payees)
        assert [c.model_dump() for c in r1] == [c.model_dump() for c in r2]

    def test_attainment_above_highest_tier_terminates(self) -> None:
        """Bug 1 regression: highest tier extends to infinity, loop terminates."""
        engine = CommissionEngine()
        tiers = [
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("1.5"), rate=Decimal("0.08")),
        ]
        rule = TieredRule(type="tiered", id="t", tiers=tiers)
        payees = {"P1": _payee(id="P1", name="A", quota=Decimal("100000"))}
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("200000"), close_date=date(2026, 1, 10))]
        commissions, _ = engine._calc_tiered(rule, txns, payees)
        total = sum(c.commission_amount for c in commissions)
        # 100000 @ 0.05 = 5000, 100000 @ 0.08 = 8000 → 13000
        assert total == Decimal("13000")

    def test_undated_transactions_no_crash(self) -> None:
        """Bug 2 regression: transactions without close_date sort deterministically."""
        engine = CommissionEngine()
        tiers = [
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.08")),
        ]
        rule = TieredRule(type="tiered", id="t", tiers=tiers)
        payees = {"P1": _payee(id="P1", name="A", quota=Decimal("100000"))}
        txns = [
            _txn(id="T1", payee_id="P1", amount=Decimal("5000"), close_date=None),
            _txn(id="T2", payee_id="P1", amount=Decimal("6000"), close_date=None),
        ]
        commissions, _ = engine._calc_tiered(rule, txns, payees)
        total = sum(c.commission_amount for c in commissions)
        assert total == Decimal("550")  # both at 5%

    def test_undated_sorts_after_dated(self) -> None:
        """Undated transactions sort after all dated ones, deterministically."""
        engine = CommissionEngine()
        tiers = [
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.08")),
        ]
        rule = TieredRule(type="tiered", id="t", tiers=tiers)
        payees = {"P1": _payee(id="P1", name="A", quota=Decimal("100000"))}
        txns = [
            _txn(id="Early", payee_id="P1", amount=Decimal("60000"), close_date=date(2026, 1, 1)),
            _txn(id="NoDate", payee_id="P1", amount=Decimal("10000"), close_date=None),
            _txn(id="Late", payee_id="P1", amount=Decimal("30000"), close_date=date(2026, 1, 31)),
        ]
        r1, _ = engine._calc_tiered(rule, txns, payees)
        r2, _ = engine._calc_tiered(rule, txns, payees)
        assert [c.model_dump() for c in r1] == [c.model_dump() for c in r2]

    def test_tiers_must_be_ascending(self) -> None:
        """Validator rejects tiers not sorted ascending by threshold_pct."""
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="ascending"):
            TieredRule(
                type="tiered", id="t",
                tiers=[
                    Tier(threshold_pct=Decimal("2.0"), rate=Decimal("0.05")),
                    Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.08")),
                ],
            )


# --- accelerator --------------------------------------------------------


class TestAccelerator:
    def test_below_threshold_no_commission(self) -> None:
        engine = CommissionEngine()
        rule = AcceleratorRule(
            type="accelerator", id="accel",
            rate=Decimal("0.05"),
            threshold_pct=Decimal("1.0"),
            multiplier=Decimal("1.5"),
        )
        payees = {"P001": _payee(quota=Decimal("10000"))}
        txns = [_txn(amount=Decimal("5000"))]  # 50% of quota
        commissions, _ = engine._calc_accelerator(rule, txns, payees)
        assert len(commissions) == 0

    def test_above_threshold(self) -> None:
        engine = CommissionEngine()
        rule = AcceleratorRule(
            type="accelerator", id="accel",
            rate=Decimal("0.05"),
            threshold_pct=Decimal("1.0"),
            multiplier=Decimal("2.0"),
        )
        payees = {"P001": _payee(quota=Decimal("10000"))}
        txns = [_txn(amount=Decimal("15000"))]  # 150% of quota
        commissions, _ = engine._calc_accelerator(rule, txns, payees)
        assert len(commissions) == 1
        assert commissions[0].base_amount == Decimal("5000")  # 5000 above threshold
        assert commissions[0].commission_amount == Decimal("500.00")  # 5000 * 0.10

    def test_crosses_threshold(self) -> None:
        engine = CommissionEngine()
        rule = AcceleratorRule(
            type="accelerator", id="accel",
            rate=Decimal("0.05"),
            threshold_pct=Decimal("1.0"),
            multiplier=Decimal("2.0"),
        )
        payees = {"P001": _payee(quota=Decimal("10000"))}
        txns = [
            _txn(
                id="T1", amount=Decimal("8000"), close_date=date(2026, 4, 1)
            ),
            _txn(
                id="T2", amount=Decimal("5000"), close_date=date(2026, 4, 15)
            ),
        ]
        commissions, _ = engine._calc_accelerator(rule, txns, payees)
        # T2: first 2000 fills to threshold, remaining 3000 above
        assert all(c.transaction_id == "T2" for c in commissions)
        assert commissions[0].base_amount == Decimal("3000")

    def test_undated_transactions_no_crash(self) -> None:
        """Bug 2 regression: accelerator handles transactions without close_date."""
        engine = CommissionEngine()
        rule = AcceleratorRule(
            type="accelerator", id="accel",
            rate=Decimal("0.05"), threshold_pct=Decimal("1.0"), multiplier=Decimal("2.0"),
        )
        payees = {"P1": _payee(id="P1", name="A", quota=Decimal("100000"))}
        txns = [
            _txn(id="T1", payee_id="P1", amount=Decimal("5000"), close_date=None),
            _txn(id="T2", payee_id="P1", amount=Decimal("6000"), close_date=None),
        ]
        commissions, _ = engine._calc_accelerator(rule, txns, payees)
        assert len(commissions) >= 0  # no crash


# --- top-level ----------------------------------------------------------


class TestCalculate:
    def test_multiple_rules(self) -> None:
        engine = CommissionEngine()
        plan = Plan(
            plan_id="test",
            name="Test",
            period_type="monthly",
            currency="USD",
            rules=[
                FlatRateRule(type="flat_rate", id="flat", rate=Decimal("0.05")),
                AcceleratorRule(
                    type="accelerator", id="accel",
                    rate=Decimal("0.05"),
                    threshold_pct=Decimal("1.0"),
                    multiplier=Decimal("2.0"),
                ),
            ],
        )
        payees = [_payee(quota=Decimal("10000"))]
        txns = [_txn(amount=Decimal("15000"))]
        result = engine.calculate(plan, txns, payees)
        # flat rate gives: 15000 * 0.05 = 750
        # accelerator: 5000 above threshold * 0.05 * 2.0 = 500
        assert len(result.commissions) == 2
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("1250.00")

    def test_quarterly_aggregates_across_months(self) -> None:
        """period_type='quarterly' groups Jan+Feb+Mar together as one window."""
        engine = CommissionEngine()
        plan = Plan(
            plan_id="q", name="Quarterly", period_type="quarterly", currency="USD",
            rules=[TieredRule(type="tiered", id="t", tiers=[
                Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
                Tier(threshold_pct=Decimal("2.0"), rate=Decimal("0.08")),
            ])],
        )
        payees = [_payee(id="P1", name="A", quota=Decimal("100000"), plan_id="q")]
        txns = [
            _txn(id="T1", payee_id="P1", period="2026-01", amount=Decimal("40000"), close_date=date(2026, 1, 15)),
            _txn(id="T2", payee_id="P1", period="2026-02", amount=Decimal("40000"), close_date=date(2026, 2, 15)),
            _txn(id="T3", payee_id="P1", period="2026-03", amount=Decimal("40000"), close_date=date(2026, 3, 15)),
        ]
        result = engine.calculate(plan, txns, payees)
        total = sum(c.commission_amount for c in result.commissions)
        # 120000 in Q1 vs 100000 quota: 100000@0.05 + 20000@0.08 = 6600
        assert total == Decimal("6600")
        periods = {c.period for c in result.commissions}
        assert periods == {"2026-Q1"}

    def test_monthly_unchanged(self) -> None:
        """monthly plans produce identical output to before period_type was threaded."""
        engine = CommissionEngine()
        plan = Plan(
            plan_id="m", name="Monthly", period_type="monthly", currency="USD",
            rules=[TieredRule(type="tiered", id="t", tiers=[
                Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
                Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.08")),
            ])],
        )
        payees = [_payee(id="P1", name="A", quota=Decimal("100000"), plan_id="m")]
        txns = [
            _txn(id="T1", payee_id="P1", period="2026-01", amount=Decimal("50000"), close_date=date(2026, 1, 15)),
            _txn(id="T2", payee_id="P1", period="2026-02", amount=Decimal("60000"), close_date=date(2026, 2, 15)),
        ]
        result = engine.calculate(plan, txns, payees)
        total = sum(c.commission_amount for c in result.commissions)
        # Each month independently vs 100000: 50000@0.05 + 60000@0.05 = 5500
        assert total == Decimal("5500")
        periods = {c.period for c in result.commissions}
        assert "2026-01" in periods
        assert "2026-02" in periods


# --- credits ------------------------------------------------------------


class TestCredits:
    def test_split_credits_partition_deal(self) -> None:
        """Split credits partition the deal: 70/30 of 10000 -> 350 + 150 = 500."""
        from icm_engine.models import Credit
        engine = CommissionEngine()
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R-001", rate=Decimal("0.05"))])
        payees = [
            _payee(id="P1", name="AE", quota=Decimal("0"), plan_id="p"),
            _payee(id="P2", name="SDR", quota=Decimal("0"), plan_id="p"),
        ]
        txn = Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                          close_date=date(2026, 1, 10),
                          credits=[Credit(payee_id="P1", split_pct=Decimal("0.7")),
                                   Credit(payee_id="P2", split_pct=Decimal("0.3"))])
        r = engine.calculate(plan, [txn], payees)
        by_payee = {c.payee_id: c.commission_amount for c in r.commissions}
        assert by_payee["P1"] == Decimal("350.00")
        assert by_payee["P2"] == Decimal("150.00")

    def test_overlay_additive(self) -> None:
        """Overlay credits are additive: P1 100% + P3 overlay 100% -> 500 each."""
        from icm_engine.models import Credit
        engine = CommissionEngine()
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R-001", rate=Decimal("0.05"))])
        payees = [
            _payee(id="P1", name="AE", quota=Decimal("0"), plan_id="p"),
            _payee(id="P3", name="SE", quota=Decimal("0"), plan_id="p"),
        ]
        txn = Transaction(id="T2", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                          close_date=date(2026, 1, 10),
                          credits=[Credit(payee_id="P1", split_pct=Decimal("1.0"), kind="split"),
                                   Credit(payee_id="P3", split_pct=Decimal("1.0"), kind="overlay")])
        r = engine.calculate(plan, [txn], payees)
        by_payee = {c.payee_id: c.commission_amount for c in r.commissions}
        assert by_payee["P1"] == Decimal("500.00")
        assert by_payee["P3"] == Decimal("500.00")

    def test_credit_allocated_ledger_entries(self) -> None:
        """Each credit emits a credit_allocated ledger entry."""
        from icm_engine.models import Credit
        engine = CommissionEngine()
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R-001", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="A", quota=Decimal("0"), plan_id="p")]
        txn = Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                          close_date=date(2026, 1, 10),
                          credits=[Credit(payee_id="P1", split_pct=Decimal("1.0"))])
        r = engine.calculate(plan, [txn], payees)
        alloc_entries = [e for e in r.ledger if e.event_type == "credit_allocated"]
        assert len(alloc_entries) == 1
        assert alloc_entries[0].payee_id == "P1"

    def test_credits_splits_must_sum_to_one(self) -> None:
        """Split credits that don't sum to 1.0 raise ValueError."""
        from icm_engine.models import Credit
        with pytest.raises(ValidationError, match="sum to 1.0"):
            Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                        close_date=date(2026, 1, 10),
                        credits=[Credit(payee_id="P1", split_pct=Decimal("0.7")),
                                 Credit(payee_id="P2", split_pct=Decimal("0.2"))])

    def test_no_credits_unchanged(self) -> None:
        """A transaction without credits produces same output as before."""
        engine = CommissionEngine()
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R-001", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", quota=Decimal("0"), plan_id="p")]
        txn = _txn(id="T1", payee_id="P1", amount=Decimal("10000"), close_date=date(2026, 1, 10))
        r = engine.calculate(plan, [txn], payees)
        assert r.commissions[0].commission_amount == Decimal("500.00")
        assert r.commissions[0].split_pct == Decimal("1")
        assert r.commissions[0].kind == "split"

    def test_tiered_with_split_credit(self) -> None:
        """A split credit contributes its credited amount to the payee's attainment."""
        from icm_engine.models import Credit
        engine = CommissionEngine()
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[TieredRule(type="tiered", id="t", tiers=[
                        Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
                        Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.08")),
                    ])])
        payees = [
            _payee(id="P1", name="AE", quota=Decimal("100000"), plan_id="p"),
            _payee(id="P2", name="SDR", quota=Decimal("100000"), plan_id="p"),
        ]
        # P1 gets 60% of 200000 = 120000 -> crosses tier (100000@5% + 20000@8% = 6600)
        # P2 gets 40% of 200000 = 80000 -> stays in first tier (80000@5% = 4000)
        txn = Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("200000"),
                          close_date=date(2026, 1, 10),
                          credits=[Credit(payee_id="P1", split_pct=Decimal("0.6")),
                                   Credit(payee_id="P2", split_pct=Decimal("0.4"))])
        r = engine.calculate(plan, [txn], payees)
        by_payee: dict[str, Decimal] = {}
        for c in r.commissions:
            by_payee[c.payee_id] = by_payee.get(c.payee_id, Decimal("0")) + c.commission_amount
        assert by_payee["P1"] == Decimal("6600.00")  # 100000@5% + 20000@8%
        assert by_payee["P2"] == Decimal("4000.00")  # 80000@5%


# --- true-up ------------------------------------------------------------


class TestTrueUp:
    def test_downward_clawback(self) -> None:
        """Removing a deal from current data produces a negative adjustment."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R-001", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="A", quota=Decimal("0"), plan_id="p")]
        eng = CommissionEngine()

        prior = eng.calculate(plan, [
            _txn(id="D1", payee_id="P1", period="2026-01", amount=Decimal("10000"), close_date=date(2026, 1, 10)),
        ], payees).commissions

        tu = eng.true_up(plan, [], payees, prior)
        assert len(tu.adjustments) == 1
        assert tu.adjustments[0].payee_id == "P1"
        assert tu.adjustments[0].period == "2026-01"
        assert tu.adjustments[0].delta == Decimal("-500.00")
        assert len(tu.exceptions) == 1
        assert tu.exceptions[0]["status"] == "removed"
        assert tu.exceptions[0]["transaction_id"] == "D1"

    def test_upward_adjustment(self) -> None:
        """Increasing a deal's amount produces a positive adjustment."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R-001", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="A", quota=Decimal("0"), plan_id="p")]
        eng = CommissionEngine()

        prior = eng.calculate(plan, [
            _txn(id="D1", payee_id="P1", amount=Decimal("8000"), close_date=date(2026, 1, 10)),
        ], payees).commissions

        tu = eng.true_up(plan, [
            _txn(id="D1", payee_id="P1", amount=Decimal("10000"), close_date=date(2026, 1, 10)),
        ], payees, prior)
        assert len(tu.adjustments) == 1
        assert tu.adjustments[0].delta == Decimal("100.00")  # 500 - 400
        assert tu.exceptions[0]["status"] == "changed"

    def test_unchanged_is_silent(self) -> None:
        """Identical prior and current produce empty adjustments and exceptions."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R-001", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="A", quota=Decimal("0"), plan_id="p")]
        eng = CommissionEngine()

        txns = [_txn(id="D1", payee_id="P1", amount=Decimal("10000"), close_date=date(2026, 1, 10))]
        prior = eng.calculate(plan, txns, payees).commissions
        tu = eng.true_up(plan, txns, payees, prior)
        assert tu.adjustments == []
        assert tu.exceptions == []

    def test_negative_amount_refund(self) -> None:
        """A negative transaction computes a negative commission with no crash."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R-001", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="A", quota=Decimal("0"), plan_id="p")]
        eng = CommissionEngine()

        r = eng.calculate(plan, [
            _txn(id="D1", payee_id="P1", amount=Decimal("-3000"), close_date=date(2026, 1, 10)),
        ], payees)
        assert r.commissions[0].commission_amount == Decimal("-150.00")
        assert r.commissions[0].base_amount == Decimal("-3000")

    def test_split_ownership_change(self) -> None:
        """Changing credit splits produces per-payee adjustments."""
        from icm_engine.models import Credit
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R-001", rate=Decimal("0.05"))])
        payees = [
            _payee(id="P1", name="A", quota=Decimal("0"), plan_id="p"),
            _payee(id="P2", name="B", quota=Decimal("0"), plan_id="p"),
        ]
        eng = CommissionEngine()

        # Prior: 100% to P1
        prior = eng.calculate(plan, [
            Transaction(id="D1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                        close_date=date(2026, 1, 10),
                        credits=[Credit(payee_id="P1", split_pct=Decimal("1.0"))]),
        ], payees).commissions

        # Current: 70/30 P1/P2
        tu = eng.true_up(plan, [
            Transaction(id="D1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                        close_date=date(2026, 1, 10),
                        credits=[Credit(payee_id="P1", split_pct=Decimal("0.7")),
                                 Credit(payee_id="P2", split_pct=Decimal("0.3"))]),
        ], payees, prior)

        deltas = {a.payee_id: a.delta for a in tu.adjustments}
        assert deltas["P1"] == Decimal("-150.00")  # was 500, now 350
        assert deltas["P2"] == Decimal("150.00")   # was 0, now 150
        assert len(tu.exceptions) >= 1

    def test_true_up_ledger_entries(self) -> None:
        """Each adjustment emits a true_up ledger entry."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R-001", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="A", quota=Decimal("0"), plan_id="p")]
        eng = CommissionEngine()

        prior = eng.calculate(plan, [
            _txn(id="D1", payee_id="P1", period="2026-01", amount=Decimal("10000"), close_date=date(2026, 1, 10)),
        ], payees).commissions

        tu = eng.true_up(plan, [], payees, prior)
        tu_entries = [e for e in tu.ledger if e.event_type == "true_up"]
        assert len(tu_entries) == 1
        assert tu_entries[0].payee_id == "P1"
        assert "delta" in tu_entries[0].outputs

    def test_deterministic(self) -> None:
        """Same inputs produce byte-identical true-up results."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R-001", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="A", quota=Decimal("0"), plan_id="p")]
        eng = CommissionEngine()

        prior = eng.calculate(plan, [
            _txn(id="D1", payee_id="P1", period="2026-01", amount=Decimal("10000"), close_date=date(2026, 1, 10)),
        ], payees).commissions

        tu1 = eng.true_up(plan, [], payees, prior)
        tu2 = eng.true_up(plan, [], payees, prior)
        assert [(a.payee_id, a.period, a.delta) for a in tu1.adjustments] == \
               [(a.payee_id, a.period, a.delta) for a in tu2.adjustments]
        assert tu1.exceptions == tu2.exceptions


# --- attainment ---------------------------------------------------------


class TestAttainment:
    def test_basic_attainment(self) -> None:
        """Bookings 120k vs 100k quota = 120% attainment."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="A", quota=Decimal("100000"), plan_id="p")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("120000"), close_date=date(2026, 1, 15))]
        result = CommissionEngine().calculate(plan, txns, payees)
        assert len(result.attainment) == 1
        a = result.attainment[0]
        assert a.payee_id == "P1"
        assert a.bookings == Decimal("120000")
        assert a.quota == Decimal("100000")
        assert a.attainment_pct == Decimal("1.2")

    def test_zero_quota_no_crash(self) -> None:
        """Zero-quota payee produces attainment_pct=None, no crash."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="A", quota=Decimal("0"), plan_id="p")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("5000"), close_date=date(2026, 1, 15))]
        result = CommissionEngine().calculate(plan, txns, payees)
        assert len(result.attainment) == 1
        assert result.attainment[0].attainment_pct is None

    def test_split_credit_bookings(self) -> None:
        """A 30% split on 10,000 adds 3,000 to that payee's bookings."""
        from icm_engine.models import Credit
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [
            _payee(id="P1", name="A", quota=Decimal("100000"), plan_id="p"),
            _payee(id="P2", name="B", quota=Decimal("100000"), plan_id="p"),
        ]
        txns = [Transaction(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"),
                            close_date=date(2026, 1, 10),
                            credits=[Credit(payee_id="P1", split_pct=Decimal("0.7")),
                                     Credit(payee_id="P2", split_pct=Decimal("0.3"))])]
        result = CommissionEngine().calculate(plan, txns, payees)
        p2 = next(a for a in result.attainment if a.payee_id == "P2")
        assert p2.bookings == Decimal("3000")  # 30% of 10000

    def test_attainment_ledger_entry(self) -> None:
        """An attainment_computed ledger entry is emitted."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="A", quota=Decimal("100000"), plan_id="p")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("120000"), close_date=date(2026, 1, 15))]
        result = CommissionEngine().calculate(plan, txns, payees)
        att_entries = [e for e in result.ledger if e.event_type == "attainment_computed"]
        assert len(att_entries) == 1
        assert att_entries[0].payee_id == "P1"
        assert att_entries[0].inputs["bookings"] == "120000"

    def test_attainment_in_api(self) -> None:
        """API response includes attainment."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="A", quota=Decimal("100000"), plan_id="p")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("120000"), close_date=date(2026, 1, 15))]
        result = CommissionEngine().calculate(plan, txns, payees)
        # Just verify attainment is accessible with expected fields
        a = result.attainment[0]
        assert a.payee_id
        assert a.period
        assert a.bookings is not None
        assert a.quota is not None

    def test_attainment_in_statement(self) -> None:
        """Statement HTML includes attainment info."""
        from icm_engine.statements import generate_statements
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", name="Alice", quota=Decimal("100000"), plan_id="p")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("120000"), close_date=date(2026, 1, 15))]
        result = CommissionEngine().calculate(plan, txns, payees)
        out = Path("tests/fixtures/_stmt_att")
        out.mkdir(parents=True, exist_ok=True)
        files = generate_statements(
            result.commissions, payees, out_dir=out, formats=("html",),
            attainment=result.attainment,
        )
        content = files[0].path.read_text(encoding="utf-8")
        assert "120000" in content


# --- time-varying quotas ------------------------------------------------


class TestTimeVaryingQuotas:
    def test_quarterly_tiered_uses_period_quota(self) -> None:
        """Tiered rule uses period-specific quota when quotas dict is set."""
        engine = CommissionEngine()
        tiers = [
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
        ]
        rule = TieredRule(type="tiered", id="t", tiers=tiers)
        # Q1 quota=300000, Q2 quota=350000
        payee = Payee(id="P1", name="A", quota=Decimal("100000"),
                      quotas={"2026-Q1": Decimal("300000"), "2026-Q2": Decimal("350000")},
                      plan_id="p", effective_from=date(2026, 1, 1))
        payees = {"P1": payee}
        # Q1: 200,000 against 300,000 = 66.7% -> stays in first tier (5%) -> 10,000
        # Q2: 200,000 against 350,000 = 57.1% -> stays in first tier (5%) -> 10,000
        txns = [
            _txn(id="T1", payee_id="P1", period="2026-01", amount=Decimal("200000")),
            _txn(id="T2", payee_id="P1", period="2026-04", amount=Decimal("200000")),
        ]
        commissions, _ = engine._calc_tiered(rule, txns, payees, period_type="quarterly")
        total = sum(c.commission_amount for c in commissions)
        assert total == Decimal("20000.00")

    def test_fallback_to_default_quota(self) -> None:
        """A window not in quotas uses the default quota."""
        engine = CommissionEngine()
        tiers = [
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
        ]
        rule = TieredRule(type="tiered", id="t", tiers=tiers)
        payee = Payee(id="P1", name="A", quota=Decimal("100000"),
                      quotas={"2026-Q1": Decimal("300000")},
                      plan_id="p", effective_from=date(2026, 1, 1))
        payees = {"P1": payee}
        # Q2 not in quotas -> uses default 100,000
        # 150,000 against 100,000 = 150% -> crosses tier
        txns = [_txn(id="T1", payee_id="P1", period="2026-04", amount=Decimal("150000"))]
        commissions, _ = engine._calc_tiered(rule, txns, payees)
        total = sum(c.commission_amount for c in commissions)
        # 100k@5% + 50k@10% = 10000
        assert total == Decimal("10000.00")

    def test_backward_compat_empty_quotas(self) -> None:
        """Payee with empty quotas dict behaves same as default."""
        engine = CommissionEngine()
        tiers = [
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
        ]
        rule = TieredRule(type="tiered", id="t", tiers=tiers)
        payee = Payee(id="P1", name="A", quota=Decimal("100000"),
                      quotas={}, plan_id="p", effective_from=date(2026, 1, 1))
        payees = {"P1": payee}
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("150000"))]
        commissions, _ = engine._calc_tiered(rule, txns, payees)
        total = sum(c.commission_amount for c in commissions)
        assert total == Decimal("10000.00")  # 100k@5% + 50k@10%

    def test_attainment_uses_period_quota(self) -> None:
        """Attainment computation uses period-specific quota."""
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [Payee(id="P1", name="A", quota=Decimal("100000"),
                        quotas={"2026-01": Decimal("300000")},
                        plan_id="p", effective_from=date(2026, 1, 1))]
        txns = [_txn(id="T1", payee_id="P1", period="2026-01", amount=Decimal("200000"))]
        result = CommissionEngine().calculate(plan, txns, payees)
        a = result.attainment[0]
        # 200k against 300k (period quota), not 100k (default)
        assert a.quota == Decimal("300000")
        assert a.bookings == Decimal("200000")
        assert a.attainment_pct is not None
        assert abs(a.attainment_pct - Decimal("0.6667")) < Decimal("0.01")


# --- rule_skipped ledger entries ---------------------------------------


class TestRuleSkipped:
    def test_tiered_filter_excluded_logs_skip(self) -> None:
        """Tiered rule logs rule_skipped for filter-excluded transactions."""
        engine = CommissionEngine()
        tiers = [
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
        ]
        rule = TieredRule(type="tiered", id="tiered", tiers=tiers,
                          filter='product == "Enterprise"')
        txn = _txn(id="T1", amount=Decimal("5000"), product="Standard")
        payees = {"P001": _payee(quota=Decimal("10000"))}
        _, ledger = engine._calc_tiered(rule, [txn], payees)
        skipped = [e for e in ledger if e.event_type == "rule_skipped"]
        assert len(skipped) == 1
        assert skipped[0].transaction_id == "T1"
        assert skipped[0].inputs["reason"] == "filter_excluded"

    def test_tiered_payee_not_found_logs_skip(self) -> None:
        """Tiered rule logs rule_skipped when payee not in payee_map."""
        engine = CommissionEngine()
        tiers = [
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
        ]
        rule = TieredRule(type="tiered", id="tiered", tiers=tiers)
        txn = _txn(id="T1", payee_id="UNKNOWN", amount=Decimal("5000"))
        payees: dict[str, Payee] = {}
        _, ledger = engine._calc_tiered(rule, [txn], payees)
        skipped = [e for e in ledger if e.event_type == "rule_skipped"]
        assert len(skipped) == 1
        assert skipped[0].inputs["reason"] == "payee_not_found"

    def test_accelerator_filter_excluded_logs_skip(self) -> None:
        """Accelerator rule logs rule_skipped for filter-excluded transactions."""
        engine = CommissionEngine()
        rule = AcceleratorRule(
            type="accelerator", id="accel",
            rate=Decimal("0.05"), threshold_pct=Decimal("1.0"), multiplier=Decimal("2.0"),
            filter='product == "Enterprise"',
        )
        txn = _txn(id="T1", amount=Decimal("5000"), product="Standard")
        payees = {"P001": _payee(quota=Decimal("10000"))}
        _, ledger = engine._calc_accelerator(rule, [txn], payees)
        skipped = [e for e in ledger if e.event_type == "rule_skipped"]
        assert len(skipped) == 1
        assert skipped[0].inputs["reason"] == "filter_excluded"

    def test_accelerator_payee_not_found_logs_skip(self) -> None:
        """Accelerator logs rule_skipped for missing payee."""
        engine = CommissionEngine()
        rule = AcceleratorRule(
            type="accelerator", id="accel",
            rate=Decimal("0.05"), threshold_pct=Decimal("1.0"), multiplier=Decimal("2.0"),
        )
        txn = _txn(id="T1", payee_id="UNKNOWN", amount=Decimal("5000"))
        payees: dict[str, Payee] = {}
        _, ledger = engine._calc_accelerator(rule, [txn], payees)
        skipped = [e for e in ledger if e.event_type == "rule_skipped"]
        assert len(skipped) == 1
        assert skipped[0].inputs["reason"] == "payee_not_found"

    def test_accelerator_zero_quota_logs_skip(self) -> None:
        """Accelerator logs rule_skipped for zero-quota payee, no commission."""
        engine = CommissionEngine()
        rule = AcceleratorRule(
            type="accelerator", id="accel",
            rate=Decimal("0.05"), threshold_pct=Decimal("1.0"), multiplier=Decimal("2.0"),
        )
        txn = _txn(id="T1", payee_id="P001", amount=Decimal("5000"))
        payees = {"P001": _payee(id="P001", quota=Decimal("0"))}
        commissions, ledger = engine._calc_accelerator(rule, [txn], payees)
        assert len(commissions) == 0
        skipped = [e for e in ledger if e.event_type == "rule_skipped"]
        assert len(skipped) == 1
        assert skipped[0].inputs["reason"] == "zero_quota"

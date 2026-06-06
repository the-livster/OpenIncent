from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from icm_engine.engine import (
    CommissionEngine,
    check_filter_fields,
    compile_filter,
)
from icm_engine.models import (
    AcceleratorRule,
    FlatRateRule,
    Payee,
    Plan,
    RampSchedule,
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

    # -- metadata field resolution ---------------------------------------

    def test_metadata_string_eq(self) -> None:
        """Filter on metadata column: region == 'EMEA'."""
        pred = compile_filter('region == "EMEA"')
        t = _txn(metadata={"region": "EMEA"})
        assert pred(t)
        assert not pred(_txn(metadata={"region": "APAC"}))

    def test_metadata_string_in(self) -> None:
        """Filter: metadata column with 'in'."""
        pred = compile_filter('deal_type in ["perm", "contract"]')
        t = _txn(metadata={"deal_type": "perm"})
        assert pred(t)
        assert not pred(_txn(metadata={"deal_type": "consulting"}))

    def test_metadata_numeric_gt(self) -> None:
        """Metadata string '3' coerced to number: tier > 2."""
        pred = compile_filter("tier > 2")
        assert pred(_txn(metadata={"tier": "3"}))
        assert not pred(_txn(metadata={"tier": "1"}))

    def test_metadata_decimal_ge(self) -> None:
        """Metadata decimal coercion: gp_margin >= 0.3."""
        pred = compile_filter("gp_margin >= 0.3")
        assert pred(_txn(metadata={"gp_margin": "0.35"}))
        assert not pred(_txn(metadata={"gp_margin": "0.25"}))

    def test_metadata_numeric_in(self) -> None:
        """Metadata numeric coercion in 'in' list."""
        pred = compile_filter("tier in [1, 2, 3]")
        assert pred(_txn(metadata={"tier": "2"}))
        assert not pred(_txn(metadata={"tier": "4"}))

    # -- canonical field still works ------------------------------------

    def test_canonical_product_eq(self) -> None:
        """product == 'Enterprise' still works unchanged."""
        pred = compile_filter('product == "Enterprise"')
        assert pred(_txn(product="Enterprise"))
        assert not pred(_txn(product="Standard"))

    def test_canonical_amount_gt(self) -> None:
        """amount > 5000 still works unchanged."""
        pred = compile_filter("amount > 5000")
        assert pred(_txn(amount=Decimal("10000")))
        assert not pred(_txn(amount=Decimal("1000")))

    def test_canonical_overrides_metadata(self) -> None:
        """Canonical field takes precedence over metadata of same name."""
        pred = compile_filter("amount == 500")
        t = _txn(amount=Decimal("1000"), metadata={"amount": "500"})
        assert not pred(t)  # canonical amount (1000) != 500

    # -- missing field → always False -----------------------------------

    def test_missing_field_eq_always_false(self) -> None:
        """Field absent from both canonical and metadata → False even for ==."""
        pred = compile_filter('status != "void"')
        t = _txn(metadata={"region": "EMEA"})  # no status field
        assert not pred(t)

    def test_missing_field_in_always_false(self) -> None:
        """Missing field in 'in' → False."""
        pred = compile_filter('unknown_col in ["x", "y"]')
        assert not pred(_txn())

    def test_missing_field_ne_always_false(self) -> None:
        """Missing field tested with != → False (never-matched-field rule)."""
        pred = compile_filter('missing_field == "x"')
        assert not pred(_txn())

    # -- combined metadata + canonical ----------------------------------

    def test_mixed_canonical_and_metadata(self) -> None:
        """Canonical and metadata fields can be mixed in a filter."""
        pred = compile_filter('product == "Enterprise" and region == "EMEA"')
        assert pred(_txn(product="Enterprise", metadata={"region": "EMEA"}))
        assert not pred(_txn(product="Enterprise", metadata={"region": "APAC"}))
        assert not pred(_txn(product="Standard", metadata={"region": "EMEA"}))

    # -- date comparisons -----------------------------------------------

    def test_close_date_gt(self) -> None:
        """close_date >= '2026-04-01' works (date comparison from canonical)."""
        pred = compile_filter("close_date >= '2026-04-01'")
        assert pred(_txn(close_date=date(2026, 4, 15)))
        assert not pred(_txn(close_date=date(2026, 3, 1)))

    def test_metadata_date_eq(self) -> None:
        """Metadata date string compared as date."""
        pred = compile_filter("sign_date == '2026-04-15'")
        assert pred(_txn(metadata={"sign_date": "2026-04-15"}))
        assert not pred(_txn(metadata={"sign_date": "2026-04-16"}))

    # -- backtick-quoted field names ------------------------------------

    def test_backtick_quoted_field(self) -> None:
        """`Deal Type` field with spaces works via backtick quoting."""
        pred = compile_filter('`Deal Type` == "Perm"')
        assert pred(_txn(metadata={"Deal Type": "Perm"}))
        assert not pred(_txn(metadata={"Deal Type": "Contract"}))

    def test_backtick_numeric(self) -> None:
        """`GP %` >= 0.3 with backtick-quoted field."""
        pred = compile_filter("`GP %` >= 0.3")
        assert pred(_txn(metadata={"GP %": "0.35"}))
        assert not pred(_txn(metadata={"GP %": "0.25"}))

    # -- typo guard -----------------------------------------------------

    def test_typo_guard_catches_unused(self) -> None:
        """check_filter_fields returns fields that appear in no transaction."""
        from icm_engine.models import Transaction as Txn
        txns = [Txn(id="T1", payee_id="P1", period="2026-01", amount=Decimal("100"),
                    metadata={"region": "EMEA"})]
        unused = check_filter_fields('nonexistent_column == "x"', txns)
        assert "nonexistent_column" in unused

    def test_typo_guard_ok_for_canonical(self) -> None:
        """Canonical fields are never flagged by the typo guard."""
        from icm_engine.models import Transaction as Txn
        txns = [Txn(id="T1", payee_id="P1", period="2026-01", amount=Decimal("100"))]
        unused = check_filter_fields("amount > 50", txns)
        assert "amount" not in unused

    def test_typo_guard_ok_for_present_metadata(self) -> None:
        """A metadata field that exists in at least one txn is not flagged."""
        from icm_engine.models import Transaction as Txn
        txns = [Txn(id="T1", payee_id="P1", period="2026-01", amount=Decimal("100"),
                    metadata={"region": "EMEA"})]
        unused = check_filter_fields('region == "EMEA"', txns)
        assert "region" not in unused


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

    def test_annual_groups_full_year(self) -> None:
        """period_type='annual' groups all months into a single year window."""
        engine = CommissionEngine()
        plan = Plan(
            plan_id="a", name="Annual", period_type="annual", currency="USD",
            rules=[TieredRule(type="tiered", id="t", tiers=[
                Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
                Tier(threshold_pct=Decimal("2.0"), rate=Decimal("0.08")),
            ])],
        )
        payees = [_payee(id="P1", name="A", quota=Decimal("100000"), plan_id="a")]
        txns = [
            _txn(id="T1", payee_id="P1", period="2026-03", amount=Decimal("80000"), close_date=date(2026, 3, 15)),
            _txn(id="T2", payee_id="P1", period="2026-07", amount=Decimal("40000"), close_date=date(2026, 7, 15)),
        ]
        result = engine.calculate(plan, txns, payees)
        total = sum(c.commission_amount for c in result.commissions)
        # 120000 annual vs 100000: 100000@0.05 + 20000@0.08 = 6600
        assert total == Decimal("6600")
        periods = {c.period for c in result.commissions}
        assert periods == {"2026"}


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


# --- ramp periods -------------------------------------------------------


class TestRampPeriods:
    def test_basic_monthly_ramp(self) -> None:
        """Ramp of [0.5, 1.0] halves quota in month 1, full in month 2."""
        ramp = RampSchedule(months=2, schedule=[Decimal("0.5"), Decimal("1.0")])
        payee = Payee(
            id="P1", name="Alice", quota=Decimal("100000"),
            effective_from=date(2026, 1, 1), ramp=ramp,
        )
        # Month 1: 0.5 * 100000 = 50000
        assert payee.quota_for("2026-01") == Decimal("50000")
        # Month 2: 1.0 * 100000 = 100000
        assert payee.quota_for("2026-02") == Decimal("100000")

    def test_ramp_ends_after_duration(self) -> None:
        """After ramp months, full quota without multiplier."""
        ramp = RampSchedule(months=2, schedule=[Decimal("0.5"), Decimal("1.0")])
        payee = Payee(
            id="P1", name="Alice", quota=Decimal("100000"),
            effective_from=date(2026, 1, 1), ramp=ramp,
        )
        # Month 3: ramp is over
        assert payee.quota_for("2026-03") == Decimal("100000")

    def test_window_before_effective_from(self) -> None:
        """Quota before start date is 0."""
        ramp = RampSchedule(months=2, schedule=[Decimal("0.5"), Decimal("1.0")])
        payee = Payee(
            id="P1", name="Alice", quota=Decimal("100000"),
            effective_from=date(2026, 3, 1), ramp=ramp,
        )
        assert payee.quota_for("2026-01") == Decimal("0")

    def test_mid_month_effective_from(self) -> None:
        """effective_from mid-month: that calendar month is ramp month 0."""
        ramp = RampSchedule(months=2, schedule=[Decimal("0.5"), Decimal("1.0")])
        payee = Payee(
            id="P1", name="Alice", quota=Decimal("100000"),
            effective_from=date(2026, 6, 15), ramp=ramp,
        )
        # June = same calendar month as effective_from → ramp month 0
        assert payee.quota_for("2026-06") == Decimal("50000")
        # July = next calendar month → ramp month 1
        assert payee.quota_for("2026-07") == Decimal("100000")
        # August = beyond ramp → full
        assert payee.quota_for("2026-08") == Decimal("100000")

    def test_quarterly_ramp_uses_last_month(self) -> None:
        """Quarterly windows use the last month of the quarter as reference."""
        ramp = RampSchedule(months=3, schedule=[Decimal("0.5"), Decimal("0.75"), Decimal("1.0")])
        # effective_from=2026-04-01 → month 4,5,6 are ramp months 0,1,2
        payee = Payee(
            id="P1", name="Alice", quota=Decimal("100000"),
            effective_from=date(2026, 4, 1), ramp=ramp,
        )
        # Q2 (Apr-Jun): last month is June → months_since = 6-4 = 2 → ramp[2] = 1.0
        assert payee.quota_for("2026-Q2") == Decimal("100000")
        # Q1 (Jan-Mar): last month is March → months_since = 3-4 = -1 → 0
        assert payee.quota_for("2026-Q1") == Decimal("0")

    def test_quarterly_ramp_mid_quarter_start(self) -> None:
        """Mid-quarter start gets correct ramp month using last-month reference."""
        ramp = RampSchedule(months=3, schedule=[Decimal("0.5"), Decimal("0.75"), Decimal("1.0")])
        # effective_from=2026-05-15 (mid Q2)
        payee = Payee(
            id="P1", name="Alice", quota=Decimal("100000"),
            effective_from=date(2026, 5, 15), ramp=ramp,
        )
        # Q2: last month = June → months_since = 6-5 = 1 → ramp[1] = 0.75
        assert payee.quota_for("2026-Q2") == Decimal("75000")
        # Q3: last month = September → months_since = 9-5 = 4 → beyond ramp → full
        assert payee.quota_for("2026-Q3") == Decimal("100000")

    def test_ramp_with_explicit_quotas_dict(self) -> None:
        """Ramp multiplier applies on top of explicit per-window quotas."""
        ramp = RampSchedule(months=2, schedule=[Decimal("0.5"), Decimal("1.0")])
        payee = Payee(
            id="P1", name="Alice", quota=Decimal("100000"),
            quotas={"2026-01": Decimal("80000")},
            effective_from=date(2026, 1, 1), ramp=ramp,
        )
        # 80000 * 0.5 = 40000
        assert payee.quota_for("2026-01") == Decimal("40000")

    def test_no_ramp_backward_compat(self) -> None:
        """Payee without ramp uses raw quota unchanged."""
        payee = Payee(
            id="P1", name="Alice", quota=Decimal("100000"),
            effective_from=date(2026, 1, 1),
        )
        assert payee.quota_for("2026-01") == Decimal("100000")

    def test_schedule_length_validation(self) -> None:
        """Schedule length must match months."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="length"):
            RampSchedule(months=3, schedule=[Decimal("0.5"), Decimal("0.75")])

    def test_tiered_with_ramp_reduced_attainment(self) -> None:
        """Tiered rule uses reduced quota, making tiers easier to reach."""
        ramp = RampSchedule(months=2, schedule=[Decimal("0.5"), Decimal("1.0")])
        payees = [
            Payee(id="P1", name="Alice", quota=Decimal("100000"),
                  effective_from=date(2026, 1, 1), ramp=ramp, plan_id="p"),
        ]
        plan = Plan(
            plan_id="p", name="P", period_type="monthly", currency="USD",
            rules=[TieredRule(type="tiered", id="t", tiers=[
                Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
                Tier(threshold_pct=Decimal("2.0"), rate=Decimal("0.10")),
            ])],
        )
        # 60000 against ramp-adjusted quota of 50000 = 120% attainment
        # 50000 @ 0.05 = 2500, 10000 @ 0.10 = 1000 → 3500
        txn = _txn(id="T1", payee_id="P1", amount=Decimal("60000"),
                   period="2026-01", close_date=date(2026, 1, 15))
        result = CommissionEngine().calculate(plan, [txn], payees)
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("3500")

    def test_attainment_reflects_ramp_quota(self) -> None:
        """Attainment computation uses the ramp-reduced quota."""
        ramp = RampSchedule(months=2, schedule=[Decimal("0.5"), Decimal("1.0")])
        payees = [
            Payee(id="P1", name="Alice", quota=Decimal("100000"),
                  effective_from=date(2026, 1, 1), ramp=ramp, plan_id="p"),
        ]
        plan = Plan(
            plan_id="p", name="P", period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))],
        )
        txn = _txn(id="T1", payee_id="P1", amount=Decimal("50000"),
                   period="2026-01", close_date=date(2026, 1, 15))
        result = CommissionEngine().calculate(plan, [txn], payees)
        a = result.attainment[0]
        # 50000 against ramp quota 50000 (50% of 100k) = 100% attainment
        assert a.quota == Decimal("50000")
        assert a.attainment_pct == Decimal("1.0")

    def test_zero_ramp_multiplier(self) -> None:
        """schedule[0]=0 means zero quota, attainment_pct is None, no crash."""
        ramp = RampSchedule(months=2, schedule=[Decimal("0"), Decimal("0.5")])
        payees = [
            Payee(id="P1", name="Alice", quota=Decimal("100000"),
                  effective_from=date(2026, 1, 1), ramp=ramp, plan_id="p"),
        ]
        plan = Plan(
            plan_id="p", name="P", period_type="monthly", currency="USD",
            rules=[TieredRule(type="tiered", id="t", tiers=[
                Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
                Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),
            ])],
        )
        txn = _txn(id="T1", payee_id="P1", amount=Decimal("50000"),
                   period="2026-01", close_date=date(2026, 1, 15))
        result = CommissionEngine().calculate(plan, [txn], payees)
        # Zero quota → top tier applied to all
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("5000")  # 50000 * 0.10
        assert result.attainment[0].attainment_pct is None

    def test_true_up_detects_ramp_change(self) -> None:
        """Changing a payee's ramp schedule triggers true-up adjustments.

        Uses an accelerator rule because it depends on quota (unlike flat rate)
        and produces exactly one commission line per (txn, rule, payee) when
        the deal crosses the threshold in a single transaction (unlike tiered).
        """
        plan = Plan(
            plan_id="p", name="P", period_type="monthly", currency="USD",
            rules=[AcceleratorRule(
                type="accelerator", id="accel",
                rate=Decimal("0.05"), threshold_pct=Decimal("1.0"),
                multiplier=Decimal("2.0"),
            )],
        )
        txn = _txn(id="T1", payee_id="P1", amount=Decimal("80000"),
                   period="2026-01", close_date=date(2026, 1, 15))
        eng = CommissionEngine()

        # Prior: no ramp, quota=100k. 80000 → below threshold → 0 commission
        prior_payees = [
            _payee(id="P1", name="Alice", quota=Decimal("100000"), plan_id="p"),
        ]
        prior = eng.calculate(plan, [txn], prior_payees).commissions
        assert sum(c.commission_amount for c in prior) == Decimal("0")

        # Current: ramp 0.5, quota=50k. 80000 → above threshold (160%).
        # 30000 above threshold * 0.05 * 2.0 = 3000
        ramp = RampSchedule(months=2, schedule=[Decimal("0.5"), Decimal("1.0")])
        current_payees = [
            Payee(id="P1", name="Alice", quota=Decimal("100000"),
                  effective_from=date(2026, 1, 1), ramp=ramp, plan_id="p"),
        ]
        tu = eng.true_up(plan, [txn], current_payees, prior)
        assert len(tu.adjustments) == 1
        assert tu.adjustments[0].delta == Decimal("3000")

    def test_ramp_csv_loader(self) -> None:
        """CSV with ramp_months and ramp_schedule columns parses correctly."""
        from icm_engine.loader import _parse_ramp
        row = {"ramp_months": "3", "ramp_schedule": "0.5 0.75 1.0"}
        ramp = _parse_ramp(row)
        assert ramp is not None
        assert ramp.months == 3
        assert ramp.schedule == [Decimal("0.5"), Decimal("0.75"), Decimal("1.0")]

    def test_ramp_csv_loader_no_columns(self) -> None:
        """Missing or empty ramp columns return None."""
        from icm_engine.loader import _parse_ramp
        assert _parse_ramp({}) is None
        assert _parse_ramp({"ramp_months": "", "ramp_schedule": ""}) is None

    def test_ramp_serialization_round_trip(self) -> None:
        """Payee with ramp survives JSON round trip."""
        ramp = RampSchedule(months=2, schedule=[Decimal("0.5"), Decimal("1.0")])
        payee = Payee(
            id="P1", name="Alice", quota=Decimal("100000"),
            effective_from=date(2026, 1, 1), ramp=ramp,
        )
        json_str = payee.model_dump_json()
        restored = Payee.model_validate_json(json_str)
        assert restored.ramp is not None
        assert restored.ramp.months == 2
        assert restored.ramp.schedule == [Decimal("0.5"), Decimal("1.0")]
        assert restored.quota_for("2026-01") == Decimal("50000")


# ------------------------------------------------------------------
# Manual adjustments
# ------------------------------------------------------------------


class TestManualAdjustments:
    def test_positive_adjustment(self) -> None:
        """Positive adjustment adds to commission total."""
        from icm_engine.models import ManualAdjustment

        engine = CommissionEngine()
        plan = Plan(plan_id="ADJ1", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]
        adjustments = [ManualAdjustment(payee_id="P1", period="2026-04", amount=Decimal("500"), reason="Bonus")]
        result = engine.calculate(plan, txns, payees, adjustments=adjustments)

        assert len(result.commissions) == 2
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("1000")  # 500 rule + 500 adjustment

        adj_line = next(c for c in result.commissions if c.rule_id == "manual_adjustment")
        assert adj_line.commission_amount == Decimal("500")
        assert adj_line.notes == "Bonus"

    def test_negative_adjustment(self) -> None:
        """Negative adjustment reduces commission."""
        from icm_engine.models import ManualAdjustment

        engine = CommissionEngine()
        plan = Plan(plan_id="ADJ2", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]
        adj = [ManualAdjustment(payee_id="P1", period="2026-04", amount=Decimal("-200"), reason="Clawback")]
        result = engine.calculate(plan, txns, payees, adjustments=adj)
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("300")

    def test_adjustment_does_not_affect_attainment(self) -> None:
        """Attainment unchanged by adjustments."""
        from icm_engine.models import ManualAdjustment

        engine = CommissionEngine()
        plan = Plan(plan_id="ADJ3", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", quota=Decimal("100000"))]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]
        adjustments = [ManualAdjustment(payee_id="P1", period="2026-04",
                                        amount=Decimal("99999"), reason="Huge")]
        result = engine.calculate(plan, txns, payees, adjustments=adjustments)
        att = result.attainment[0]
        assert att.bookings == Decimal("10000")
        assert att.attainment_pct is not None
        assert float(att.attainment_pct) == pytest.approx(0.1)

    def test_adjustment_ledger_event(self) -> None:
        """Each adjustment emits a ledger entry."""
        from icm_engine.models import ManualAdjustment

        engine = CommissionEngine()
        plan = Plan(plan_id="ADJ4", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]
        adjustments = [ManualAdjustment(payee_id="P1", period="2026-04",
                                        amount=Decimal("42"), reason="Test")]
        result = engine.calculate(plan, txns, payees, adjustments=adjustments)
        adj_events = [e for e in result.ledger if e.event_type == "manual_adjustment"]
        assert len(adj_events) == 1

    def test_no_adjustments_opt_out(self) -> None:
        """Without adjustments, output is identical to before."""
        engine = CommissionEngine()
        plan = Plan(plan_id="ADJ5", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]
        result = engine.calculate(plan, txns, payees)
        assert len(result.commissions) == 1
        assert result.commissions[0].commission_amount == Decimal("500")


# ------------------------------------------------------------------
# Caps & thresholds
# ------------------------------------------------------------------


class TestCaps:
    def test_per_rule_cap(self) -> None:
        """Per-rule cap limits that rule's output: 5% on 30k = 1500 capped to 1000."""
        engine = CommissionEngine()
        plan = Plan(plan_id="CAP1", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"),
                                       cap=Decimal("1000"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("30000"))]
        result = engine.calculate(plan, txns, payees)
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("1000")  # capped

    def test_plan_payout_cap(self) -> None:
        """Plan payout_cap applies across all rules: two rules, total 1800 capped to 1500."""
        engine = CommissionEngine()
        plan = Plan(plan_id="CAP2", name="Test", period_type="monthly", currency="USD",
                    payout_cap=Decimal("1500"),
                    rules=[
                        FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05")),
                        FlatRateRule(type="flat_rate", id="R2", rate=Decimal("0.04")),
                    ])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("20000"))]  # 1000 + 800 = 1800
        result = engine.calculate(plan, txns, payees)
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("1500")

    def test_cap_ledger_event(self) -> None:
        """Plan cap emits a cap_applied ledger entry."""
        engine = CommissionEngine()
        plan = Plan(plan_id="CAP3", name="Test", period_type="monthly", currency="USD",
                    payout_cap=Decimal("100"),
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]  # 500
        result = engine.calculate(plan, txns, payees)
        cap_events = [e for e in result.ledger if e.event_type == "cap_applied"]
        assert len(cap_events) >= 1

    def test_no_cap_opt_out(self) -> None:
        """Without caps, output unchanged."""
        engine = CommissionEngine()
        plan = Plan(plan_id="CAP4", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]
        result = engine.calculate(plan, txns, payees)
        assert result.commissions[0].commission_amount == Decimal("500")


class TestThresholdGate:
    def test_below_gate_pays_zero(self) -> None:
        """Rule with min_attainment_pct=0.5: 10% attainment → below gate, pays 0."""
        engine = CommissionEngine()
        plan = Plan(plan_id="GATE1", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"),
                                       min_attainment_pct=Decimal("0.5"))])
        payees = [_payee(id="P1", quota=Decimal("100000"))]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]  # 10%
        result = engine.calculate(plan, txns, payees)
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("0")

    def test_at_gate_pays(self) -> None:
        """Attainment exactly at min → rule pays."""
        engine = CommissionEngine()
        plan = Plan(plan_id="GATE2", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"),
                                       min_attainment_pct=Decimal("0.1"))])
        payees = [_payee(id="P1", quota=Decimal("100000"))]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]  # exactly 10%
        result = engine.calculate(plan, txns, payees)
        assert result.commissions[0].commission_amount == Decimal("500")

    def test_gate_emits_skip_event(self) -> None:
        """Below-gate payee gets a rule_skipped ledger entry."""
        engine = CommissionEngine()
        plan = Plan(plan_id="GATE3", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"),
                                       min_attainment_pct=Decimal("0.5"))])
        payees = [_payee(id="P1", quota=Decimal("100000"))]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]
        result = engine.calculate(plan, txns, payees)
        skip_events = [e for e in result.ledger
                       if e.event_type == "rule_skipped"
                       and e.inputs.get("reason") == "below_threshold_gate"]
        assert len(skip_events) >= 1


# ------------------------------------------------------------------
# Draws
# ------------------------------------------------------------------


class TestDraws:
    def test_non_recoverable_floor(self) -> None:
        """Non-recoverable draw tops up to floor: earned 500, draw 3000 → topup 2500."""
        from icm_engine.models import Draw

        engine = CommissionEngine()
        plan = Plan(plan_id="DRW1", name="Test", period_type="monthly", currency="USD",
                    draw=Draw(amount=Decimal("3000"), recoverable=False),
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]  # 500
        result = engine.calculate(plan, txns, payees)
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("3000")  # 500 + 2500 topup

    def test_non_recoverable_no_topup_when_above(self) -> None:
        """Above draw → no topup. Earned 5000, draw 3000 → payout 5000."""
        from icm_engine.models import Draw

        engine = CommissionEngine()
        plan = Plan(plan_id="DRW2", name="Test", period_type="monthly", currency="USD",
                    draw=Draw(amount=Decimal("3000"), recoverable=False),
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("100000"))]  # 5000
        result = engine.calculate(plan, txns, payees)
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("5000")

    def test_recoverable_draw_three_periods(self) -> None:
        """Three-period recoverable draw: P1=topup, P2=recovery, P3=clean."""
        from icm_engine.models import Draw

        engine = CommissionEngine()
        plan = Plan(plan_id="DRW3", name="Test", period_type="monthly", currency="USD",
                    draw=Draw(amount=Decimal("3000"), recoverable=True),
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))])
        payees = [_payee(id="P1")]

        # Period 1: earned 1000, draw 3000 → topup 2000, balance 0→2000
        txns1 = [_txn(id="T1", payee_id="P1", period="2026-01", amount=Decimal("10000"))]
        r1 = engine.calculate(plan, txns1, payees)
        total1 = sum(c.commission_amount for c in r1.commissions)
        assert total1 == Decimal("3000")
        assert r1.draw_balances.get("P1") == Decimal("2000")

        # Period 2: earned 5000, draw 3000, balance 2000 → recovers 2000, payout 3000, balance 2000→0
        txns2 = [_txn(id="T2", payee_id="P1", period="2026-02", amount=Decimal("50000"))]
        r2 = engine.calculate(plan, txns2, payees, prior_draw_balances=r1.draw_balances)
        total2 = sum(c.commission_amount for c in r2.commissions)
        assert total2 == Decimal("3000")  # 5000 - 2000 recovery
        assert r2.draw_balances.get("P1") == Decimal("0")

        # Period 3: earned 5000, draw 3000, balance 0 → no recovery, payout 5000
        txns3 = [_txn(id="T3", payee_id="P1", period="2026-03", amount=Decimal("50000"))]
        r3 = engine.calculate(plan, txns3, payees, prior_draw_balances=r2.draw_balances)
        total3 = sum(c.commission_amount for c in r3.commissions)
        assert total3 == Decimal("5000")
        assert r3.draw_balances.get("P1") == Decimal("0")

    def test_payee_draw_overrides_plan(self) -> None:
        """Payee-level draw overrides plan-level draw."""
        from icm_engine.models import Draw

        engine = CommissionEngine()
        plan = Plan(plan_id="DRW4", name="Test", period_type="monthly", currency="USD",
                    draw=Draw(amount=Decimal("1000"), recoverable=False),
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payee = Payee(id="P1", name="Alice", quota=Decimal("0"), plan_id="DRW4",
                      effective_from=date(2026, 1, 1),
                      draw=Draw(amount=Decimal("5000"), recoverable=False))
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]  # 500
        result = engine.calculate(plan, txns, [payee])
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("5000")  # uses payee draw (5000), not plan draw (1000)

    def test_draw_ledger_event(self) -> None:
        """Draw emits a ledger event."""
        from icm_engine.models import Draw

        engine = CommissionEngine()
        plan = Plan(plan_id="DRW5", name="Test", period_type="monthly", currency="USD",
                    draw=Draw(amount=Decimal("3000"), recoverable=False),
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]
        result = engine.calculate(plan, txns, payees)
        draw_events = [e for e in result.ledger if e.event_type == "draw"]
        assert len(draw_events) >= 1

    def test_no_draw_opt_out(self) -> None:
        """Without draw, output unchanged."""
        engine = CommissionEngine()
        plan = Plan(plan_id="DRW6", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]
        result = engine.calculate(plan, txns, payees)
        assert result.commissions[0].commission_amount == Decimal("500")
        assert result.draw_balances == {}


# ------------------------------------------------------------------
# MBOs / bonuses
# ------------------------------------------------------------------


class TestMBOs:
    def test_mbo_adds_to_total(self) -> None:
        """MBO amount appears in commission total."""
        from icm_engine.models import MBO

        engine = CommissionEngine()
        plan = Plan(plan_id="MBO1", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]  # 500
        mbos = [MBO(payee_id="P1", period="2026-04", amount=Decimal("2000"), label="Q2 Bonus")]
        result = engine.calculate(plan, txns, payees, mbos=mbos)
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("2500")  # 500 + 2000

    def test_mbo_subject_to_cap(self) -> None:
        """Plan cap applies to commission + MBO combined."""
        from icm_engine.models import MBO

        engine = CommissionEngine()
        plan = Plan(plan_id="MBO2", name="Test", period_type="monthly", currency="USD",
                    payout_cap=Decimal("1000"),
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]  # 500
        mbos = [MBO(payee_id="P1", period="2026-04", amount=Decimal("2000"), label="Bonus")]
        result = engine.calculate(plan, txns, payees, mbos=mbos)
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("1000")  # capped

    def test_mbo_covered_by_draw(self) -> None:
        """Draw floor covers commission + MBO. Earned 500 + 0, draw 3000 → topup 2500."""
        from icm_engine.models import MBO, Draw

        engine = CommissionEngine()
        plan = Plan(plan_id="MBO3", name="Test", period_type="monthly", currency="USD",
                    draw=Draw(amount=Decimal("3000"), recoverable=False),
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]  # 500
        mbos = [MBO(payee_id="P1", period="2026-04", amount=Decimal("0"), label="No bonus")]
        result = engine.calculate(plan, txns, payees, mbos=mbos)
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("3000")  # 500 earned, topped up to 3000

    def test_mbo_does_not_affect_attainment(self) -> None:
        """Attainment is bookings-only, unaffected by MBO."""
        from icm_engine.models import MBO

        engine = CommissionEngine()
        plan = Plan(plan_id="MBO4", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1", quota=Decimal("100000"))]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]
        mbos = [MBO(payee_id="P1", period="2026-04", amount=Decimal("50000"), label="Huge")]
        result = engine.calculate(plan, txns, payees, mbos=mbos)
        assert result.attainment[0].attainment_pct is not None
        assert float(result.attainment[0].attainment_pct) == pytest.approx(0.1)

    def test_mbo_ledger_event(self) -> None:
        """MBO emits a ledger event."""
        from icm_engine.models import MBO

        engine = CommissionEngine()
        plan = Plan(plan_id="MBO5", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]
        mbos = [MBO(payee_id="P1", period="2026-04", amount=Decimal("42"), label="Test")]
        result = engine.calculate(plan, txns, payees, mbos=mbos)
        mbo_events = [e for e in result.ledger if e.event_type == "mbo"]
        assert len(mbo_events) == 1

    def test_mbo_opt_out(self) -> None:
        """Without mbos, output unchanged."""
        engine = CommissionEngine()
        plan = Plan(plan_id="MBO6", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]
        result = engine.calculate(plan, txns, payees)
        assert result.commissions[0].commission_amount == Decimal("500")


# ------------------------------------------------------------------
# Per-category quotas
# ------------------------------------------------------------------


class TestCategoryQuotas:
    def test_category_quota_tiered(self) -> None:
        """Tiered rule uses category-specific quota."""
        payee = Payee(id="P1", name="Alice", quota=Decimal("100000"),
                      category_quotas={"new_business": Decimal("50000")},
                      effective_from=date(2026, 1, 1))
        engine = CommissionEngine()
        plan = Plan(plan_id="CAT1", name="Test", period_type="monthly", currency="USD",
                    rules=[TieredRule(type="tiered", id="R1", quota_category="new_business",
                                     tiers=[Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
                                            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10"))])])
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("60000"))]
        result = engine.calculate(plan, txns, [payee])
        # Bookings 60K / category quota 50K = 120% attainment
        # Tier 1: 50K * 0.05 = 2500, Tier 2: 10K * 0.10 = 1000
        assert sum(c.commission_amount for c in result.commissions) == Decimal("3500")

    def test_category_quota_uses_default_when_none(self) -> None:
        """Rule without quota_category uses default quota."""
        payee = Payee(id="P1", name="Alice", quota=Decimal("100000"),
                      category_quotas={"new_business": Decimal("50000")},
                      effective_from=date(2026, 1, 1))
        engine = CommissionEngine()
        plan = Plan(plan_id="CAT2", name="Test", period_type="monthly", currency="USD",
                    rules=[TieredRule(type="tiered", id="R1",  # no quota_category
                                     tiers=[Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
                                            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10"))])])
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("60000"))]
        result = engine.calculate(plan, txns, [payee])
        # Bookings 60K / default quota 100K = 60% attainment → tier 1 only
        assert sum(c.commission_amount for c in result.commissions) == Decimal("3000")

    def test_two_rules_different_categories(self) -> None:
        """Two rules with different categories use their respective quotas."""
        payee = Payee(id="P1", name="Alice", quota=Decimal("100000"),
                      category_quotas={"new_biz": Decimal("50000"), "expansion": Decimal("30000")},
                      effective_from=date(2026, 1, 1))
        engine = CommissionEngine()
        plan = Plan(plan_id="CAT3", name="Test", period_type="monthly", currency="USD",
                    rules=[
                        FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"),
                                     filter='product == "New"', quota_category="new_biz"),
                        FlatRateRule(type="flat_rate", id="R2", rate=Decimal("0.05"),
                                     filter='product == "Expansion"', quota_category="expansion"),
                    ])
        txns = [
            _txn(id="T1", payee_id="P1", amount=Decimal("40000"), product="New"),
            _txn(id="T2", payee_id="P1", amount=Decimal("20000"), product="Expansion"),
        ]
        result = engine.calculate(plan, txns, [payee])
        # R1: 40000 * 0.10 = 4000. R2: 20000 * 0.05 = 1000
        assert sum(c.commission_amount for c in result.commissions) == Decimal("5000")

    def test_category_quota_ramp(self) -> None:
        """Ramp applies to category quota."""
        ramp = RampSchedule(months=1, schedule=[Decimal("0.5")])
        payee = Payee(id="P1", name="Alice", quota=Decimal("100000"),
                      category_quotas={"new_business": Decimal("50000")},
                      effective_from=date(2026, 1, 1), ramp=ramp)
        engine = CommissionEngine()
        plan = Plan(plan_id="CAT4", name="Test", period_type="monthly", currency="USD",
                    rules=[TieredRule(type="tiered", id="R1", quota_category="new_business",
                                     tiers=[Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
                                            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10"))])])
        txns = [_txn(id="T1", payee_id="P1", period="2026-01", amount=Decimal("30000"))]
        result = engine.calculate(plan, txns, [payee])
        # Category quota = 50000 * 0.5 = 25000. Bookings 30K = 120%
        # Tier 1: 25K * 0.05 = 1250, Tier 2: 5K * 0.10 = 500
        assert sum(c.commission_amount for c in result.commissions) == Decimal("1750")

    def test_category_quota_backward_compat(self) -> None:
        """Plan without categories produces same output as before."""
        engine = CommissionEngine()
        plan = Plan(plan_id="CAT5", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("10000"))]
        result = engine.calculate(plan, txns, payees)
        assert result.commissions[0].commission_amount == Decimal("500")


class TestIntegration:
    def test_caps_draws_adjustments_order(self) -> None:
        """Verify order: caps → draws → manual adjustments."""
        from icm_engine.models import Draw, ManualAdjustment

        engine = CommissionEngine()
        # Plan: flat 5%, capped at $400, $5000 draw (non-recoverable)
        plan = Plan(plan_id="INT1", name="Test", period_type="monthly", currency="USD",
                    payout_cap=Decimal("400"),
                    draw=Draw(amount=Decimal("5000"), recoverable=False),
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))])
        payees = [_payee(id="P1")]
        txns = [_txn(id="T1", payee_id="P1", amount=Decimal("20000"))]  # 5% = 1000

        # No adjustments: cap limits to 400, then draw tops up to 5000 → total 5000
        r1 = engine.calculate(plan, txns, payees)
        assert sum(c.commission_amount for c in r1.commissions) == Decimal("5000")

        # With adjustments: cap→400, draw→5000, then +200 adjustment = 5200
        adjustments = [ManualAdjustment(payee_id="P1", period="2026-04",
                                        amount=Decimal("200"), reason="Bonus")]
        r2 = engine.calculate(plan, txns, payees, adjustments=adjustments)
        assert sum(c.commission_amount for c in r2.commissions) == Decimal("5200")

        # Verify adjustment is NOT capped
        adj_line = next(c for c in r2.commissions if c.rule_id == "manual_adjustment")
        assert adj_line.commission_amount == Decimal("200")


# ------------------------------------------------------------------
# Multi-plan tests
# ------------------------------------------------------------------


class TestCalculateRun:
    """Tests for calculate_run() — multi-plan commission runs."""

    def test_two_payees_two_plans(self) -> None:
        """Two payees on different plans — each computed under their own plan."""
        engine = CommissionEngine()
        plan_a = Plan(
            plan_id="A", name="Plan A", period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))],
        )
        plan_b = Plan(
            plan_id="B", name="Plan B", period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
        )
        payees = [
            _payee(id="P001", plan_id="A"),
            _payee(id="P002", plan_id="B"),
        ]
        txns = [
            _txn(id="T001", payee_id="P001", amount=Decimal("10000")),
            _txn(id="T002", payee_id="P002", amount=Decimal("10000")),
        ]
        result = engine.calculate_run({"A": plan_a, "B": plan_b}, txns, payees)

        # P001 under Plan A: 5% of 10000 = 500
        # P002 under Plan B: 10% of 10000 = 1000
        p001_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "P001")
        p002_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "P002")
        assert p001_total == Decimal("500")
        assert p002_total == Decimal("1000")

    def test_cross_plan_split(self) -> None:
        """One deal split 60/40 between payees on different plans."""
        engine = CommissionEngine()
        from icm_engine.models import Credit

        plan_a = Plan(
            plan_id="A", name="Plan A", period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
        )
        plan_b = Plan(
            plan_id="B", name="Plan B", period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))],
        )
        payees = [
            _payee(id="P001", plan_id="A"),
            _payee(id="P002", plan_id="B"),
        ]
        txns = [
            Transaction(
                id="T001", payee_id="P001", amount=Decimal("10000"),
                period="2026-04", close_date=date(2026, 4, 15),
                credits=[
                    Credit(payee_id="P001", split_pct=Decimal("0.6"), kind="split"),
                    Credit(payee_id="P002", split_pct=Decimal("0.4"), kind="split"),
                ],
            ),
        ]
        result = engine.calculate_run({"A": plan_a, "B": plan_b}, txns, payees)

        # P001: 60% of 10000 = 6000, 10% rate = 600
        # P002: 40% of 10000 = 4000, 5% rate = 200
        p001_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "P001")
        p002_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "P002")
        assert p001_total == Decimal("600")
        assert p002_total == Decimal("200")

    def test_mixed_period_types(self) -> None:
        """Payee on quarterly plan, another on monthly — each windowed correctly."""
        engine = CommissionEngine()
        plan_q = Plan(
            plan_id="Q", name="Quarterly", period_type="quarterly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
        )
        plan_m = Plan(
            plan_id="M", name="Monthly", period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
        )
        payees = [
            _payee(id="P001", plan_id="Q", quota=Decimal("30000")),
            _payee(id="P002", plan_id="M", quota=Decimal("10000")),
        ]
        txns = [
            _txn(id="T001", payee_id="P001", amount=Decimal("10000"), period="2026-01"),
            _txn(id="T002", payee_id="P001", amount=Decimal("10000"), period="2026-02"),
            _txn(id="T003", payee_id="P001", amount=Decimal("10000"), period="2026-03"),
            _txn(id="T004", payee_id="P002", amount=Decimal("5000"), period="2026-01"),
        ]
        result = engine.calculate_run({"Q": plan_q, "M": plan_m}, txns, payees)

        # P001 quarterly: 3 months in Q1 = 30000 bookings, 30000 quota = 100% attainment
        # Window is "2026-Q1"
        p001_periods = {c.period for c in result.commissions if c.payee_id == "P001"}
        assert p001_periods == {"2026-Q1"}

        # P002 monthly: period is "2026-01"
        p002_periods = {c.period for c in result.commissions if c.payee_id == "P002"}
        assert p002_periods == {"2026-01"}

        # Both should have correct commission at 10%
        p001_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "P001")
        p002_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "P002")
        assert p001_total == Decimal("3000")  # 30000 * 0.10
        assert p002_total == Decimal("500")   # 5000 * 0.10

    def test_per_plan_tier_a(self) -> None:
        """Plan A has payout_cap and draw, Plan B does not — applied only to right payees."""
        engine = CommissionEngine()
        from icm_engine.models import Draw

        plan_a = Plan(
            plan_id="A", name="Plan A", period_type="monthly", currency="USD",
            payout_cap=Decimal("500"),
            draw=Draw(amount=Decimal("1000"), recoverable=False),
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
        )
        plan_b = Plan(
            plan_id="B", name="Plan B", period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
        )
        payees = [
            _payee(id="P001", plan_id="A"),
            _payee(id="P002", plan_id="B"),
        ]
        txns = [
            _txn(id="T001", payee_id="P001", amount=Decimal("10000")),  # 10% = 1000, cap→500, draw→1000
            _txn(id="T002", payee_id="P002", amount=Decimal("10000")),  # 10% = 1000, no cap
        ]
        result = engine.calculate_run({"A": plan_a, "B": plan_b}, txns, payees)

        p001_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "P001")
        p002_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "P002")

        # P001: 1000 capped to 500, then draw tops up to 1000
        assert p001_total == Decimal("1000")
        # P002: 1000, no cap
        assert p002_total == Decimal("1000")

        # Verify cap was applied to P001
        cap_lines = [c for c in result.commissions if c.rule_id == "payout_cap" and c.payee_id == "P001"]
        assert len(cap_lines) == 1
        assert cap_lines[0].commission_amount == Decimal("-500")
        # No cap on P002
        cap_lines_p2 = [c for c in result.commissions if c.rule_id == "payout_cap" and c.payee_id == "P002"]
        assert len(cap_lines_p2) == 0

        # Verify draw was applied to P001
        draw_lines = [c for c in result.commissions if c.rule_id == "draw" and c.payee_id == "P001"]
        assert len(draw_lines) == 1
        # No draw on P002
        draw_lines_p2 = [c for c in result.commissions if c.rule_id == "draw" and c.payee_id == "P002"]
        assert len(draw_lines_p2) == 0

    def test_missing_plan_raises_error(self) -> None:
        """Payee whose plan_id is not in the library → clear error."""
        engine = CommissionEngine()
        plan_a = Plan(
            plan_id="A", name="Plan A", period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05"))],
        )
        payees = [
            _payee(id="P001", plan_id="A"),
            _payee(id="P002", plan_id="MISSING"),
        ]
        txns = [
            _txn(id="T001", payee_id="P001", amount=Decimal("1000")),
        ]
        with pytest.raises(ValueError, match="MISSING"):
            engine.calculate_run({"A": plan_a}, txns, payees)

    def test_golden_equivalence(self) -> None:
        """A run where every payee is on the SAME plan produces output identical to calculate()."""
        engine = CommissionEngine()
        plan = Plan(
            plan_id="SINGLE", name="Single", period_type="monthly", currency="USD",
            payout_cap=Decimal("2000"),
            rules=[
                FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.05")),
                TieredRule(
                    type="tiered", id="R2",
                    tiers=[
                        Tier(threshold_pct=Decimal("0.5"), rate=Decimal("0.08")),
                        Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.12")),
                    ],
                ),
            ],
        )
        payees = [
            _payee(id="P001", plan_id="SINGLE", quota=Decimal("10000")),
            _payee(id="P002", plan_id="SINGLE", quota=Decimal("20000")),
            _payee(id="P003", plan_id="SINGLE", quota=Decimal("50000")),
        ]
        txns = [
            _txn(id="T001", payee_id="P001", amount=Decimal("5000")),
            _txn(id="T002", payee_id="P002", amount=Decimal("15000")),
            _txn(id="T003", payee_id="P003", amount=Decimal("30000")),
        ]
        r_single = engine.calculate(plan, txns, payees)
        r_multi = engine.calculate_run({"SINGLE": plan}, txns, payees)

        assert len(r_multi.commissions) == len(r_single.commissions)
        for mc, sc in zip(
            sorted(r_multi.commissions, key=lambda c: (c.payee_id, c.rule_id, c.transaction_id)),
            sorted(r_single.commissions, key=lambda c: (c.payee_id, c.rule_id, c.transaction_id)),
            strict=True,
        ):
            assert mc.payee_id == sc.payee_id
            assert mc.rule_id == sc.rule_id
            assert mc.transaction_id == sc.transaction_id
            assert mc.commission_amount == sc.commission_amount
            assert mc.period == sc.period

        assert len(r_multi.ledger) == len(r_single.ledger)
        assert len(r_multi.attainment) == len(r_single.attainment)
        assert r_multi.draw_balances == r_single.draw_balances

    def test_golden_equivalence_with_all_params(self) -> None:
        """Equivalence with MBOs, adjustments, caps, and draws."""
        engine = CommissionEngine()
        from icm_engine.models import MBO, Draw, ManualAdjustment

        plan = Plan(
            plan_id="FULL", name="Full", period_type="monthly", currency="USD",
            payout_cap=Decimal("5000"),
            draw=Draw(amount=Decimal("3000"), recoverable=False),
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
        )
        payees = [
            _payee(id="P001", plan_id="FULL"),
        ]
        txns = [
            _txn(id="T001", payee_id="P001", amount=Decimal("40000")),  # 10% = 4000, cap under 5000
        ]
        mbos = [MBO(payee_id="P001", period="2026-04", amount=Decimal("500"), label="Q1 bonus")]
        adjustments = [ManualAdjustment(payee_id="P001", period="2026-04",
                                         amount=Decimal("-200"), reason="Clawback")]

        r_single = engine.calculate(
            plan, txns, payees,
            adjustments=adjustments, mbos=mbos,
        )
        r_multi = engine.calculate_run(
            {"FULL": plan}, txns, payees,
            adjustments=adjustments, mbos=mbos,
        )

        assert len(r_multi.commissions) == len(r_single.commissions)
        for mc, sc in zip(
            sorted(r_multi.commissions, key=lambda c: (c.payee_id, c.rule_id, c.transaction_id)),
            sorted(r_single.commissions, key=lambda c: (c.payee_id, c.rule_id, c.transaction_id)),
            strict=True,
        ):
            assert mc.commission_amount == sc.commission_amount
            assert mc.rule_id == sc.rule_id
            assert mc.payee_id == sc.payee_id

        assert len(r_multi.ledger) == len(r_single.ledger)


# ------------------------------------------------------------------
# Hierarchy tests
# ------------------------------------------------------------------


class TestHierarchy:
    """Tests for manager hierarchy — manager overrides via overlays."""

    def test_simple_manager_override(self) -> None:
        """Alice reports to Bob with 5% manager override. Bob gets 5% overlay."""
        engine = CommissionEngine()
        plan = Plan(
            plan_id="P1", name="Test", period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
        )
        alice = _payee(id="alice", plan_id="P1", quota=Decimal("10000"))
        alice.manager_id = "bob"
        alice.manager_override = Decimal("0.05")

        bob = _payee(id="bob", plan_id="P1", quota=Decimal("50000"))

        txns = [_txn(id="T1", payee_id="alice", amount=Decimal("10000"))]
        result = engine.calculate(plan, txns, [alice, bob])

        # Alice: 10% of 10000 = 1000
        alice_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "alice")
        assert alice_total == Decimal("1000")

        # Bob: 10% of his overlay (5% of 10000 = 500) = 50
        bob_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "bob")
        assert bob_total == Decimal("50")

        # Verify overlay credit exists
        overlay = [c for c in result.commissions if c.payee_id == "bob" and c.kind == "manager_override"]
        assert len(overlay) == 1
        assert overlay[0].split_pct == Decimal("0.05")

    def test_multi_level_chain(self) -> None:
        """Alice → Bob (5%) → Carol (2%). Three-level chain."""
        engine = CommissionEngine()
        plan = Plan(
            plan_id="P1", name="Test", period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
        )
        alice = _payee(id="alice", plan_id="P1", quota=Decimal("10000"))
        alice.manager_id = "bob"
        alice.manager_override = Decimal("0.05")

        bob = _payee(id="bob", plan_id="P1", quota=Decimal("50000"))
        bob.manager_id = "carol"
        bob.manager_override = Decimal("0.02")

        carol = _payee(id="carol", plan_id="P1", quota=Decimal("200000"))

        txns = [_txn(id="T1", payee_id="alice", amount=Decimal("10000"))]
        result = engine.calculate(plan, txns, [alice, bob, carol])

        # Alice: 10% of 10000 = 1000
        alice_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "alice")
        assert alice_total == Decimal("1000")

        # Bob: 10% of (5% of 10000 = 500) = 50
        bob_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "bob")
        assert bob_total == Decimal("50")

        # Carol: 10% of (2% of original 10000 = 200) = 20
        # Each manager gets their override on the original deal amount
        carol_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "carol")
        assert carol_total == Decimal("20")

    def test_cross_plan_manager(self) -> None:
        """Alice on Plan A reports to Bob on Plan B. Bob's overlay is under Plan B."""
        engine = CommissionEngine()
        plan_a = Plan(
            plan_id="A", name="Plan A", period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
        )
        plan_b = Plan(
            plan_id="B", name="Plan B", period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.20"))],
        )
        alice = _payee(id="alice", plan_id="A", quota=Decimal("10000"))
        alice.manager_id = "bob"
        alice.manager_override = Decimal("0.05")

        bob = _payee(id="bob", plan_id="B", quota=Decimal("50000"))

        txns = [_txn(id="T1", payee_id="alice", amount=Decimal("10000"))]
        result = engine.calculate_run({"A": plan_a, "B": plan_b}, txns, [alice, bob])

        # Alice: 10% of 10000 = 1000
        alice_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "alice")
        assert alice_total == Decimal("1000")

        # Bob: 20% (Plan B rate) of (5% of 10000 = 500) = 100
        bob_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "bob")
        assert bob_total == Decimal("100")

    def test_no_override_when_zero(self) -> None:
        """Manager with override=0 generates no overlay."""
        engine = CommissionEngine()
        plan = Plan(
            plan_id="P1", name="Test", period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
        )
        alice = _payee(id="alice", plan_id="P1", quota=Decimal("10000"))
        alice.manager_id = "bob"
        alice.manager_override = Decimal("0")

        bob = _payee(id="bob", plan_id="P1", quota=Decimal("50000"))

        txns = [_txn(id="T1", payee_id="alice", amount=Decimal("10000"))]
        result = engine.calculate(plan, txns, [alice, bob])

        bob_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "bob")
        assert bob_total == Decimal("0")

    def test_cycle_detection(self) -> None:
        """Alice → Bob → Alice cycle should not infinite-loop."""
        engine = CommissionEngine()
        plan = Plan(
            plan_id="P1", name="Test", period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
        )
        alice = _payee(id="alice", plan_id="P1", quota=Decimal("10000"))
        alice.manager_id = "bob"
        alice.manager_override = Decimal("0.05")

        bob = _payee(id="bob", plan_id="P1", quota=Decimal("50000"))
        bob.manager_id = "alice"  # cycle!
        bob.manager_override = Decimal("0.05")

        txns = [_txn(id="T1", payee_id="alice", amount=Decimal("10000"))]
        result = engine.calculate(plan, txns, [alice, bob])

        # Should complete without error (cycle breaks after one iteration)
        assert len(result.commissions) > 0

    def test_manager_not_in_payee_list_is_ok(self) -> None:
        """Manager referenced but not in payee list — overlay is still generated.
        It just won't match any rules (no payee_map entry)."""
        engine = CommissionEngine()
        plan = Plan(
            plan_id="P1", name="Test", period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
        )
        alice = _payee(id="alice", plan_id="P1", quota=Decimal("10000"))
        alice.manager_id = "bob"  # bob not in payees list
        alice.manager_override = Decimal("0.05")

        txns = [_txn(id="T1", payee_id="alice", amount=Decimal("10000"))]
        # Should not raise — bob's overlay is generated but just yields no commissions
        result = engine.calculate(plan, txns, [alice])
        assert len(result.commissions) > 0  # alice still gets paid

    def test_attainment_includes_manager_overlay(self) -> None:
        """Manager's overlay bookings count toward their attainment."""
        engine = CommissionEngine()
        plan = Plan(
            plan_id="P1", name="Test", period_type="monthly", currency="USD",
            rules=[],
        )
        alice = _payee(id="alice", plan_id="P1", quota=Decimal("10000"))
        alice.manager_id = "bob"
        alice.manager_override = Decimal("0.10")

        bob = _payee(id="bob", plan_id="P1", quota=Decimal("50000"))

        txns = [_txn(id="T1", payee_id="alice", amount=Decimal("10000"))]
        result = engine.calculate(plan, txns, [alice, bob])

        # Bob's attainment includes his overlay booking (10% of 10000 = 1000)
        bob_att = [a for a in result.attainment if a.payee_id == "bob"]
        assert len(bob_att) == 1
        assert bob_att[0].bookings == Decimal("1000")

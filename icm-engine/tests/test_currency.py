"""Tests for multi-currency conversion (display-only, off by default)."""

from decimal import Decimal

import pytest

from icm_engine.currency import convert, load_rates, needs_conversion


class TestLoadRates:
    def test_valid_json(self) -> None:
        rates = load_rates('{"CAD": "1.35", "EUR": "0.92"}')
        assert rates == {"CAD": Decimal("1.35"), "EUR": Decimal("0.92")}

    def test_empty_returns_empty(self) -> None:
        assert load_rates(None) == {}
        assert load_rates("") == {}
        assert load_rates("  ") == {}

    def test_invalid_json_returns_empty(self) -> None:
        assert load_rates("not json") == {}
        assert load_rates("{bad") == {}

    def test_zero_rate_skipped(self) -> None:
        rates = load_rates('{"CAD": "0", "EUR": "0.92"}')
        assert "CAD" not in rates
        assert rates["EUR"] == Decimal("0.92")

    def test_uppercases_keys(self) -> None:
        rates = load_rates('{"cad": "1.35"}')
        assert rates == {"CAD": Decimal("1.35")}


class TestConvert:
    _rates = {"CAD": Decimal("1.35"), "EUR": Decimal("0.92"), "GBP": Decimal("0.79")}

    def test_same_currency_no_conversion(self) -> None:
        assert convert(Decimal("100"), "USD", "USD", self._rates) == Decimal("100")
        assert convert(Decimal("100"), "CAD", "CAD", self._rates) == Decimal("100")

    def test_blank_currency_no_conversion(self) -> None:
        assert convert(Decimal("100"), "", "USD", self._rates) == Decimal("100")
        assert convert(Decimal("100"), "USD", "", self._rates) == Decimal("100")

    def test_usd_to_cad(self) -> None:
        # 1 USD = 1.35 CAD → 100 USD = 135 CAD
        result = convert(Decimal("100"), "USD", "CAD", self._rates)
        assert result == Decimal("135.00")

    def test_cad_to_usd(self) -> None:
        # 100 CAD / 1.35 = 74.074... → round half-up to 74.07
        result = convert(Decimal("100"), "CAD", "USD", self._rates)
        assert result == Decimal("74.07")

    def test_cross_currency_cad_to_eur(self) -> None:
        # CAD → USD (÷ 1.35) → EUR (× 0.92)
        # 135 CAD = 100 USD = 92 EUR
        result = convert(Decimal("135"), "CAD", "EUR", self._rates)
        assert result == Decimal("92.00")

    def test_missing_from_currency_raises(self) -> None:
        with pytest.raises(KeyError, match="MXN"):
            convert(Decimal("100"), "MXN", "USD", self._rates)

    def test_missing_to_currency_raises(self) -> None:
        with pytest.raises(KeyError, match="JPY"):
            convert(Decimal("100"), "USD", "JPY", self._rates)

    def test_case_insensitive(self) -> None:
        result = convert(Decimal("100"), "usd", "cad", self._rates)
        assert result == Decimal("135.00")


class TestNeedsConversion:
    def test_blank_reporting_currency(self) -> None:
        assert not needs_conversion("CAD", "")
        assert not needs_conversion("CAD", "  ")

    def test_same_currency(self) -> None:
        assert not needs_conversion("USD", "USD")
        assert not needs_conversion("cad", "CAD")

    def test_different_currency(self) -> None:
        assert needs_conversion("CAD", "USD")
        assert needs_conversion("EUR", "USD")


class TestIntegration:
    """End-to-end: statement generation with currency conversion."""

    def test_statement_rounds_and_converts(self) -> None:
        """CAD plan, reporting in USD. Statement amounts should be in USD."""
        from datetime import date as _date
        from decimal import Decimal as _D
        from pathlib import Path as _Path
        from tempfile import TemporaryDirectory

        from icm_engine.engine import CommissionEngine
        from icm_engine.models import (
            FlatRateRule,
            Payee,
            Plan,
            Transaction,
        )
        from icm_engine.statements import generate_statements

        plan = Plan(
            plan_id="P1", name="CAD Plan", period_type="monthly",
            currency="CAD", reporting_currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=_D("0.10"))],
        )
        payees = [
            Payee(id="A", name="Alice", quota=_D("10000"), plan_id="P1",
                  effective_from=_date(2026, 1, 1)),
        ]
        txns = [
            Transaction(id="T1", payee_id="A", amount=_D("10000"),
                        period="2026-04", close_date=_date(2026, 4, 15)),
        ]
        engine = CommissionEngine()
        result = engine.calculate(plan, txns, payees)

        # Alice's commission: 10% of 10000 CAD = 1000 CAD
        alice_total = sum(
            c.commission_amount for c in result.commissions if c.payee_id == "A"
        )
        assert alice_total == _D("1000")  # still CAD internally

        rates = {"CAD": _D("1.35")}  # 1 USD = 1.35 CAD
        with TemporaryDirectory() as tmp:
            out = _Path(tmp)
            files = generate_statements(
                result.commissions, payees, out_dir=out,
                formats=("xlsx",), rounding_mode="half-up",
                rates=rates, reporting_currency="USD",
                source_currency="CAD",
            )
            assert len(files) == 1

    def test_no_conversion_when_off(self) -> None:
        """Default: no reporting_currency → amounts stay in source currency."""
        from datetime import date as _date
        from decimal import Decimal as _D
        from pathlib import Path as _Path
        from tempfile import TemporaryDirectory

        from icm_engine.engine import CommissionEngine
        from icm_engine.models import (
            FlatRateRule,
            Payee,
            Plan,
            Transaction,
        )
        from icm_engine.statements import generate_statements

        plan = Plan(
            plan_id="P1", name="CAD Plan", period_type="monthly",
            currency="CAD",  # no reporting_currency set
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=_D("0.10"))],
        )
        payees = [
            Payee(id="A", name="Alice", quota=_D("10000"), plan_id="P1",
                  effective_from=_date(2026, 1, 1)),
        ]
        txns = [
            Transaction(id="T1", payee_id="A", amount=_D("10000"),
                        period="2026-04", close_date=_date(2026, 4, 15)),
        ]
        engine = CommissionEngine()
        result = engine.calculate(plan, txns, payees)

        with TemporaryDirectory() as tmp:
            out = _Path(tmp)
            files = generate_statements(
                result.commissions, payees, out_dir=out,
                formats=("xlsx",),
            )
            assert len(files) == 1  # should not crash

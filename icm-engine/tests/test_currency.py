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

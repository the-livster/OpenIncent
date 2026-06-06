from decimal import Decimal

from icm_engine.rounding import (
    RoundingMode,
    parse_rounding_mode,
    round_money,
    round_money_str,
)


class TestRoundMoney:
    def test_half_up(self) -> None:
        """Standard rounding: 0.5 rounds up."""
        rm = RoundingMode.HALF_UP
        assert round_money(Decimal("64.595"), rm) == Decimal("64.60")
        assert round_money(Decimal("64.594"), rm) == Decimal("64.59")
        assert round_money(Decimal("100.005"), rm) == Decimal("100.01")
        assert round_money(Decimal("0.001"), rm) == Decimal("0.00")
        assert round_money(Decimal("0.009"), rm) == Decimal("0.01")
        assert round_money(Decimal("100.00"), rm) == Decimal("100.00")

    def test_floor(self) -> None:
        """Floor: always rounds down."""
        rm = RoundingMode.FLOOR
        assert round_money(Decimal("64.599"), rm) == Decimal("64.59")
        assert round_money(Decimal("64.591"), rm) == Decimal("64.59")
        assert round_money(Decimal("100.009"), rm) == Decimal("100.00")
        assert round_money(Decimal("-10.001"), rm) == Decimal("-10.01")  # floor goes more negative

    def test_ceil(self) -> None:
        """Ceil: always rounds up."""
        rm = RoundingMode.CEIL
        assert round_money(Decimal("64.591"), rm) == Decimal("64.60")
        assert round_money(Decimal("64.500"), rm) == Decimal("64.50")
        assert round_money(Decimal("100.001"), rm) == Decimal("100.01")
        assert round_money(Decimal("-10.009"), rm) == Decimal("-10.00")  # ceil toward zero for negative

    def test_none_mode(self) -> None:
        """None mode: no rounding, exact value returned."""
        rm = RoundingMode.NONE
        assert round_money(Decimal("64.595123"), rm) == Decimal("64.595123")
        assert round_money(Decimal("0.000001"), rm) == Decimal("0.000001")

    def test_round_money_str(self) -> None:
        """round_money_str returns a string."""
        assert round_money_str(Decimal("64.595")) == "64.60"
        assert round_money_str(Decimal("100.00")) == "100.00"


class TestParseRoundingMode:
    def test_standard_names(self) -> None:
        assert parse_rounding_mode("half-up") == RoundingMode.HALF_UP
        assert parse_rounding_mode("floor") == RoundingMode.FLOOR
        assert parse_rounding_mode("ceil") == RoundingMode.CEIL
        assert parse_rounding_mode("none") == RoundingMode.NONE

    def test_case_insensitive(self) -> None:
        assert parse_rounding_mode("HALF-UP") == RoundingMode.HALF_UP
        assert parse_rounding_mode("Floor") == RoundingMode.FLOOR
        assert parse_rounding_mode("NONE") == RoundingMode.NONE

    def test_aliases(self) -> None:
        assert parse_rounding_mode("half_up") == RoundingMode.HALF_UP
        assert parse_rounding_mode("round_half_up") == RoundingMode.HALF_UP
        assert parse_rounding_mode("off") == RoundingMode.NONE

    def test_invalid_defaults_to_half_up(self) -> None:
        assert parse_rounding_mode("garbage") == RoundingMode.HALF_UP
        assert parse_rounding_mode("") == RoundingMode.HALF_UP


class TestInternalPrecisionPreserved:
    """Verify that rounding is display-only — internal sums stay exact."""

    def test_sum_is_exact_regardless_of_rounding(self) -> None:
        """Internal Decimal addition is never rounded by the rounding module."""
        a = Decimal("0.333")
        b = Decimal("0.333")
        c = Decimal("0.334")
        total = a + b + c
        assert total == Decimal("1.000")
        # Rounding for display does not affect the sum
        displayed = round_money_str(total, RoundingMode.HALF_UP)
        assert displayed == "1.00"
        # The original total is still exact
        assert total == Decimal("1.000")

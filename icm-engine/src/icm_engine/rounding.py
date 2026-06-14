"""Centralized money rounding — applied at display/output boundary only.

The calculation engine always uses full-precision Decimal internally.
Rounding is a presentation concern: it never feeds back into the next run.
"""

from __future__ import annotations

import enum
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal


class RoundingMode(enum.Enum):
    HALF_UP = "half-up"
    HALF_EVEN = "half-even"
    FLOOR = "floor"
    CEIL = "ceil"
    NONE = "none"


_ROUNDING_MAP = {
    RoundingMode.HALF_UP: ROUND_HALF_UP,
    RoundingMode.HALF_EVEN: ROUND_HALF_EVEN,
    RoundingMode.FLOOR: ROUND_FLOOR,
    RoundingMode.CEIL: ROUND_CEILING,
}

_CENTS = Decimal("0.01")


def round_money(
    amount: Decimal, mode: RoundingMode | str = RoundingMode.HALF_UP, places: int = 2,
) -> Decimal:
    """Round a monetary amount to `places` decimal places using the given mode.

    `mode` may be a RoundingMode or its string name (rounding policies are
    string-configured in plans/CLI/API).

    HALF_UP:   standard rounding (0.5 → 1)
    HALF_EVEN: banker's rounding (0.5 → nearest even digit)
    FLOOR:     always round down
    CEIL:      always round up
    NONE:      return exact value (no rounding)
    """
    if isinstance(mode, str):
        mode = parse_rounding_mode(mode)
    if mode == RoundingMode.NONE:
        return amount
    py_rounding = _ROUNDING_MAP[mode]
    quantum = _CENTS if places == 2 else Decimal(1).scaleb(-places)
    return amount.quantize(quantum, rounding=py_rounding)


def round_money_str(
    amount: Decimal, mode: RoundingMode = RoundingMode.HALF_UP, places: int = 2,
) -> str:
    """Round and return as a string."""
    return str(round_money(amount, mode, places))


def parse_rounding_mode(value: str) -> RoundingMode:
    """Parse a rounding mode string, case-insensitive. Defaults to HALF_UP."""
    v = value.strip().lower().replace("_", "-")
    for mode in RoundingMode:
        if mode.value == v:
            return mode
    # Accept Python constant names too
    aliases = {"round_half_up": RoundingMode.HALF_UP, "half_up": RoundingMode.HALF_UP,
               "round_floor": RoundingMode.FLOOR, "round_ceil": RoundingMode.CEIL,
               "none": RoundingMode.NONE, "off": RoundingMode.NONE,
               "halfup": RoundingMode.HALF_UP,
               "round_half_even": RoundingMode.HALF_EVEN, "half_even": RoundingMode.HALF_EVEN,
               "halfeven": RoundingMode.HALF_EVEN, "banker": RoundingMode.HALF_EVEN,
               "bankers": RoundingMode.HALF_EVEN, "even": RoundingMode.HALF_EVEN}
    if v in aliases:
        return aliases[v]
    return RoundingMode.HALF_UP

"""Centralized money rounding — applied at display/output boundary only.

The calculation engine always uses full-precision Decimal internally.
Rounding is a presentation concern: it never feeds back into the next run.
"""

from __future__ import annotations

import enum
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal


class RoundingMode(enum.Enum):
    HALF_UP = "half-up"
    FLOOR = "floor"
    CEIL = "ceil"
    NONE = "none"


_ROUNDING_MAP = {
    RoundingMode.HALF_UP: ROUND_HALF_UP,
    RoundingMode.FLOOR: ROUND_FLOOR,
    RoundingMode.CEIL: ROUND_CEILING,
}

_CENTS = Decimal("0.01")


def round_money(amount: Decimal, mode: RoundingMode = RoundingMode.HALF_UP) -> Decimal:
    """Round a monetary amount to cents using the specified mode.

    HALF_UP: standard rounding (0.5 → 1)
    FLOOR:   always round down
    CEIL:    always round up
    NONE:    return exact value (no rounding)
    """
    if mode == RoundingMode.NONE:
        return amount
    py_rounding = _ROUNDING_MAP[mode]
    return amount.quantize(_CENTS, rounding=py_rounding)


def round_money_str(amount: Decimal, mode: RoundingMode = RoundingMode.HALF_UP) -> str:
    """Round and return as a string."""
    return str(round_money(amount, mode))


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
               "halfup": RoundingMode.HALF_UP, "half_up": RoundingMode.HALF_UP}
    if v in aliases:
        return aliases[v]
    return RoundingMode.HALF_UP

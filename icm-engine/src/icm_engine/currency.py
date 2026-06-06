"""Multi-currency conversion — display/output boundary only.

Internal calculations always stay in the plan's native currency. Conversion
to a reporting currency is applied only when formatting output (statements,
registers, CLI tables, API responses). Default: off (reporting_currency blank).
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

from icm_engine.rounding import RoundingMode, round_money

_log = logging.getLogger(__name__)


def load_rates(rates_json: str | None) -> dict[str, Decimal]:
    """Parse a JSON exchange rates string into {currency: rate_vs_USD}.

    Example input: '{"CAD": "1.35", "EUR": "0.92"}'
    Means: 1 USD = 1.35 CAD, 1 USD = 0.92 EUR.

    Returns empty dict for None/blank/invalid input.
    """
    if not rates_json or not rates_json.strip():
        return {}
    try:
        raw: dict[str, str] = json.loads(rates_json)
        return {k.upper(): Decimal(str(v)) for k, v in raw.items() if Decimal(str(v)) != 0}
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        _log.warning("Invalid exchange rates JSON: %s", e)
        return {}


def convert(
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    rates: dict[str, Decimal],
    *,
    rounding: RoundingMode = RoundingMode.HALF_UP,
) -> Decimal:
    """Convert an amount between currencies using USD as the base.

    If from == to, returns amount unchanged.
    If either currency is missing from rates, raises KeyError with a clear message.
    """
    f = from_currency.upper().strip()
    t = to_currency.upper().strip()

    if f == t or not f or not t:
        return amount

    # Convert from_currency → USD → to_currency
    # USD is the implied base: rates[CUR] = how many CUR per 1 USD
    usd: Decimal
    if f == "USD":
        usd = amount
    elif f in rates:
        usd = amount / rates[f]
    else:
        raise KeyError(
            f"Exchange rate not found for {from_currency}. "
            f"Available rates: {sorted(rates.keys())}"
        )

    if t == "USD":
        result = usd
    elif t in rates:
        result = usd * rates[t]
    else:
        raise KeyError(
            f"Exchange rate not found for {to_currency}. "
            f"Available rates: {sorted(rates.keys())}"
        )

    return round_money(result, rounding)


def needs_conversion(plan_currency: str, reporting_currency: str) -> bool:
    """Return True if conversion should be applied."""
    rc = (reporting_currency or "").strip().upper()
    pc = (plan_currency or "").strip().upper()
    return bool(rc) and rc != pc

"""Payout register — the finance-ready output produced after locking a period.

All monetary values are rounded to cents using the configured rounding mode.
Internal calculations remain exact Decimal; rounding is applied only at this output boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from icm_engine.excel import write_xlsx
from icm_engine.models import Commission, Payee, Plan
from icm_engine.rounding import RoundingMode, round_money


def _round(amount: Decimal, mode: RoundingMode = RoundingMode.HALF_UP) -> Decimal:
    """Round to two decimal places using the given mode."""
    return round_money(amount, mode)


@dataclass
class PayoutRegister:
    """A finance-ready summary of what each payee is owed for a period."""

    plan_id: str
    plan_name: str
    period: str
    currency: str
    version: int
    lines: list[RegisterLine]
    total_payout: Decimal  # rounded sum of all payouts

    def to_rows(self) -> list[dict[str, str]]:
        """Convert to list-of-dicts suitable for write_xlsx."""
        rows: list[dict[str, str]] = []
        total = Decimal("0")
        for line in sorted(self.lines, key=lambda r: (r.payee_id,)):
            rows.append({
                "Payee ID": line.payee_id,
                "Payee Name": line.payee_name,
                "Period": self.period,
                "Plan ID": self.plan_id,
                "Plan Name": self.plan_name,
                "Currency": self.currency,
                "Gross Commission": str(line.gross_commission),
                "Payout": str(line.rounded_payout),
            })
            total += line.rounded_payout
        # Totals row
        rows.append({
            "Payee ID": "",
            "Payee Name": "TOTAL",
            "Period": "",
            "Plan ID": "",
            "Plan Name": "",
            "Currency": "",
            "Gross Commission": "",
            "Payout": str(_round(total)),
        })
        return rows


@dataclass
class RegisterLine:
    """One payee's payout for a period."""

    payee_id: str
    payee_name: str
    gross_commission: Decimal  # exact sum before rounding
    rounded_payout: Decimal    # rounded to cents


def _line_items_sheet(
    commissions: list[Commission],
) -> list[dict[str, str]]:
    """Build line-item detail rows with rounded amounts."""
    rows: list[dict[str, str]] = []
    for c in commissions:
        rows.append({
            "Transaction ID": c.transaction_id,
            "Payee ID": c.payee_id,
            "Period": c.period,
            "Origin Period": c.origin_period,
            "Rule ID": c.rule_id,
            "Base Amount": str(_round(c.base_amount)),
            "Rate": str(c.rate),
            "Amount (rounded)": str(_round(c.commission_amount)),
            "Split %": str(c.split_pct),
            "Kind": c.kind,
            "Notes": c.notes,
        })
    return rows


def generate_payout_register(
    commissions: list[Commission],
    payees: list[Payee],
    plan: Plan,
    period: str,
    version: int,
) -> PayoutRegister:
    """Build a payout register from commission lines and payee data.

    Groups commissions by payee, sums gross, rounds to cents. Only includes
    lines whose period matches the given period.
    """
    payee_map: dict[str, Payee] = {p.id: p for p in payees}

    # Filter to this period only (exclude true-ups attributed to other periods)
    period_lines = [c for c in commissions if c.period == period]

    # Sum per payee
    by_payee: dict[str, Decimal] = {}
    for c in period_lines:
        by_payee[c.payee_id] = by_payee.get(c.payee_id, Decimal("0")) + c.commission_amount

    lines: list[RegisterLine] = []
    total = Decimal("0")
    for pid, gross in sorted(by_payee.items()):
        rounded = _round(gross)
        name = payee_map.get(pid)
        payee_name = name.name if name else pid
        lines.append(RegisterLine(
            payee_id=pid,
            payee_name=payee_name,
            gross_commission=gross,
            rounded_payout=rounded,
        ))
        total += rounded

    return PayoutRegister(
        plan_id=plan.plan_id,
        plan_name=plan.name,
        period=period,
        currency=plan.currency,
        version=version,
        lines=lines,
        total_payout=_round(total),
    )


def write_register(
    register: PayoutRegister,
    commissions: list[Commission],
    path: Path,
) -> None:
    """Write a payout register to an XLSX file.

    Produces two sheets:
      - "Payout Register": one row per payee + totals row
      - "Line Items": every commission line with rounded amounts
    """
    sheets: dict[str, list[dict[str, Any]]] = {
        "Payout Register": register.to_rows(),
        "Line Items": _line_items_sheet(commissions),
    }
    write_xlsx(path, sheets)


def register_path(
    app_dir: Path,
    plan_id: str,
    period: str,
    version: int,
) -> Path:
    """Return the canonical path for a payout register file."""
    safe_plan = plan_id.replace("/", "_").replace("\\", "_")
    safe_period = period.replace("/", "_").replace("\\", "_")
    return app_dir / f"payout_register_{safe_plan}_{safe_period}_v{version}.xlsx"

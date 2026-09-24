"""Statement totals and exports from immutable calculation records."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path

from icm_engine.database import Database
from icm_engine.models import Commission, Payee, Plan
from icm_engine.rounding import RoundingMode, parse_rounding_mode, round_money
from icm_engine.statements import StatementTheme, generate_statements


def payee_plan(payee_id: str, payees: list[Payee], plans: dict[str, Plan], multi: bool) -> Plan:
    if not multi:
        return next(iter(plans.values()))
    plan_id = next((p.plan_id for p in payees if p.id == payee_id), "")
    if plan_id not in plans:
        raise ValueError(f"No statement plan for payee {payee_id!r}")
    return plans[plan_id]


def payout_rows(
    commissions: list[Commission], payees: list[Payee], plans: dict[str, Plan], multi: bool,
) -> list[dict[str, str]]:
    totals: dict[tuple[str, str], Decimal] = {}
    policies: dict[str, Plan] = {}
    for c in commissions:
        plan = payee_plan(c.payee_id, payees, plans, multi)
        policies[c.payee_id] = plan
        rounding = plan.rounding
        amount = round_money(
            c.commission_amount,
            parse_rounding_mode(rounding.mode) if rounding else RoundingMode.HALF_UP,
            rounding.places if rounding else 2,
        )
        key = (c.payee_id, c.period)
        totals[key] = totals.get(key, Decimal(0)) + amount
    names = {p.id: p.name for p in payees}
    return [
        {"payee_id": pid, "name": names.get(pid, pid), "period": period,
         "currency": policies[pid].currency, "total": str(total)}
        for (pid, period), total in sorted(totals.items())
    ]


def export_saved_run(
    db: Database, ids: list[str], formats: tuple[str, ...], root: Path,
) -> bytes:
    records = [db.get_calculation(cid) for cid in ids]
    if any(r is None for r in records):
        raise LookupError("Calculation not found")
    snapshots = [json.loads(r["input_summary"]).get("statement_snapshot") for r in records if r]
    if not snapshots or any(not s for s in snapshots):
        raise ValueError("This older calculation has no statement snapshot. Calculate it again before exporting.")
    if len({s["run_id"] for s in snapshots}) != 1:
        raise ValueError("Choose calculations from one run.")
    snapshot = snapshots[0]
    payees = [Payee.model_validate(p) for p in snapshot["payees"]]
    plans = {key: Plan.model_validate(value) for key, value in snapshot["plans"].items()}
    lines = [Commission.model_validate(c) for cid in ids for c in db.get_commission_lines(cid)]
    rows = payout_rows(lines, payees, plans, snapshot["multi_plan"])
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for index, row in enumerate(rows):
            pid, period = row["payee_id"], row["period"]
            plan = payee_plan(pid, payees, plans, snapshot["multi_plan"])
            rounding = plan.rounding
            # Separate directories also prevent sanitized payee IDs colliding.
            folder = f"statements/{index + 1:04d}"
            files = generate_statements(
                [c for c in lines if c.payee_id == pid and c.period == period],
                payees, out_dir=root / folder, period=period, formats=formats,
                attainment=snapshot["attainment"], plan_name=plan.name,
                rounding_mode=parse_rounding_mode(rounding.mode) if rounding else RoundingMode.HALF_UP,
                rounding_places=rounding.places if rounding else 2,
                source_currency=plan.currency,
                theme=StatementTheme(currency_symbol=f"{plan.currency} "),
            )
            for file in files:
                zf.write(file.path, f"{folder}/{file.path.name}")
        summary = io.StringIO(newline="")
        writer = csv.DictWriter(summary, fieldnames=["payee_id", "name", "period", "currency", "total"])
        writer.writeheader()
        # CSV is for review, so neutralize spreadsheet formula prefixes in text.
        for row in rows:
            writer.writerow({k: ("'" + v if k in {"payee_id", "name"} and v.startswith(("=", "+", "-", "@")) else v)
                             for k, v in row.items()})
        zf.writestr("internal/payout-summary.csv", summary.getvalue())
        zf.writestr("internal/calculation-ids.json", json.dumps(ids))
    return archive.getvalue()

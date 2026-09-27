"""Statement totals and exports from immutable calculation records."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from icm_engine.database import Database
from icm_engine.models import Commission, Payee, Plan
from icm_engine.rounding import RoundingMode, parse_rounding_mode, round_money
from icm_engine.statements import StatementFile, StatementTheme, generate_statements


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


@dataclass
class SavedRun:
    """Everything a statement needs, read back from a run's frozen records."""

    snapshot: dict[str, Any]
    payees: list[Payee]
    plans: dict[str, Plan]
    lines: list[Commission]
    deals: list[dict[str, str]]


def load_saved_run(db: Database, ids: list[str]) -> SavedRun:
    records = [db.get_calculation(cid) for cid in ids]
    if any(r is None for r in records):
        raise LookupError("Calculation not found")
    summaries = [json.loads(r["input_summary"]) for r in records if r]
    snapshots = [s.get("statement_snapshot") for s in summaries]
    if not snapshots or any(not s for s in snapshots):
        raise ValueError("This older calculation has no statement snapshot. Calculate it again before exporting.")
    if len({s["run_id"] for s in snapshots}) != 1:
        raise ValueError("Choose calculations from one run.")
    snapshot = snapshots[0]
    # Deal details frozen with the run; runs saved before they were kept
    # simply show transaction ids.
    deals: dict[str, dict[str, str]] = {}
    for summary in summaries:
        deals.update(summary.get("statement_deals") or {})
    return SavedRun(
        snapshot=snapshot,
        payees=[Payee.model_validate(p) for p in snapshot["payees"]],
        plans={key: Plan.model_validate(value) for key, value in snapshot["plans"].items()},
        lines=[Commission.model_validate(c) for cid in ids for c in db.get_commission_lines(cid)],
        deals=[{"id": tid, **info} for tid, info in deals.items()],
    )


def write_saved_statements(
    run: SavedRun, payee_id: str, period: str, formats: tuple[str, ...], out_dir: Path,
) -> list[StatementFile]:
    """One payee's statement for one period of a saved run, as issued."""
    plan = payee_plan(payee_id, run.payees, run.plans, run.snapshot["multi_plan"])
    rounding = plan.rounding
    return generate_statements(
        [c for c in run.lines if c.payee_id == payee_id and c.period == period],
        run.payees, out_dir=out_dir, period=period, formats=formats,
        attainment=run.snapshot["attainment"], plan_name=plan.name,
        rounding_mode=parse_rounding_mode(rounding.mode) if rounding else RoundingMode.HALF_UP,
        rounding_places=rounding.places if rounding else 2,
        source_currency=plan.currency,
        theme=StatementTheme(currency_symbol=f"{plan.currency} "),
        transactions=run.deals,
    )


def render_saved_statement(db: Database, ids: list[str], payee_id: str, period: str, root: Path) -> str:
    """The interactive HTML statement exactly as the export would write it."""
    run = load_saved_run(db, ids)
    if not any(c.payee_id == payee_id and c.period == period for c in run.lines):
        raise LookupError(f"No statement for {payee_id} in {period}")
    files = write_saved_statements(run, payee_id, period, ("html",), root)
    return files[0].path.read_text(encoding="utf-8")


def export_saved_run(
    db: Database, ids: list[str], formats: tuple[str, ...], root: Path,
) -> bytes:
    run = load_saved_run(db, ids)
    rows = payout_rows(run.lines, run.payees, run.plans, run.snapshot["multi_plan"])
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for index, row in enumerate(rows):
            # Separate directories also prevent sanitized payee IDs colliding.
            folder = f"statements/{index + 1:04d}"
            files = write_saved_statements(run, row["payee_id"], row["period"], formats, root / folder)
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

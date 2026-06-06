from __future__ import annotations

import csv
import logging
from collections import defaultdict
from datetime import date as _date
from decimal import Decimal
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from icm_engine.database import Database, default_db_path
from icm_engine.engine import CalculationResult, CommissionEngine
from icm_engine.exceptions import MissingAPIKeyError, PlanGenerationError
from icm_engine.ledger import write_ledger_jsonl
from icm_engine.loader import load_payees, load_plan, load_transactions
from icm_engine.models import Commission, Payee, Plan, Transaction

app = typer.Typer()
db_app = typer.Typer(help="Database operations")
app.add_typer(db_app, name="db")
console = Console()


@app.callback(invoke_without_command=True)
def main(
    plan: str = typer.Option(None, "--plan", help="Path to plan YAML file (single-plan mode)"),
    plans: list[str] = typer.Option(
        None, "--plans", help="Paths to plan YAML files for multi-plan run"
    ),
    transactions: str = typer.Option(
        None, "--transactions", help="Path to transactions CSV or XLSX"
    ),
    payees: str = typer.Option(None, "--payees", help="Path to payees CSV or XLSX"),
    output: str = typer.Option(None, "--output", help="Output directory"),
    mapping: str = typer.Option(
        None, "--mapping", help="Path to column mapping YAML file"
    ),
    save_mapping: str = typer.Option(
        None, "--save-mapping", help="Write inferred mapping to this path"
    ),
    csv_output: bool = typer.Option(
        False, "--csv", help="Output CSV files instead of XLSX"
    ),
    no_db: bool = typer.Option(
        False, "--no-db", help="Skip database persistence"
    ),
    org: str = typer.Option(
        "default", "--org", help="Organization ID for multi-tenancy"
    ),
    db_path: str = typer.Option(
        None, "--db-path", help="Database file path (default: platform-specific)"
    ),
    effective_period: str = typer.Option(
        None, "--effective-period", help="Payout period for late transactions (default: current month)"
    ),
    allow_recalculate_locked: bool = typer.Option(
        True, "--allow-recalculate-locked/--no-allow-recalculate-locked",
        help="Allow recalculation of locked periods"
    ),
    adjustments_file: str = typer.Option(
        None, "--adjustments", help="Path to manual adjustments CSV"
    ),
    mbos_file: str = typer.Option(
        None, "--mbos", help="Path to MBOs/bonuses CSV"
    ),
) -> None:
    """Calculate commissions from a plan, transactions, and payees.

    Single-plan mode (--plan):
      icm --plan plan.yaml --transactions deals.csv --payees reps.csv --output out/

    Multi-plan mode (--plans or auto-resolve from DB):
      icm --plans plan_a.yaml plan_b.yaml --transactions deals.csv --payees reps.csv --output out/
      icm --transactions deals.csv --payees reps.csv --output out/   (resolves plans from DB)
    """
    if transactions is None or payees is None or output is None:
        return
    if plan is None and not plans and no_db:
        console.print("[red]Either --plan, --plans, or DB access is required[/red]")
        raise typer.Exit(code=1)

    _setup_logging()

    mapping_obj = None
    if mapping:
        from icm_engine.mapping import load_mapping as _load_mapping_file
        mapping_obj = _load_mapping_file(Path(mapping))

    txns, txn_mapping = load_transactions(transactions, mapping=mapping_obj)
    payee_list, payee_mapping = load_payees(payees, mapping=mapping_obj)

    # --- Resolve plans (single-plan or multi-plan) ---
    plan_library: dict[str, Plan] = {}
    single_plan_mode = plan is not None

    db: Database | None = None
    if not no_db:
        db = Database(db_path or str(default_db_path()), org_id=org)
        db.init()

    if single_plan_mode:
        p_obj = load_plan(plan)
        plan_library[p_obj.plan_id] = p_obj
    elif plans:
        for pf in plans:
            p_obj = load_plan(pf)
            plan_library[p_obj.plan_id] = p_obj
    elif db is not None:
        # Auto-resolve from DB using payee plan_ids
        try:
            plan_library = db.load_plan_library()
        except Exception:
            plan_library = {}
        if not plan_library:
            # Fall back: try loading a single plan from any payee's plan_id
            plan_ids_in_use = {p.plan_id for p in payee_list if p.plan_id}
            for pid in plan_ids_in_use:
                row = db.get_plan(pid)
                if row and row.get("yaml_content"):
                    import tempfile
                    from pathlib import Path as _Path
                    with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".yaml", delete=False, encoding="utf-8",
                    ) as tf:
                        tf.write(row["yaml_content"])
                        tf.flush()
                        p_obj = load_plan(_Path(tf.name))
                        plan_library[p_obj.plan_id] = p_obj

    if not plan_library:
        console.print("[red]No plans available. Provide --plan, --plans, or save plans to the DB.[/red]")
        raise typer.Exit(code=1)

    use_multi_plan = len(plan_library) > 1 or (
        not single_plan_mode and any(p.plan_id != (list(plan_library.keys())[0]) for p in payee_list)
    )

    if use_multi_plan:
        console.print(
            f"[cyan]Multi-plan run: {len(plan_library)} plan(s), "
            f"{len(payee_list)} payee(s), {len(txns)} transaction(s)[/cyan]"
        )

    # Filter-field typo guard
    from icm_engine.engine import check_filter_fields
    for p in plan_library.values():
        for rule in p.rules:
            f_source: str | None = getattr(rule, "filter", None)
            if f_source:
                unused = check_filter_fields(f_source, txns)
                for field in unused:
                    console.print(
                        f"[yellow]Warning:[/yellow] filter on rule [bold]{rule.id}[/bold] "
                        f"(plan [bold]{p.plan_id}[/bold]) references field "
                        f"[bold]{field!r}[/bold] which is neither a canonical field "
                        f"nor present in any transaction's metadata. It will never match."
                    )

    if txn_mapping:
        _print_mapping(txn_mapping)
    if payee_mapping:
        _print_mapping(payee_mapping)

    engine = CommissionEngine()

    # Determine locked periods and effective period
    locked_periods: set[str] = set()
    locked_by_plan: dict[str, set[str]] = {}
    eff_period: str | None = effective_period
    if db is not None:
        for pid in plan_library:
            period_status = db.get_period_status(pid)
            lp = {r["period"] for r in period_status if r.get("locked_calc_id") is not None}
            if lp:
                locked_by_plan[pid] = lp
                locked_periods |= lp

    txn_periods = {t.period for t in txns}
    locked_relevant = txn_periods & locked_periods

    if locked_relevant:
        if not allow_recalculate_locked:
            console.print(
                f"[red]Error: Some periods are locked: {sorted(locked_relevant)}. "
                f"Use --allow-recalculate-locked to proceed.[/red]"
            )
            raise typer.Exit(code=1)
        if eff_period is None:
            today = _date.today()
            eff_period = today.strftime("%Y-%m")
        console.print(
            f"[yellow]Recalculating with locked periods {sorted(locked_relevant)}. "
            f"Late transactions will be attributed to {eff_period}.[/yellow]"
        )

    # Load prior official commission lines for locked periods
    prior_commissions: list[Commission] | None = None
    prior_by_plan: dict[str, list[Commission]] = {}
    if locked_relevant and db is not None:
        prior_commissions = []
        for pid in plan_library:
            plan_priors: list[Commission] = []
            for period in locked_by_plan.get(pid, set()):
                official = db.get_official_calculation(pid, period)
                if official:
                    lines = db.get_commission_lines(official["id"])
                    for line in lines:
                        c = Commission(
                            transaction_id=line["transaction_id"],
                            payee_id=line["payee_id"],
                            period=line["period"],
                            origin_period=line.get("origin_period", ""),
                            rule_id=line["rule_id"],
                            base_amount=Decimal(line["base_amount"]),
                            rate=Decimal(line["rate"]),
                            commission_amount=Decimal(line["commission_amount"]),
                            notes=line.get("notes", ""),
                        )
                        plan_priors.append(c)
                        prior_commissions.append(c)
            if plan_priors:
                prior_by_plan[pid] = plan_priors

    # Load manual adjustments if provided
    adjustments_list = None
    if adjustments_file:
        from icm_engine.loader import load_adjustments
        adjustments_list = load_adjustments(adjustments_file)

    # Load MBOs if provided
    mbos_list = None
    if mbos_file:
        from icm_engine.loader import load_mbos
        mbos_list = load_mbos(mbos_file)

    if use_multi_plan:
        result = engine.calculate_run(
            plan_library, txns, payee_list,
            locked_periods=locked_by_plan if locked_by_plan else None,
            effective_period=eff_period if locked_relevant else None,
            prior_commissions=prior_by_plan if prior_by_plan else None,
            adjustments=adjustments_list,
            prior_draw_balances=None,
            mbos=mbos_list,
        )
    else:
        single_plan = list(plan_library.values())[0]
        result = engine.calculate(
            single_plan, txns, payee_list,
            locked_periods=locked_relevant if locked_relevant else None,
            effective_period=eff_period if locked_relevant else None,
            prior_commissions=prior_commissions,
            adjustments=adjustments_list,
            mbos=mbos_list,
        )

    # Persist to database
    if not no_db and db is not None:
        commissions = [c.model_dump() for c in result.commissions]
        ledger_dicts = [e.to_dict() for e in result.ledger]

        by_period: dict[str, list[dict[str, Any]]] = {}
        for c_dict in commissions:
            p = c_dict["period"]
            by_period.setdefault(p, []).append(c_dict)

        # Group commission lines by (plan, period) for per-plan persistence
        # In single-plan mode, all lines belong to the same plan.
        # In multi-plan mode, we assign lines to the plan of their payee's plan_id.
        payee_plan: dict[str, str] = {p.id: p.plan_id for p in payee_list}

        period_plan: dict[str, str] = {}
        for c_dict in commissions:
            p = c_dict["period"]
            pid = payee_plan.get(c_dict["payee_id"], list(plan_library.keys())[0])
            # Use the first plan_id seen for this period as the "primary" plan
            if p not in period_plan:
                period_plan[p] = pid

        calc_ids: dict[str, str] = {}
        for period_key, comms in sorted(by_period.items()):
            plan_for_period = period_plan.get(period_key, list(plan_library.keys())[0])
            calc_id = db.record_calculation(
                plan_for_period,
                period=period_key,
                input_summary={"txn_count": len(txns), "payee_count": len(payee_list)},
            )
            db.save_commission_lines(calc_id, comms)
            db.save_ledger_entries(calc_id, ledger_dicts)
            calc_ids[period_key] = calc_id

        # Persist transactions and link them to all calculations in this run
        txn_dicts = [t.model_dump() for t in txns]
        db.save_transactions(txn_dicts)
        all_txn_ids = [t.id for t in txns]
        for cid in calc_ids.values():
            db.link_transactions(cid, all_txn_ids)

        console.print("[green]Saved to database[/green]")
        for p, cid in sorted(calc_ids.items()):
            console.print(f"  [dim]{p}: {cid}[/dim]")

    # Write output files

    out_dir = Path(output)
    out_dir.mkdir(parents=True, exist_ok=True)

    use_xlsx = _is_xlsx(transactions) or _is_xlsx(payees)
    if csv_output or not use_xlsx:
        _write_commissions_csv(result.commissions, out_dir / "commissions.csv")
        _write_summary_csv(result.commissions, out_dir / "summary.csv")
    else:
        _write_commissions_xlsx(result.commissions, out_dir / "commissions.xlsx")
        _write_summary_xlsx(result.commissions, out_dir / "summary.xlsx")
    write_ledger_jsonl(result.ledger, out_dir / "ledger.jsonl")

    if save_mapping and (txn_mapping or payee_mapping):
        from icm_engine.mapping import save_mapping as _save_mapping_file
        save_path = Path(save_mapping)
        if txn_mapping and payee_mapping:
            # Save both mappings with descriptive suffixes
            stem, suffix = save_path.stem, save_path.suffix
            txn_path = save_path.with_name(f"{stem}_transactions{suffix}")
            payee_path = save_path.with_name(f"{stem}_payees{suffix}")
            _save_mapping_file(txn_mapping, txn_path)
            _save_mapping_file(payee_mapping, payee_path)
            console.print(
                f"[green]Saved mappings to {txn_path} and {payee_path}[/green]"
            )
        else:
            m = txn_mapping if txn_mapping is not None else payee_mapping
            assert m is not None
            _save_mapping_file(m, save_path)
            console.print(f"[green]Saved mapping to {save_mapping}[/green]")

    _print_summary(result, plan_library, txns, payee_list)


@app.command("serve")
def serve(
    host: str = typer.Option(
        "0.0.0.0", "--host", help="Bind address"
    ),
    port: int = typer.Option(8000, "--port", help="Listen port"),
) -> None:
    """Start the HTTP API server."""
    import uvicorn

    console.print(f"[cyan]Starting icm-engine API on {host}:{port}[/cyan]")
    uvicorn.run(
        "icm_engine.api:app",
        host=host,
        port=port,
        log_level="info",
    )


@app.command("plan-from-text")
def plan_from_text(
    description: str = typer.Argument(..., help="Natural-language plan description"),
    plan_id: str = typer.Option(
        None, "--plan-id", help="Override the plan_id"
    ),
    output: str = typer.Option(
        None, "--output", "-o", help="Write YAML to this path instead of stdout"
    ),
    model: str = typer.Option(
        None, "--model", help="LLM model override"
    ),
    api_key: str = typer.Option(
        None, "--api-key", help="API key (overrides env ICM_LLM_API_KEY / ANTHROPIC_API_KEY)"
    ),
    api_base_url: str = typer.Option(
        None, "--api-base-url", help="OpenAI-compatible base URL (e.g. https://api.openai.com)"
    ),
) -> None:
    """Generate a validated plan YAML from a natural-language description via LLM."""
    from icm_engine.ai.plan_author import generate_plan_from_text

    try:
        plan_obj = generate_plan_from_text(
            description, plan_id=plan_id, model=model,
            api_key=api_key, base_url=api_base_url,
            console=console,
        )
    except MissingAPIKeyError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1) from e
    except PlanGenerationError as e:
        console.print(f"[red]Plan generation failed after {e.attempts} attempts.[/red]")
        console.print(f"[red]Last validation error:[/red] {e.last_error}")
        console.print(f"[yellow]Last generated YAML:[/yellow]\n{e.last_yaml}")
        raise typer.Exit(code=1) from e

    yaml_text = _plan_to_yaml(plan_obj)

    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml_text, encoding="utf-8")
        console.print(
            f"[green]Generated plan with {len(plan_obj.rules)} rule(s). "
            f"Saved to {path}.[/green]"
        )
    else:
        console.print(yaml_text)

    _run_sanity_check(plan_obj)


@app.command("map")
def map_command(
    input_file: str = typer.Argument(..., help="Path to XLSX or CSV file"),
    target: str = typer.Option(
        ..., "--target", help="Target schema: 'transactions' or 'payees'"
    ),
    output: str = typer.Option(
        None, "--output", "-o", help="Save inferred mapping to YAML file"
    ),
) -> None:
    """Infer and display a column mapping for an input file."""
    from icm_engine.excel import read_xlsx_rows
    from icm_engine.mapping import infer_mapping, save_mapping

    p = Path(input_file)
    if not p.exists():
        console.print(f"[red]File not found: {input_file}[/red]")
        raise typer.Exit(code=1)

    if p.suffix.lower() == ".xlsx":
        headers, _ = read_xlsx_rows(p)
    else:
        with p.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = list(reader.fieldnames or [])

    if target not in ("transactions", "payees"):
        console.print("[red]--target must be 'transactions' or 'payees'[/red]")
        raise typer.Exit(code=1)

    colmap = infer_mapping(list(headers), target)

    table = Table(title=f"Column Mapping: {p.name}")
    table.add_column("Source", style="cyan")
    table.add_column("Target", style="green")
    table.add_column("Confidence", style="dim")
    table.add_column("Transform", style="yellow")

    for src, tgt in colmap.mappings.items():
        conf = colmap.confidence.get(src, 0)
        tform = colmap.transformations.get(tgt, "")
        table.add_row(src, tgt, f"{conf:.0%}", tform)

    if not colmap.mappings:
        console.print("[yellow]No confident mappings found.[/yellow]")
    else:
        console.print(table)

    if output:
        save_mapping(colmap, Path(output))
        console.print(f"[green]Saved mapping to {output}[/green]")


# ------------------------------------------------------------------
# Database commands
# ------------------------------------------------------------------


@db_app.command("init")
def db_init(
    db_path: str = typer.Option(
        None, "--db", help="Database path (default: platform app data dir)"
    ),
) -> None:
    """Initialize the SQLite database."""
    from icm_engine.database import Database, default_db_path

    path = Path(db_path) if db_path else default_db_path()
    db = Database(path)
    db.init()
    console.print(f"[green]Database initialized at {path}[/green]")


@db_app.command("plans")
def db_plans(
    db_path: str = typer.Option(
        None, "--db", help="Database path (default: platform app data dir)"
    ),
) -> None:
    """List saved plans."""
    from icm_engine.database import Database, default_db_path

    path = Path(db_path) if db_path else default_db_path()
    db = Database(path)
    plans = db.list_plans()

    if not plans:
        console.print("[dim]No plans saved.[/dim]")
        return

    table = Table(title="Saved Plans")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Updated", style="dim")

    for p in plans:
        table.add_row(p["id"], p["name"], p["updated_at"][:16])

    console.print(table)


@db_app.command("plan-save")
def db_plan_save(
    plan_file: str = typer.Argument(..., help="Path to plan YAML file"),
    name: str = typer.Option(
        None, "--name", help="Plan name (default: filename stem)"
    ),
    db_path: str = typer.Option(
        None, "--db", help="Database path (default: platform app data dir)"
    ),
) -> None:
    """Save a plan YAML file to the database."""
    from icm_engine.database import Database, default_db_path

    file_path = Path(plan_file)
    if not file_path.exists():
        console.print(f"[red]File not found: {plan_file}[/red]")
        raise typer.Exit(code=1)

    yaml_content = file_path.read_text(encoding="utf-8")
    plan_name = name or file_path.stem

    path = Path(db_path) if db_path else default_db_path()
    db = Database(path)
    db.init()

    # Validate the plan before saving
    try:
        load_plan(str(file_path))
    except ValueError as e:
        console.print(f"[red]Invalid plan: {e}[/red]")
        raise typer.Exit(code=1) from e

    plan_id = db.save_plan(plan_name, yaml_content)
    console.print(f"[green]Saved plan '{plan_name}' ({plan_id})[/green]")


@db_app.command("plan-delete")
def db_plan_delete(
    plan_id: str = typer.Argument(..., help="Plan ID to delete"),
    db_path: str = typer.Option(
        None, "--db", help="Database path (default: platform app data dir)"
    ),
) -> None:
    """Delete a plan from the database."""
    from icm_engine.database import Database, default_db_path

    path = Path(db_path) if db_path else default_db_path()
    db = Database(path)
    deleted = db.delete_plan(plan_id)
    if deleted:
        console.print(f"[green]Deleted plan {plan_id}[/green]")
    else:
        console.print(f"[yellow]Plan {plan_id} not found[/yellow]")


@db_app.command("payees")
def db_payees(
    plan_id: str = typer.Option(
        None, "--plan", help="Filter payees by plan ID"
    ),
    db_path: str = typer.Option(
        None, "--db", help="Database path (default: platform app data dir)"
    ),
) -> None:
    """List saved payees."""
    from icm_engine.database import Database, default_db_path

    path = Path(db_path) if db_path else default_db_path()
    db = Database(path)
    payees = db.list_payees(plan_id=plan_id)

    if not payees:
        console.print("[dim]No payees saved.[/dim]")
        return

    table = Table(title="Saved Payees")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Quota", style="yellow")
    table.add_column("Plan", style="dim")

    for p in payees:
        table.add_row(p["id"], p["name"], p["quota"], p["plan_id"])

    console.print(table)


@db_app.command("settings")
def db_settings(
    key: str = typer.Option(
        None, "--key", help="Setting key to get/set"
    ),
    value: str = typer.Option(
        None, "--value", help="Value to set (omit to read)"
    ),
    delete: bool = typer.Option(
        False, "--delete", help="Delete the setting"
    ),
    db_path: str = typer.Option(
        None, "--db", help="Database path (default: platform app data dir)"
    ),
) -> None:
    """Get, set, or delete a setting."""
    from icm_engine.database import Database, default_db_path

    if key is None:
        console.print("[red]--key is required[/red]")
        raise typer.Exit(code=1)

    path = Path(db_path) if db_path else default_db_path()
    db = Database(path)
    db.init()

    if delete:
        removed = db.delete_setting(key)
        if removed:
            console.print(f"[green]Deleted setting '{key}'[/green]")
        else:
            console.print(f"[yellow]Setting '{key}' not found[/yellow]")
    elif value is not None:
        db.set_setting(key, value)
        console.print(f"[green]Set '{key}' = '{value}'[/green]")
    else:
        val = db.get_setting(key)
        if val is not None:
            console.print(f"{key} = {val}")
        else:
            console.print(f"[dim]Setting '{key}' not set[/dim]")


# ------------------------------------------------------------------
# Trace command
# ------------------------------------------------------------------


@app.command("trace")
def trace_command(
    txn: str = typer.Option(..., "--txn", help="Transaction ID to trace"),
    payee: str = typer.Option(..., "--payee", help="Payee ID to trace"),
    calculation_id: str = typer.Option(
        None, "--calculation-id", help="Filter ledger by calculation ID"
    ),
    db_path: str = typer.Option(
        None, "--db", help="Database path (default: platform app data dir)"
    ),
) -> None:
    """Show how one order flowed through the plan, derived from the audit ledger."""
    from icm_engine.database import Database, default_db_path
    from icm_engine.trace import build_order_trace

    path = Path(db_path) if db_path else default_db_path()
    db = Database(path)
    entries = db.query_ledger(payee_id=payee, calculation_id=calculation_id, limit=10000)
    if not entries:
        console.print(f"[yellow]No ledger entries found for {txn}/{payee}[/yellow]")
        raise typer.Exit(code=0)

    trace = build_order_trace(txn, payee, entries)

    # Header
    console.print()
    table = Table(title=f"Trace: {txn} → {payee}")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")
    if trace.order:
        for k, v in trace.order.items():
            table.add_row(k, str(v))
    console.print(table)

    # Steps
    for step in trace.steps:
        status_color = "green" if step.status == "matched" else "yellow"
        status_icon = "\u2713" if step.status == "matched" else "\u2717"
        console.print(
            f"\n[bold {status_color}]{status_icon} Rule {step.rule_id}: "
            f"{step.status.upper()}[/bold {status_color}]"
        )
        if step.reason:
            console.print(f"  [dim]Reason: {step.reason}[/dim]")

        for ev in step.events:
            console.print(f"  [dim]{ev['event_type']}:[/dim] {ev['human_readable']}")

    # Total
    console.print(f"\n[bold]Total commission: ${trace.total}[/bold]")
    console.print(f"[dim]{trace.summary}[/dim]")


# ------------------------------------------------------------------
# Statements command
# ------------------------------------------------------------------


@app.command("statements")
def statements_command(
    plan: str = typer.Option(..., "--plan", help="Path to plan YAML file"),
    transactions: str = typer.Option(..., "--transactions", help="Path to transactions CSV/XLSX"),
    payees: str = typer.Option(..., "--payees", help="Path to payees CSV/XLSX"),
    output: str = typer.Option(..., "--output", help="Output directory for statements"),
    period: str = typer.Option(None, "--period", help="Filter to this period (YYYY-MM)"),
    formats: str = typer.Option("xlsx", "--format", help="Comma-separated: xlsx,html,pdf"),
    emit_zero: bool = typer.Option(False, "--emit-zero", help="Emit $0 statements for payees with no lines"),
    adjustments_file: str = typer.Option(
        None, "--adjustments", help="Path to manual adjustments CSV"
    ),
    mbos_file: str = typer.Option(
        None, "--mbos", help="Path to MBOs/bonuses CSV"
    ),
) -> None:
    """Generate per-rep commission statements in the requested formats."""
    from icm_engine.engine import CommissionEngine
    from icm_engine.loader import load_payees, load_plan, load_transactions
    from icm_engine.statements import generate_statements

    plan_obj = load_plan(plan)
    txn_list, _ = load_transactions(transactions)
    payee_list, _ = load_payees(payees)

    adjustments_list = None
    if adjustments_file:
        from icm_engine.loader import load_adjustments
        adjustments_list = load_adjustments(adjustments_file)

    mbos_list = None
    if mbos_file:
        from icm_engine.loader import load_mbos
        mbos_list = load_mbos(mbos_file)

    result = CommissionEngine().calculate(
        plan_obj, txn_list, payee_list,
        adjustments=adjustments_list, mbos=mbos_list,
    )

    fmt_tuple = tuple(f.strip() for f in formats.split(","))
    out_dir = Path(output)

    files = generate_statements(
        result.commissions,
        payee_list,
        out_dir=out_dir,
        period=period,
        formats=fmt_tuple,
        emit_zero=emit_zero,
    )

    console.print(f"[green]Generated {len(files)} statement file(s) in {out_dir}[/green]")
    for f in files:
        console.print(f"  [dim]{f.payee_id}[/dim] → {f.path.name}")


# ------------------------------------------------------------------
# Distribute command
# ------------------------------------------------------------------


@app.command("distribute")
def distribute_command(
    statements_dir: str = typer.Option(
        ..., "--statements-dir", help="Directory of generated statement files"
    ),
    payees_file: str = typer.Option(
        ..., "--payees", help="Path to payees CSV/XLSX with email column"
    ),
    subject: str = typer.Option(
        "Your commission statement for {period}", "--subject",
        help="Subject template ({name}, {period}, {total})",
    ),
    body: str = typer.Option(
        "Hi {name},\n\nHere is your commission statement for {period}.\n\n- OpenIncent",
        "--body", help="Body template ({name}, {period}, {total})",
    ),
    send: bool = typer.Option(
        False, "--send", help="Actually send emails (default: preview only)"
    ),
    smtp_host: str = typer.Option("", "--smtp-host", help="SMTP server hostname"),
    smtp_port: int = typer.Option(587, "--smtp-port"),
    smtp_user: str = typer.Option("", "--smtp-user"),
    smtp_pass: str = typer.Option("", "--smtp-pass"),
    smtp_from: str = typer.Option("", "--smtp-from"),
    smtp_tls: bool = typer.Option(True, "--smtp-tls/--no-smtp-tls"),
    smtp_from_env: bool = typer.Option(
        False, "--smtp-from-env",
        help="Read SMTP credentials from ICM_SMTP_HOST/PORT/USER/PASS/FROM/TLS env vars",
    ),
    eml_out: str = typer.Option(
        "", "--eml", help="Write .eml files to this directory"
    ),
    mail_merge: str = typer.Option(
        "", "--mail-merge", help="Write mail-merge CSV to this path"
    ),
) -> None:
    """Distribute per-rep statements. Safe by default — nothing is sent without --send."""
    import os as _os

    from icm_engine.distribute import (
        SmtpConfig,
        build_messages,
        send_via_smtp,
        write_eml,
        write_mail_merge_csv,
    )
    from icm_engine.loader import load_payees

    # Load payees for email lookup
    payee_list, _ = load_payees(payees_file)

    # Scan statements directory
    stmt_dir = Path(statements_dir)
    if not stmt_dir.exists():
        console.print(f"[red]Statements directory not found: {statements_dir}[/red]")
        raise typer.Exit(code=1)

    statement_files: list[dict[str, str]] = []
    for p in sorted(stmt_dir.glob("statement_*.*")):
        # Parse payee_id from filename: statement_{payee_id}_{period}.{ext}
        name = p.stem  # statement_P001_all
        parts = name.split("_")
        if len(parts) >= 2:
            pid = parts[1]
            period = "_".join(parts[2:]) if len(parts) > 2 else "all"
            ext = p.suffix.lstrip(".")
            statement_files.append({
                "payee_id": pid,
                "period": period,
                "path": str(p.absolute()),
                "fmt": ext,
            })

    if not statement_files:
        console.print("[yellow]No statement files found[/yellow]")
        raise typer.Exit(code=0)

    # Build messages
    messages, skipped = build_messages(
        statement_files, payee_list,
        subject_template=subject, body_template=body,
    )

    # Print preview
    console.print(f"\n[bold]Recipients:[/bold] {len(messages)} sendable, {len(skipped)} skipped")
    for m in messages:
        console.print(f"  [green]{m.payee_id}[/green] → {m.to} ({len(m.attachments)} file(s))")
    for s in skipped:
        console.print(f"  [yellow]{s.payee_id}[/yellow] skipped: {s.reason}")

    # Write artifacts
    if eml_out:
        eml_dir = Path(eml_out)
        paths = write_eml(messages, eml_dir)
        console.print(f"\n[green]Wrote {len(paths)} .eml file(s) to {eml_dir}[/green]")
    if mail_merge:
        write_mail_merge_csv(messages, Path(mail_merge))
        console.print(f"[green]Wrote mail-merge CSV to {mail_merge}[/green]")

    # Send
    if send:
        config = SmtpConfig(host="")
        if smtp_from_env:
            config.host = _os.environ.get("ICM_SMTP_HOST", "")
            config.port = int(_os.environ.get("ICM_SMTP_PORT", "587"))
            config.user = _os.environ.get("ICM_SMTP_USER", "")
            config.password = _os.environ.get("ICM_SMTP_PASS", "")
            config.from_addr = _os.environ.get("ICM_SMTP_FROM", "")
            config.use_tls = _os.environ.get("ICM_SMTP_TLS", "1") != "0"
        else:
            config.host = smtp_host
            config.port = smtp_port
            config.user = smtp_user
            config.password = smtp_pass
            config.from_addr = smtp_from
            config.use_tls = smtp_tls

        if not config.host:
            console.print("[red]SMTP host required. Use --smtp-host or --smtp-from-env[/red]")
            raise typer.Exit(code=1)

        results = send_via_smtp(messages, config, dry_run=False)
        console.print("\n[bold]Send results:[/bold]")
        for r in results:
            icons = {"sent": "[green]✓[/green]", "failed": "[red]✗[/red]", "skipped": "[yellow]-[/yellow]"}
            icon = icons.get(r.status, "?")
            console.print(f"  {icon} {r.payee_id}: {r.status}" + (f" — {r.reason}" if r.reason else ""))
    else:
        console.print("\n[dim]Preview mode — use --send to actually email.[/dim]")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _plan_to_yaml(plan: Plan) -> str:
    from typing import Any as _Any

    import yaml as _yaml

    def _dec_repr(dumper: _yaml.Dumper, value: Decimal) -> _Any:
        return dumper.represent_str(str(value))

    class _DecimalDumper(_yaml.Dumper):
        pass

    _DecimalDumper.add_representer(Decimal, _dec_repr)
    return _yaml.dump(
        plan.model_dump(),
        Dumper=_DecimalDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )


def _run_sanity_check(plan: Plan) -> None:
    try:
        payee = Payee(
            id="SANITY-P001",
            name="Sanity Check Payee",
            quota=Decimal("100000"),
            plan_id=plan.plan_id,
            effective_from=_date.today(),
        )
        txns = [
            Transaction(
                id="SANITY-T001",
                payee_id="SANITY-P001",
                deal_id="SANITY-D001",
                period="2026-04",
                amount=Decimal("10000"),
                product=None,
                close_date=_date.today(),
            ),
            Transaction(
                id="SANITY-T002",
                payee_id="SANITY-P001",
                deal_id="SANITY-D002",
                period="2026-04",
                amount=Decimal("5000"),
                product="Enterprise",
                close_date=_date.today(),
            ),
            Transaction(
                id="SANITY-T003",
                payee_id="SANITY-P001",
                deal_id="SANITY-D003",
                period="2026-04",
                amount=Decimal("20000"),
                product=None,
                close_date=_date.today(),
            ),
        ]
        engine = CommissionEngine()
        result = engine.calculate(plan, txns, [payee])
        if result.commissions:
            c = result.commissions[0]
            console.print(
                f"[dim]Sanity check: {result.commissions[0].transaction_id} → "
                f"${c.commission_amount} via {c.rule_id}[/dim]"
            )
        else:
            console.print("[dim]Sanity check: no commissions generated[/dim]")
    except Exception as e:
        console.print(f"[dim]Sanity check skipped: {e}[/dim]")





def _is_xlsx(path_str: str) -> bool:
    return Path(path_str).suffix.lower() == ".xlsx"


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def _print_mapping(m: Any) -> None:
    for src, tgt in m.mappings.items():
        conf = m.confidence.get(src, 0)
        tform = m.transformations.get(tgt, "")
        extra = f" → {tform}" if tform else ""
        console.print(f"[dim]Mapped '{src}' → {tgt} (confidence: {conf:.0%}){extra}[/dim]")


def _write_commissions_xlsx(commissions: list[Commission], path: Path) -> None:
    from icm_engine.excel import write_xlsx

    rows = [
        {
            "transaction_id": c.transaction_id,
            "payee_id": c.payee_id,
            "period": c.period,
            "rule_id": c.rule_id,
            "base_amount": str(c.base_amount),
            "rate": str(c.rate),
            "commission_amount": str(c.commission_amount),
            "notes": c.notes,
        }
        for c in commissions
    ]
    write_xlsx(path, {"commissions": rows})


def _write_summary_xlsx(commissions: list[Commission], path: Path) -> None:
    from icm_engine.excel import write_xlsx

    by_payee_period: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for c in commissions:
        key = (c.payee_id, c.period)
        by_payee_period[key] += c.commission_amount

    rows = [
        {"payee_id": payee_id, "period": period, "total_commission": str(total)}
        for (payee_id, period), total in sorted(by_payee_period.items())
    ]
    write_xlsx(path, {"summary": rows})


def _write_commissions_csv(commissions: list[Commission], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "transaction_id",
                "payee_id",
                "period",
                "rule_id",
                "base_amount",
                "rate",
                "commission_amount",
                "notes",
            ]
        )
        for c in commissions:
            writer.writerow(
                [
                    c.transaction_id,
                    c.payee_id,
                    c.period,
                    c.rule_id,
                    str(c.base_amount),
                    str(c.rate),
                    str(c.commission_amount),
                    c.notes,
                ]
            )


def _write_summary_csv(commissions: list[Commission], path: Path) -> None:
    by_payee_period: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for c in commissions:
        key = (c.payee_id, c.period)
        by_payee_period[key] += c.commission_amount

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["payee_id", "period", "total_commission"])
        for (payee_id, period), total in sorted(by_payee_period.items()):
            writer.writerow([payee_id, period, str(total)])


def _print_summary(
    result: CalculationResult,
    plan_library: dict[str, Plan],
    txns: list[Transaction],
    payee_list: list[Payee],
) -> None:
    total_commission = sum(c.commission_amount for c in result.commissions)
    unique_payees = len({c.payee_id for c in result.commissions})

    table = Table(title="Commission Calculation Result")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    if len(plan_library) == 1:
        plan_obj: Plan = list(plan_library.values())[0]
        table.add_row("Plan", plan_obj.name)
    else:
        plan_names = ", ".join(p.name for p in plan_library.values())
        table.add_row("Plans", plan_names)
    table.add_row("Total commission", str(total_commission))
    table.add_row("Payees (in output)", str(unique_payees))
    table.add_row("Transactions processed", str(len(txns)))
    table.add_row("Commission lines", str(len(result.commissions)))
    table.add_row("Ledger entries", str(len(result.ledger)))

    console.print(table)

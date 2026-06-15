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
from icm_engine.run import LockedPeriodError, RunContext, execute, persist

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
      icm --transactions deals.csv --output out/                     (uses saved roster + plans from DB)
    """
    if transactions is None or output is None:
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

    if payees is not None:
        payee_list, payee_mapping = load_payees(payees, mapping=mapping_obj)
        using_saved_roster = False
    else:
        if no_db:
            console.print("[red]--payees is required when --no-db is set[/red]")
            raise typer.Exit(code=1)
        using_saved_roster = True

    # --- Resolve plans (single-plan or multi-plan) ---
    plan_library: dict[str, Plan] = {}
    single_plan_mode = plan is not None

    db: Database | None = None
    if not no_db:
        db = Database(db_path or str(default_db_path()), org_id=org)
        db.init()

    # Load payees from saved roster if no file provided
    if using_saved_roster:
        assert db is not None
        payee_list = db.load_saved_roster()
        if not payee_list:
            console.print("[red]No saved payees in the database. Import a roster first or use --payees.[/red]")
            raise typer.Exit(code=1)
        if effective_period:
            from datetime import date as _date
            period_dt = _date.fromisoformat(effective_period + "-01")
            payee_list = [
                p for p in payee_list
                if p.effective_from is None or p.effective_from <= period_dt
            ]
            payee_list = [
                p for p in payee_list
                if p.effective_to is None or p.effective_to >= period_dt
            ]
        console.print(f"[dim]Using saved roster: {len(payee_list)} active payees[/dim]")
        payee_mapping = None

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
    from icm_engine.filter_parser import check_filter_fields
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

    if no_db or db is None:
        # No-DB mode: direct engine call, no locking or persistence
        engine = CommissionEngine()
        if use_multi_plan:
            result = engine.calculate_run(
                plan_library, txns, payee_list,
                adjustments=adjustments_list, mbos=mbos_list,
            )
        else:
            result = engine.calculate(
                list(plan_library.values())[0], txns, payee_list,
                adjustments=adjustments_list, mbos=mbos_list,
            )
    else:
        # Full pipeline via shared run orchestration
        ctx = RunContext(
            plan_library=plan_library,
            transactions=txns,
            payees=payee_list,
            db=db,
            single_plan_mode=single_plan_mode,
            effective_period=effective_period,
            allow_recalculate_locked=allow_recalculate_locked,
            adjustments=adjustments_list,
            mbos=mbos_list,
            using_saved_roster=using_saved_roster,
        )
        try:
            ctx.resolve()
        except LockedPeriodError as e:
            console.print(
                f"[red]Error: Some periods are locked: {e.locked_periods}. "
                f"Use --allow-recalculate-locked to proceed.[/red]"
            )
            raise typer.Exit(code=1) from e

        if ctx.locked_relevant:
            console.print(
                f"[yellow]Recalculating with locked periods {sorted(ctx.locked_relevant)}. "
                f"Late transactions will be attributed to {ctx.effective_period}.[/yellow]"
            )

        result = execute(ctx)
        calc_ids = persist(ctx, result)

        console.print("[green]Saved to database[/green]")
        for p, cid in sorted(calc_ids.items()):
            console.print(f"  [dim]{p}: {cid}[/dim]")

    # Write output files

    out_dir = Path(output)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Read currency settings from DB
    rates_json: str | None = None
    if db is not None:
        rates_json = db.get_setting("exchange_rates")
    from icm_engine.currency import load_rates
    _output_rates = load_rates(rates_json)

    # Determine source and reporting currency from the plan(s)
    _src_currency = ""
    _rpt_currency = ""
    if plan_library:
        first_plan = list(plan_library.values())[0]
        _src_currency = first_plan.currency
        _rpt_currency = (first_plan.reporting_currency or "").strip()

    use_xlsx = _is_xlsx(transactions) or _is_xlsx(payees)
    if csv_output or not use_xlsx:
        _write_commissions_csv(result.commissions, out_dir / "commissions.csv",
                               rates=_output_rates, source_currency=_src_currency,
                               reporting_currency=_rpt_currency)
        _write_summary_csv(result.commissions, out_dir / "summary.csv",
                           rates=_output_rates, source_currency=_src_currency,
                           reporting_currency=_rpt_currency)
    else:
        _write_commissions_xlsx(result.commissions, out_dir / "commissions.xlsx",
                                rates=_output_rates, source_currency=_src_currency,
                                reporting_currency=_rpt_currency)
        _write_summary_xlsx(result.commissions, out_dir / "summary.xlsx",
                            rates=_output_rates, source_currency=_src_currency,
                            reporting_currency=_rpt_currency)
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


@app.command("check-plan")
def check_plan_command(
    plan: str = typer.Argument(..., help="Path to plan YAML file"),
) -> None:
    """Run a plan's declared assertions (executable invariants) through the engine."""
    from icm_engine.loader import load_plan
    from icm_engine.plan_check import check_plan

    plan_obj = load_plan(plan)
    results = check_plan(plan_obj)
    if not results:
        console.print(
            "[yellow]No assertions defined on this plan.[/yellow] "
            "Add an `assertions:` list to catch payout mistranscriptions."
        )
        raise typer.Exit(code=0)

    for r in results:
        if r.passed:
            console.print(f"[green]PASS[/green] {r.name}: payout {r.actual} == {r.expected}")
        else:
            console.print(
                f"[red]FAIL[/red] {r.name}: expected {r.expected}, got {r.actual}"
                + (f" ({r.detail})" if r.detail else "")
            )
    failed = [r for r in results if not r.passed]
    if failed:
        console.print(f"\n[red]{len(failed)} of {len(results)} assertion(s) FAILED.[/red]")
        raise typer.Exit(code=1)
    console.print(f"\n[green]All {len(results)} assertion(s) passed.[/green]")


@app.command("lint")
def lint_command(
    plan: str = typer.Argument(..., help="Path to plan YAML file"),
    strict: bool = typer.Option(False, "--strict", help="Exit non-zero on warnings too, not just errors"),
) -> None:
    """Statically check a plan for common comp-design mistakes (no data needed)."""
    from icm_engine.loader import load_plan
    from icm_engine.plan_lint import lint_plan

    findings = lint_plan(load_plan(plan))
    if not findings:
        console.print("[green]No issues found.[/green]")
        raise typer.Exit(code=0)

    color = {"error": "red", "warning": "yellow", "info": "cyan"}
    for f in findings:
        c = color.get(f.severity, "")
        console.print(f"[{c}]{f.severity.upper()}[/{c}] {f.code}: {f.message}")

    errors = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warning")
    info = len(findings) - errors - warnings
    console.print(f"\n{len(findings)} finding(s): {errors} error(s), {warnings} warning(s), {info} info.")
    if errors or (strict and warnings):
        raise typer.Exit(code=1)


@app.command("validate")
def validate_command(
    plan: str = typer.Option(..., "--plan", help="Path to plan YAML"),
    transactions: str = typer.Option(..., "--transactions", help="Transactions CSV/XLSX"),
    payees: str = typer.Option(..., "--payees", help="Payees CSV/XLSX"),
) -> None:
    """Check input data for duplicates, eligibility gaps, and missing FX rates."""
    from icm_engine.loader import load_payees, load_plan, load_transactions
    from icm_engine.validate import validate_run

    plan_obj = load_plan(plan)
    txns, _ = load_transactions(transactions)
    payee_list, _ = load_payees(payees)

    rates = None
    try:
        from icm_engine.currency import load_rates
        from icm_engine.database import Database, default_db_path
        rates = load_rates(Database(default_db_path()).get_setting("exchange_rates")) or None
    except Exception:
        rates = None

    issues = validate_run(plan_obj, txns, payee_list, rates=rates)
    if not issues:
        console.print("[green]No validation issues found.[/green]")
        raise typer.Exit(code=0)

    for iss in issues:
        color = "red" if iss.severity == "error" else "yellow"
        console.print(f"[{color}]{iss.severity.upper()}[/{color}] [{iss.code}] {iss.message}")
    errors = [i for i in issues if i.severity == "error"]
    console.print(
        f"\n{len(issues)} issue(s): {len(errors)} error(s), "
        f"{len(issues) - len(errors)} warning(s)."
    )
    raise typer.Exit(code=1 if errors else 0)


def _reconcile_compute(
    plan: str | None,
    plans: list[str] | None,
    transactions: str,
    payees: str,
) -> dict[tuple[str, str], Decimal]:
    """Recompute commissions (no DB) and aggregate per (payee, period)."""
    from icm_engine.reconcile import commissions_to_totals

    txns, _ = load_transactions(transactions)
    payee_list, _ = load_payees(payees)
    engine = CommissionEngine()
    if plan:
        result = engine.calculate(load_plan(plan), txns, payee_list)
    else:
        plan_library: dict[str, Plan] = {}
        for pp in plans or []:
            po = load_plan(pp)
            plan_library[po.plan_id] = po
        result = engine.calculate_run(plan_library, txns, payee_list)
    return commissions_to_totals(result.commissions)


@app.command("reconcile")
def reconcile_command(
    paid: str = typer.Option(
        ..., "--paid", help="CSV of what was actually paid (payee, period, amount)"
    ),
    commissions: str = typer.Option(
        None, "--commissions", help="Engine commissions.csv to reconcile (else recompute)"
    ),
    plan: str = typer.Option(None, "--plan", help="Plan YAML (single-plan recompute)"),
    plans: list[str] = typer.Option(None, "--plans", help="Plan YAMLs (multi-plan recompute)"),
    transactions: str = typer.Option(
        None, "--transactions", help="Transactions CSV/XLSX (with --plan/--plans)"
    ),
    payees: str = typer.Option(
        None, "--payees", help="Payees CSV/XLSX (with --plan/--plans)"
    ),
    period: str = typer.Option(
        None, "--period", help="Default period for paid rows that lack one"
    ),
    tolerance: str = typer.Option("0.01", "--tolerance", help="Match tolerance (currency)"),
    output: str = typer.Option(
        None, "--output", "-o", help="Write the full reconciliation to this CSV"
    ),
    strict: bool = typer.Option(
        False, "--strict", help="Exit non-zero if any discrepancy is found"
    ),
) -> None:
    """Reconcile computed commissions against what was actually paid.

    Point at an existing run's output:
      icm reconcile --commissions out/commissions.csv --paid payroll.csv
    or recompute from a plan and diff in one step:
      icm reconcile --plan plan.yaml --transactions deals.csv --payees reps.csv --paid payroll.csv
    """
    from icm_engine.reconcile import (
        load_commission_totals_csv,
        load_paid_csv,
        reconcile_totals,
        write_report_csv,
    )

    tol = Decimal(tolerance)

    if commissions:
        computed = load_commission_totals_csv(commissions)
    elif (plan or plans) and transactions and payees:
        computed = _reconcile_compute(plan, plans, transactions, payees)
    else:
        console.print(
            "[red]Provide --commissions FILE, or --plan/--plans with "
            "--transactions and --payees to recompute.[/red]"
        )
        raise typer.Exit(code=1)

    paid_totals = load_paid_csv(paid, default_period=period)
    report = reconcile_totals(computed, paid_totals, tolerance=tol)

    if report.has_discrepancies:
        style = {
            "underpaid": "red", "missing": "red",
            "overpaid": "yellow", "unexpected": "yellow", "match": "green",
        }
        table = Table(title="Commission reconciliation")
        table.add_column("Payee", style="cyan")
        table.add_column("Period")
        table.add_column("Computed", justify="right")
        table.add_column("Paid", justify="right")
        table.add_column("Delta", justify="right")
        table.add_column("Status")
        for ln in report.discrepancies:
            s = style.get(ln.status, "")
            table.add_row(
                ln.payee_id, ln.period, f"{ln.computed:,.2f}", f"{ln.paid:,.2f}",
                f"{ln.delta:+,.2f}", f"[{s}]{ln.status}[/{s}]",
            )
        console.print(table)
        parts = [f"{n} {s}" for s, n in sorted(report.counts().items()) if s != "match"]
        console.print(f"\n[bold]{len(report.discrepancies)} discrepancy(ies):[/bold] " + ", ".join(parts))
        console.print(
            f"Owed to payees: [red]{report.total_owed_to_payees:,.2f}[/red]  |  "
            f"Paid above plan: [yellow]{report.total_overpaid:,.2f}[/yellow]  |  "
            f"Net: {report.net_delta:+,.2f}"
        )
    else:
        console.print(
            f"[green]All {len(report.lines)} payee-periods reconcile (+/-{tol}).[/green]"
        )

    if output:
        write_report_csv(report, output)
        console.print(f"[green]Wrote {output}[/green]")

    if strict and report.has_discrepancies:
        raise typer.Exit(code=1)


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
        status_icon = "+" if step.status == "matched" else "-"
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
    rounding: str | None = typer.Option(
        None, "--rounding",
        help="Rounding mode: half-up, half-even, floor, ceil, none. Defaults to the plan's policy.",
    ),
) -> None:
    """Generate per-rep commission statements in the requested formats."""
    from icm_engine.engine import CommissionEngine
    from icm_engine.loader import load_payees, load_plan, load_transactions
    from icm_engine.rounding import parse_rounding_mode
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

    # Read currency settings
    from icm_engine.currency import load_rates
    from icm_engine.database import Database, default_db_path
    try:
        _sdb = Database(default_db_path())
        _rates_json = _sdb.get_setting("exchange_rates")
        _rates = load_rates(_rates_json)
    except Exception:
        _rates = {}

    _src_cur = plan_obj.currency
    _rpt_cur = (plan_obj.reporting_currency or "").strip()

    # Rounding precedence: explicit --rounding flag > plan policy > half-up/2dp.
    _plan_round = plan_obj.rounding
    if rounding is not None:
        _rmode = parse_rounding_mode(rounding)
        _rplaces = _plan_round.places if _plan_round else 2
    elif _plan_round is not None:
        _rmode = parse_rounding_mode(_plan_round.mode)
        _rplaces = _plan_round.places
    else:
        _rmode = parse_rounding_mode("half-up")
        _rplaces = 2

    files = generate_statements(
        result.commissions,
        payee_list,
        out_dir=out_dir,
        period=period,
        formats=fmt_tuple,
        emit_zero=emit_zero,
        rounding_mode=_rmode,
        rounding_places=_rplaces,
        rates=_rates if _rates else None,
        reporting_currency=_rpt_cur,
        source_currency=_src_cur,
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
# Register command
# ------------------------------------------------------------------


@app.command("register")
def register_command(
    plan_id: str = typer.Option(..., "--plan", help="Plan ID"),
    period: str = typer.Option(..., "--period", help="Period (YYYY-MM)"),
    version: int = typer.Option(
        None, "--version", help="Calculation version (default: latest locked)"
    ),
    output: str = typer.Option(
        None, "--output", "-o", help="Output path (default: app data dir)"
    ),
    db_path: str = typer.Option(
        None, "--db", help="Database path (default: platform-specific)"
    ),
) -> None:
    """Generate or open the finance payout register for a locked period.

    The register is an XLSX workbook with two sheets:
      - Payout Register: one row per payee with rounded payouts + totals
      - Line Items: every commission line with rounded amounts
    """
    from icm_engine.database import Database, default_db_path
    from icm_engine.models import Commission, Payee
    from icm_engine.payout_register import (
        generate_payout_register,
        register_path,
        write_register,
    )

    db_path_obj = Path(db_path) if db_path else default_db_path()
    db = Database(db_path_obj)
    app_dir = db_path_obj.parent

    # Find the calculation
    calcs = db.list_calculations(plan_id=plan_id, period=period, limit=5)
    if not calcs:
        console.print(f"[red]No calculations found for {plan_id}/{period}[/red]")
        raise typer.Exit(code=1)

    if version is not None:
        matching = [c for c in calcs if c.get("version") == version]
    else:
        # Prefer locked calculation, else latest
        locked_calc = db.get_official_calculation(plan_id, period)
        if locked_calc:
            matching = [locked_calc]
        else:
            matching = [calcs[0]]

    if not matching:
        console.print("[red]No matching calculation found[/red]")
        raise typer.Exit(code=1)

    calc = matching[0]
    calc_id = calc["id"]
    calc_version = calc.get("version", 1)

    # Check if register already exists
    rp = register_path(app_dir, plan_id, period, calc_version)
    if rp.exists() and output is None:
        console.print(f"[green]Register already exists:[/green] {rp}")
        console.print("[dim]Use --output to regenerate to a different path.[/dim]")
        return

    # Load data from DB
    raw_lines = db.get_commission_lines(calc_id)
    if not raw_lines:
        console.print("[yellow]No commission lines found for this calculation[/yellow]")
        raise typer.Exit(code=1)

    commissions = [
        Commission(
            transaction_id=li.get("transaction_id", ""),
            payee_id=li.get("payee_id", ""),
            period=li.get("period", ""),
            origin_period=li.get("origin_period", ""),
            rule_id=li.get("rule_id", ""),
            base_amount=Decimal(str(li.get("base_amount", "0"))),
            rate=Decimal(str(li.get("rate", "0"))),
            commission_amount=Decimal(str(li.get("commission_amount", "0"))),
            notes=str(li.get("notes", "")),
        )
        for li in raw_lines
    ]

    payee_rows = db.list_payees()
    payees = [
        Payee(
            id=pr["id"], name=pr["name"],
            quota=Decimal(pr.get("quota", "0")),
            plan_id=pr.get("plan_id", ""),
            effective_from=_date.today(),
        )
        for pr in payee_rows
    ]

    # Load plan
    plan_row = db.get_plan(plan_id)
    if not plan_row or not plan_row.get("yaml_content"):
        console.print(f"[red]Plan {plan_id} not found in database[/red]")
        raise typer.Exit(code=1)

    import tempfile
    from pathlib import Path as _Path
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8",
    ) as tf:
        tf.write(plan_row["yaml_content"])
        tf.flush()
        plan_obj = load_plan(_Path(tf.name))

    # Generate
    register = generate_payout_register(
        commissions, payees, plan_obj, period, calc_version,
    )
    out_path = Path(output) if output else rp
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_register(register, commissions, out_path)

    console.print(f"[green]Payout register written:[/green] {out_path}")
    console.print(f"  Payees: {len(register.lines)}")
    console.print(f"  Total payout: {register.total_payout} {plan_obj.currency}")
    console.print(f"  Version: {calc_version}")


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


def _write_commissions_xlsx(commissions: list[Commission], path: Path,
                            rounding_mode: str = "half-up",
                            rates: dict[str, Decimal] | None = None,
                            source_currency: str = "",
                            reporting_currency: str = "") -> None:
    from icm_engine.excel import write_xlsx
    from icm_engine.currency import convert as _convert, needs_conversion
    from icm_engine.rounding import parse_rounding_mode, round_money

    rm = parse_rounding_mode(rounding_mode)
    do_convert = needs_conversion(source_currency, reporting_currency) and rates
    _rates = rates or {}
    _rc = (reporting_currency or "").strip().upper()

    def _amt(v: Decimal) -> str:
        if do_convert:
            try:
                return str(_convert(v, source_currency, _rc, _rates, rounding=rm))
            except KeyError:
                pass
        return str(round_money(v, rm))

    rows = [
        {
            "transaction_id": c.transaction_id,
            "payee_id": c.payee_id,
            "period": c.period,
            "rule_id": c.rule_id,
            "base_amount": _amt(c.base_amount),
            "rate": str(c.rate),
            "commission_amount": _amt(c.commission_amount),
            "notes": c.notes,
        }
        for c in commissions
    ]
    write_xlsx(path, {"commissions": rows})


def _write_summary_xlsx(commissions: list[Commission], path: Path,
                        rounding_mode: str = "half-up",
                        rates: dict[str, Decimal] | None = None,
                        source_currency: str = "",
                        reporting_currency: str = "") -> None:
    from icm_engine.excel import write_xlsx
    from icm_engine.currency import convert as _convert, needs_conversion
    from icm_engine.rounding import parse_rounding_mode, round_money

    rm = parse_rounding_mode(rounding_mode)
    do_convert = needs_conversion(source_currency, reporting_currency) and rates
    _rates = rates or {}
    _rc = (reporting_currency or "").strip().upper()

    def _amt(v: Decimal) -> str:
        if do_convert:
            try:
                return str(_convert(v, source_currency, _rc, _rates, rounding=rm))
            except KeyError:
                pass
        return str(round_money(v, rm))
    by_payee_period: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for c in commissions:
        key = (c.payee_id, c.period)
        by_payee_period[key] += c.commission_amount

    rows = [
        {"payee_id": payee_id, "period": period, "total_commission": _amt(total)}
        for (payee_id, period), total in sorted(by_payee_period.items())
    ]
    write_xlsx(path, {"summary": rows})


def _write_commissions_csv(commissions: list[Commission], path: Path,
                           rounding_mode: str = "half-up",
                           rates: dict[str, Decimal] | None = None,
                           source_currency: str = "",
                           reporting_currency: str = "") -> None:
    from icm_engine.currency import convert as _convert, needs_conversion
    from icm_engine.rounding import parse_rounding_mode, round_money

    rm = parse_rounding_mode(rounding_mode)
    do_convert = needs_conversion(source_currency, reporting_currency) and rates
    _rates = rates or {}
    _rc = (reporting_currency or "").strip().upper()

    def _amt(v: Decimal) -> str:
        if do_convert:
            try:
                return str(_convert(v, source_currency, _rc, _rates, rounding=rm))
            except KeyError:
                pass
        return str(round_money(v, rm))

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
                    _amt(c.base_amount),
                    str(c.rate),
                    _amt(c.commission_amount),
                    c.notes,
                ]
            )


def _write_summary_csv(commissions: list[Commission], path: Path,
                       rounding_mode: str = "half-up",
                       rates: dict[str, Decimal] | None = None,
                       source_currency: str = "",
                       reporting_currency: str = "") -> None:
    from icm_engine.currency import convert as _convert, needs_conversion
    from icm_engine.rounding import parse_rounding_mode, round_money

    rm = parse_rounding_mode(rounding_mode)
    do_convert = needs_conversion(source_currency, reporting_currency) and rates
    _rates = rates or {}
    _rc = (reporting_currency or "").strip().upper()

    def _amt(v: Decimal) -> str:
        if do_convert:
            try:
                return str(_convert(v, source_currency, _rc, _rates, rounding=rm))
            except KeyError:
                pass
        return str(round_money(v, rm))
    by_payee_period: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for c in commissions:
        key = (c.payee_id, c.period)
        by_payee_period[key] += c.commission_amount

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["payee_id", "period", "total_commission"])
        for (payee_id, period), total in sorted(by_payee_period.items()):
            writer.writerow([payee_id, period, _amt(total)])


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

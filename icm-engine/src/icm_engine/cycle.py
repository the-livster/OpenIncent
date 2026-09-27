"""The pay cycle as plain functions: prepare a run, lock a period.

The HTTP API, the CLI and the assistant tools all start runs and lock periods.
These lived inside the API's request handlers, so any other surface would have
had to copy them - and a copy is how the desktop app once ran none of the
pre-flight checks. One implementation, whatever started the run.

Refusals raise `CycleError` with the HTTP status and JSON detail the API has
always answered with, so the API's responses are unchanged.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from icm_engine.database import Database
from icm_engine.models import Commission, Payee, Plan, Transaction
from icm_engine.run import LockedPeriodError, RunContext, preflight
from icm_engine.validate import ValidationIssue

logger = logging.getLogger(__name__)


class CycleError(Exception):
    """A run or lock refused before anything changed.

    `status` is the HTTP status the API answers with and `detail` the JSON-safe
    payload it sends (a dict, or a plain message)."""

    def __init__(self, status: int, detail: dict[str, Any] | str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail if isinstance(detail, str) else str(detail.get("error", "Refused")))

    @property
    def problems(self) -> list[str]:
        """Every specific problem in the refusal, as sentences."""
        if isinstance(self.detail, str):
            return []
        issues = [str(i.get("message", "")) for i in self.detail.get("issues", []) if isinstance(i, dict)]
        return issues + [str(m) for m in self.detail.get("missing", [])]


@dataclass
class PreparedRun:
    """A run whose inputs passed every check, ready to execute."""

    ctx: RunContext
    issues: list[ValidationIssue] = field(default_factory=list)   # warnings that did not block
    using_saved_roster: bool = False


def resolve_payees(
    db: Database, payees: list[Payee] | None, effective_period: str | None,
) -> list[Payee]:
    """The payees for a run: the ones given, or the saved roster, narrowed to
    the people active in the effective period when one is named."""
    if payees is not None:
        return payees
    roster = db.load_saved_roster()
    if not roster:
        raise CycleError(400, {"error": "No saved payees and no payee file uploaded. Import a roster first."})
    if effective_period:
        try:
            period_dt = date.fromisoformat(effective_period + "-01")
        except ValueError:
            raise CycleError(400, {
                "error": f"Invalid effective_period {effective_period!r}; expected YYYY-MM.",
            }) from None
        roster = [p for p in roster if p.effective_from is None or p.effective_from <= period_dt]
        roster = [p for p in roster if p.effective_to is None or p.effective_to >= period_dt]
    if not roster:
        raise CycleError(400, {"error": f"No active payees for period {effective_period or 'any'}."})
    return roster


def resolve_plans(db: Database, plan: Plan | None, payees: list[Payee]) -> dict[str, Plan]:
    """The plan library for a run: the one plan given, or each payee's plan
    from the saved library, refusing a payee whose plan cannot be loaded."""
    if plan is not None:
        return {plan.plan_id: plan}

    plan_library, plan_failures = db.load_plan_library_detailed()
    if not plan_library:
        raise CycleError(400, {
            "error": "No plans in library. Upload a plan file or save plans to the DB first.",
        })

    missing_plans: dict[str, set[str]] = {}
    for p in payees:
        if p.plan_id and p.plan_id not in plan_library:
            missing_plans.setdefault(p.plan_id, set()).add(p.id)
    if missing_plans:
        available = sorted(plan_library.keys())
        # A plan that is saved but unparseable is absent from the library, so
        # blaming the payee sends the user to fix a roster that is already
        # right. Say which of the two it is.
        broken = {pid: plan_failures[pid] for pid in missing_plans if pid in plan_failures}
        details = []
        for plan_id, pids in sorted(missing_plans.items()):
            if plan_id in broken:
                details.append(
                    f"Payees {sorted(pids)} reference '{plan_id}', which is "
                    f"saved but could not be loaded: {broken[plan_id]}"
                )
            else:
                details.append(f"Payees {sorted(pids)} reference '{plan_id}'")
        raise CycleError(400, {
            "error": (
                "Some payees reference plans that could not be loaded."
                if broken else
                "Some payees reference plans not in the library."
            ),
            "missing": details,
            "available_plans": available,
            "broken_plans": sorted(broken),
            "hint": (
                "Fix the plan YAML for: " + ", ".join(sorted(broken))
                if broken else
                "Import the missing plans or reassign the payees to an available plan."
            ),
        })
    return plan_library


def prepare_run(
    db: Database,
    *,
    transactions: list[Transaction],
    payees: list[Payee] | None = None,
    plan: Plan | None = None,
    adjustments: list[Any] | None = None,
    mbos: list[Any] | None = None,
    effective_period: str | None = None,
    allow_recalculate_locked: bool = True,
    allow_unknown_payees: bool = False,
) -> PreparedRun:
    """Resolve and check everything a run needs, without calculating.

    `payees=None` runs the saved roster; `plan=None` runs each payee on their
    own plan from the saved library. Refusals raise CycleError."""
    payee_list = resolve_payees(db, payees, effective_period)
    plan_library = resolve_plans(db, plan, payee_list)

    # Same checks on every surface. Without these the desktop app silently
    # paid deals credited to ids that were not on the roster.
    issues = preflight(plan_library, transactions, payee_list)
    blocking = [
        i for i in issues
        if i.severity == "error" and not (i.code == "unknown_payee" and allow_unknown_payees)
    ]
    if blocking:
        raise CycleError(400, {
            "error": "Input problems must be resolved before calculating",
            "issues": [{"severity": i.severity, "code": i.code, "message": i.message} for i in issues],
            "hint": "Set allow_unknown_payees=true to pay unrostered ids anyway.",
        })

    ctx = RunContext(
        plan_library=plan_library,
        transactions=transactions,
        payees=payee_list,
        db=db,
        single_plan_mode=plan is not None,
        effective_period=effective_period,
        allow_recalculate_locked=allow_recalculate_locked,
        adjustments=adjustments,
        mbos=mbos,
        using_saved_roster=payees is None,
    )
    try:
        ctx.resolve()
    except LockedPeriodError as e:
        raise CycleError(409, {
            "error": "Some periods are locked",
            "locked_periods": e.locked_periods,
            "hint": "Set allow_recalculate_locked=true to create draft versions",
        }) from e

    warnings = [i for i in issues if i not in blocking]
    return PreparedRun(ctx=ctx, issues=warnings, using_saved_roster=payees is None)


def lock_period(
    db: Database, plan_id: str, period: str, calculation_id: str | None = None,
    *, locked_by: str = "", reason: str = "",
) -> dict[str, Any]:
    """Lock a period to a calculation (the latest, by default) and write the
    payout register beside the database. The register is supplementary: a
    failure to write it never undoes the lock."""
    if calculation_id is None:
        calcs = db.list_calculations(plan_id=plan_id, period=period, limit=1)
        if not calcs:
            raise CycleError(404, "No calculations found for this plan and period")
        calculation_id = calcs[0]["id"]
    else:
        record = db.get_calculation(calculation_id)
        # Locking March to a February run would make February's numbers
        # official for March. Refuse a calculation from another plan or period.
        if record is None:
            raise CycleError(404, "Calculation not found")
        if record["plan_id"] != plan_id or record["period"] != period:
            raise CycleError(409, (
                f"Calculation {calculation_id} is {record['plan_id']} {record['period']}, "
                f"not {plan_id} {period}"
            ))
    if not db.lock_period(plan_id, period, calculation_id, locked_by=locked_by, reason=reason):
        raise CycleError(409, "Period already locked")

    register_path_str: str | None = None
    try:
        register_path_str = _write_register(db, plan_id, period, calculation_id)
    except Exception:
        logger.warning("Failed to generate payout register for %s/%s", plan_id, period, exc_info=True)

    return {
        "plan_id": plan_id, "period": period, "calculation_id": calculation_id,
        "status": "locked",
        "register_path": register_path_str,
    }


def _write_register(db: Database, plan_id: str, period: str, calculation_id: str) -> str | None:
    from icm_engine.loader import load_plan
    from icm_engine.payout_register import generate_payout_register, register_path, write_register

    plan_row = db.get_plan(plan_id)
    plan_obj = None
    if plan_row and plan_row.get("yaml_content"):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as tf:
            tf.write(plan_row["yaml_content"])
        try:
            plan_obj = load_plan(Path(tf.name))
        finally:
            Path(tf.name).unlink(missing_ok=True)

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
        for li in db.get_commission_lines(calculation_id)
    ]
    payees = [
        Payee(
            id=pr["id"], name=pr["name"],
            quota=Decimal(pr.get("quota", "0")),
            plan_id=pr.get("plan_id", ""),
            effective_from=(date.today() if not pr.get("effective_from")
                            else date.fromisoformat(str(pr["effective_from"])[:10])),
        )
        for pr in db.list_payees()
    ]
    if plan_obj is None or not commissions:
        return None
    calcs = db.list_calculations(plan_id=plan_id, period=period, limit=1)
    version = calcs[0].get("version", 1) if calcs else 1
    register = generate_payout_register(commissions, payees, plan_obj, period, version)
    path = register_path(Path(db.path).parent, plan_id, period, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_register(register, commissions, path)
    return str(path)

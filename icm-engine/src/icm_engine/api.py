from __future__ import annotations

import logging
import os
import tempfile
from datetime import date as _date
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.routing import APIRouter
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from icm_engine.database import Database, default_db_path
from icm_engine.engine import CommissionEngine
from icm_engine.ledger import LedgerEntry
from icm_engine.loader import load_payees, load_plan, load_transactions
from icm_engine.models import Commission, Payee, Plan
from icm_engine.rounding import RoundingMode, parse_rounding_mode, round_money
from icm_engine.run import LockedPeriodError, RunContext, execute, persist, preflight

app = FastAPI(title="icm-engine")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app.add_middleware(SlowAPIMiddleware)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Log the full traceback server-side; never leak internals to the client.
    logging.getLogger(__name__).exception(
        "Unhandled error handling %s %s", request.method, request.url.path
    )
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )

v1 = APIRouter(prefix="/v1")

_db_cache: dict[tuple[str, str], Database] = {}


def _get_db(org_id: str = "default") -> Database:
    path = os.environ.get("ICM_DB_PATH", str(default_db_path()))
    key = (path, org_id)
    if key not in _db_cache:
        db = Database(path, org_id=org_id)
        db.init()
        _db_cache[key] = db
    return _db_cache[key]


def get_org(authorization: str | None = Header(None)) -> str:
    """Extract org_id from Bearer token.

    If ICM_REQUIRE_AUTH is set, unauthenticated requests get a 401.
    Otherwise falls back to 'default' org (desktop / single-user mode).
    """
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        db = _get_db()
        org = db.validate_api_key(token)
        if org:
            return org
    if os.environ.get("ICM_REQUIRE_AUTH", "").lower() in ("1", "true", "yes"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return "default"


# ------------------------------------------------------------------
# Unversioned health
# ------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ------------------------------------------------------------------
# Serialization helper
# ------------------------------------------------------------------

def _serialize(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    return obj


# ------------------------------------------------------------------
# v1: Calculate
# ------------------------------------------------------------------

@v1.post("/calculate")
async def calculate(
    plan: UploadFile | None = File(None),  # noqa: B008
    transactions: UploadFile = File(...),  # noqa: B008
    payees: UploadFile | None = File(None),  # noqa: B008 — optional: saved roster used when absent
    adjustments: UploadFile | None = File(None),  # noqa: B008
    mbos: UploadFile | None = File(None),  # noqa: B008
    effective_period: str | None = None,
    allow_recalculate_locked: bool = True,
    allow_unknown_payees: bool = False,
    org: str = Depends(get_org),
) -> dict[str, Any]:
    """Calculate commissions.

    Single-plan mode: upload a plan file.
    Multi-plan mode: omit the plan file — resolves each payee's plan from
    the saved DB library by payee.plan_id.

    With a payee file: one-off / first-time import (file-based).
    Without a payee file: uses the saved roster from the DB.
    """
    db = _get_db(org)

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        files: dict[str, Path] = {}

        # Transactions (always required)
        content = await transactions.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail={
                "error": "Transactions file exceeds limit",
            })
        ext = Path(transactions.filename or "").suffix or ".csv"
        txn_path = root / f"transactions{ext}"
        txn_path.write_bytes(content)
        files["transactions"] = txn_path

        try:
            txn_list, _ = load_transactions(txn_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"error": "Invalid transactions file", "detail": str(e)}) from e

        # Payees: file-based or saved roster
        using_saved_roster = payees is None
        if payees is not None:
            content = await payees.read()
            if len(content) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail={"error": "Payees file exceeds limit"})
            ext = Path(payees.filename or "").suffix or ".csv"
            pee_path = root / f"payees{ext}"
            pee_path.write_bytes(content)
            files["payees"] = pee_path
            try:
                payee_list, _ = load_payees(pee_path)
            except ValueError as e:
                raise HTTPException(status_code=400, detail={"error": "Invalid payees file", "detail": str(e)}) from e
        else:
            # Load from saved roster
            payee_list = db.load_saved_roster()
            if not payee_list:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "No saved payees and no payee file uploaded. Import a roster first."},
                )
            # Filter by eligibility: only payees active for the run period
            if effective_period:
                from datetime import date as _date
                try:
                    period_dt = _date.fromisoformat(effective_period + "-01")
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail={"error": f"Invalid effective_period {effective_period!r}; expected YYYY-MM."},
                    ) from None
                payee_list = [
                    p for p in payee_list
                    if p.effective_from is None or p.effective_from <= period_dt
                ]
                payee_list = [
                    p for p in payee_list
                    if p.effective_to is None or p.effective_to >= period_dt
                ]
            if not payee_list:
                raise HTTPException(
                    status_code=400,
                    detail={"error": f"No active payees for period {effective_period or 'any'}."},
                )

        db = _get_db(org)

        # --- Resolve plan(s) ---
        plan_library: dict[str, Plan] = {}
        single_plan_mode = plan is not None

        if single_plan_mode:
            assert plan is not None  # type guard
            content = await plan.read()
            if len(content) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail={"error": "Plan file exceeds limit"})
            ext = Path(plan.filename or "").suffix or ".yaml"
            plan_path = root / f"plan{ext}"
            plan_path.write_bytes(content)
            try:
                plan_obj = load_plan(plan_path)
            except ValueError as e:
                raise HTTPException(status_code=400, detail={"error": "Invalid plan file", "detail": str(e)}) from e
            plan_library[plan_obj.plan_id] = plan_obj
        else:
            # Multi-plan: resolve from DB based on payee plan_ids
            plan_library = db.load_plan_library()
            if not plan_library:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "No plans in library. Upload a plan file or save plans to the DB first."},
                )

        if not plan_library:
            raise HTTPException(status_code=400, detail={"error": "No plan available"})

        # Validate that every payee's plan_id exists in the library
        if not single_plan_mode:
            missing_plans: dict[str, set[str]] = {}
            for p in payee_list:
                if p.plan_id and p.plan_id not in plan_library:
                    missing_plans.setdefault(p.plan_id, set()).add(p.id)
            if missing_plans:
                available = sorted(plan_library.keys())
                details = []
                for plan_id, pids in sorted(missing_plans.items()):
                    details.append(f"Payees {sorted(pids)} reference '{plan_id}'")
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "Some payees reference plans not in the library.",
                        "missing": details,
                        "available_plans": available,
                        "hint": "Import the missing plans or reassign the payees to an available plan.",
                    },
                )

        # Parse optional adjustments and MBOs from uploads (API-specific)
        adjustments_list = None
        if adjustments is not None:
            adj_bytes = await adjustments.read()
            if adj_bytes:
                adj_path = root / "adjustments.csv"
                adj_path.write_bytes(adj_bytes)
                from icm_engine.loader import load_adjustments
                adjustments_list = load_adjustments(adj_path)
        mbos_list = None
        if mbos is not None:
            mbo_bytes = await mbos.read()
            if mbo_bytes:
                mbo_path = root / "mbos.csv"
                mbo_path.write_bytes(mbo_bytes)
                from icm_engine.loader import load_mbos
                mbos_list = load_mbos(mbo_path)

        # --- Delegate to shared run orchestration ---
        # Same checks the CLI runs. Without these the desktop app silently paid
        # deals credited to ids that were not on the roster.
        issues = preflight(plan_library, txn_list, payee_list)
        blocking = [
            i for i in issues
            if i.severity == "error"
            and not (i.code == "unknown_payee" and allow_unknown_payees)
        ]
        if blocking:
            raise HTTPException(status_code=400, detail={
                "error": "Input problems must be resolved before calculating",
                "issues": [
                    {"severity": i.severity, "code": i.code, "message": i.message}
                    for i in issues
                ],
                "hint": "Set allow_unknown_payees=true to pay unrostered ids anyway.",
            })

        ctx = RunContext(
            plan_library=plan_library,
            transactions=txn_list,
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
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Some periods are locked",
                    "locked_periods": e.locked_periods,
                    "hint": "Set allow_recalculate_locked=true to create draft versions",
                },
            ) from e

        try:
            result = execute(ctx)
            calc_ids = persist(ctx, result)
        except Exception as e:
            import traceback as _tb
            raise HTTPException(status_code=500, detail={
                "error": "Calculation failed",
                "detail": str(e),
                "traceback": _tb.format_exc(),
            }) from e

        # Resolve each payee's display-rounding policy from their plan (if set).
        # Rounding is display-only; the stored/calculated values stay exact.
        _payee_policy: dict[str, tuple[RoundingMode, int]] = {}
        for _pp in payee_list:
            _plan = plan_library.get(_pp.plan_id)
            if _plan is not None and _plan.rounding is not None:
                _payee_policy[_pp.id] = (
                    parse_rounding_mode(_plan.rounding.mode), _plan.rounding.places,
                )

        commissions = []
        summary: dict[str, Decimal] = {}
        for c in result.commissions:
            d = c.model_dump()
            amt = c.commission_amount
            pol = _payee_policy.get(c.payee_id)
            if pol is not None:
                amt = round_money(amt, pol[0], pol[1])
                d["commission_amount"] = amt
                d["base_amount"] = round_money(c.base_amount, pol[0], pol[1])
            commissions.append(d)
            # Summary totals the ROUNDED lines so the UI reconciles to them.
            summary[c.payee_id] = summary.get(c.payee_id, Decimal("0")) + amt

        ledger_dicts = [e.to_dict() for e in result.ledger]

        return cast(dict[str, Any], _serialize({
            "calculation_ids": calc_ids,
            "commissions": commissions,
            "ledger": ledger_dicts,
            "summary": {k: str(v) for k, v in summary.items()},
            "draw_balances": {k: str(v) for k, v in result.draw_balances.items()},
            "effective_period": effective_period,
            "locked_periods": sorted(ctx.locked_relevant) if ctx.locked_relevant else [],
            "using_saved_roster": using_saved_roster,
            "attainment": [
                {
                    "payee_id": a.payee_id,
                    "period": a.period,
                    "bookings": a.bookings,
                    "quota": a.quota,
                    "attainment_pct": a.attainment_pct,
                }
                for a in result.attainment
            ],
        }))


# ------------------------------------------------------------------
# v1: Plan from text (AI)
# ------------------------------------------------------------------

class PlanFromTextRequest(BaseModel):
    description: str
    plan_id: str | None = None
    api_key: str | None = None
    base_url: str | None = None


@v1.post("/plan-from-text")
async def plan_from_text(
    req: PlanFromTextRequest, org: str = Depends(get_org),
) -> dict[str, Any]:
    from icm_engine.ai.plan_author import generate_plan_from_text
    from icm_engine.exceptions import MissingAPIKeyError, PlanGenerationError

    # Resolve API key: explicit request key > server-stored setting
    resolved_key = req.api_key or _get_db(org).get_setting("anthropic_api_key")
    if not resolved_key:
        raise HTTPException(
            status_code=400,
            detail={"error": "No Anthropic API key configured. Set it in Settings or pass it with the request."},
        )

    old_key = os.environ.get("ICM_LLM_API_KEY")
    os.environ["ICM_LLM_API_KEY"] = resolved_key
    try:
        plan = generate_plan_from_text(
            req.description, plan_id=req.plan_id or None, base_url=req.base_url,
        )
    except MissingAPIKeyError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)}) from e
    except PlanGenerationError as e:
        raise HTTPException(status_code=422, detail={
            "error": "Plan generation failed", "detail": str(e),
            "last_yaml": e.last_yaml, "last_error": e.last_error, "attempts": e.attempts,
        }) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Unexpected error", "detail": str(e)}) from e
    finally:
        if old_key is not None:
            os.environ["ICM_LLM_API_KEY"] = old_key
        elif "ICM_LLM_API_KEY" in os.environ:
            del os.environ["ICM_LLM_API_KEY"]

    import yaml as _yaml
    yaml_text = _yaml.dump(
        _serialize(plan.model_dump()), default_flow_style=False, allow_unicode=True, sort_keys=False,
    )
    return {"yaml": yaml_text, "plan": _serialize(plan.model_dump())}


# ------------------------------------------------------------------
# v1: Plans CRUD
# ------------------------------------------------------------------

class PlanSaveRequest(BaseModel):
    name: str
    yaml_content: str
    plan_id: str | None = None
    description: str = ""


@v1.get("/plans")
def list_plans(org: str = Depends(get_org)) -> list[dict[str, Any]]:
    return _get_db(org).list_plans()


@v1.get("/plans/{plan_id}")
def get_plan(plan_id: str, org: str = Depends(get_org)) -> dict[str, Any]:
    plan = _get_db(org).get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


@v1.post("/plans")
def save_plan(req: PlanSaveRequest, org: str = Depends(get_org)) -> dict[str, Any]:
    plan_id = _get_db(org).save_plan(req.name, req.yaml_content, plan_id=req.plan_id, description=req.description)
    return {"id": plan_id}


@v1.delete("/plans/{plan_id}")
def delete_plan(plan_id: str, org: str = Depends(get_org)) -> dict[str, str]:
    if not _get_db(org).delete_plan(plan_id):
        raise HTTPException(status_code=404, detail="Plan not found")
    return {"status": "deleted"}


# ------------------------------------------------------------------
# v1: Payees CRUD
# ------------------------------------------------------------------


class PayeeSaveRequest(BaseModel):
    name: str
    quota: str = "0"
    quotas: dict[str, str] | None = None  # per-window quota overrides
    plan_id: str = ""
    effective_from: str = ""
    effective_to: str | None = None
    email: str | None = None
    ramp_months: int | None = None
    ramp_schedule: str | None = None  # space-separated decimals
    draw_amount: str | None = None
    draw_recoverable: bool = False
    category_quotas: dict[str, str] | None = None
    manager_id: str = ""
    manager_override: str | None = None
    team_id: str = ""


@v1.get("/payees")
def list_payees_all(org: str = Depends(get_org)) -> list[dict[str, Any]]:
    return _get_db(org).list_payees()


@v1.get("/payees/{payee_id}")
def get_payee(payee_id: str, org: str = Depends(get_org)) -> dict[str, Any]:
    payee = _get_db(org).get_payee(payee_id)
    if payee is None:
        raise HTTPException(status_code=404, detail="Payee not found")
    return payee


@v1.put("/payees/{payee_id}")
def upsert_payee(payee_id: str, req: PayeeSaveRequest, org: str = Depends(get_org)) -> dict[str, Any]:
    db = _get_db(org)
    ramp_json = None
    if req.ramp_months and req.ramp_schedule:
        import json as _json
        schedule = [str(Decimal(v.strip())) for v in req.ramp_schedule.split() if v.strip()]
        ramp_json = _json.dumps({"months": req.ramp_months, "schedule": schedule})
    draw_json = None
    if req.draw_amount:
        import json as _json
        draw_json = _json.dumps({"amount": req.draw_amount, "recoverable": req.draw_recoverable})
    cat_json = None
    if req.category_quotas:
        import json as _json
        cat_json = _json.dumps(req.category_quotas)
    quotas_json = "{}"
    if req.quotas:
        import json as _json
        quotas_json = _json.dumps(req.quotas)
    db.save_payee(
        payee_id, req.name, req.quota, req.plan_id,
        req.effective_from, req.effective_to,
        quotas=quotas_json, ramp=ramp_json, draw=draw_json, email=req.email,
        category_quotas=cat_json,
        manager_id=req.manager_id, manager_override=req.manager_override, team_id=req.team_id,
    )
    return {"status": "saved", "id": payee_id}


@v1.delete("/payees/{payee_id}")
def delete_payee(payee_id: str, org: str = Depends(get_org)) -> dict[str, str]:
    if not _get_db(org).delete_payee(payee_id):
        raise HTTPException(status_code=404, detail="Payee not found")
    return {"status": "deleted"}


class PayeeImportRequest(BaseModel):
    replace: bool = False  # if True, wipe roster before import


@v1.post("/payees/import")
async def import_payees(
    file: UploadFile = File(...),  # noqa: B008
    replace: bool = Form(False),
    org: str = Depends(get_org),
) -> dict[str, Any]:
    """Bulk import payees from a CSV or XLSX file. Merges by default; set replace=true to wipe first."""
    import tempfile as _tf

    from icm_engine.loader import load_payees

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail={"error": "File too large"})

    ext = Path(file.filename or "").suffix or ".csv"
    with _tf.NamedTemporaryFile(suffix=ext, delete=False) as tf:
        tf.write(content)
        tf.flush()
        try:
            payee_list, _ = load_payees(Path(tf.name))
        except ValueError as e:
            Path(tf.name).unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail={"error": "Invalid payee file", "detail": str(e)}) from e
    Path(tf.name).unlink(missing_ok=True)

    db = _get_db(org)

    # Build rows from Payee models
    import json as _json

    rows: list[dict[str, Any]] = []
    for p in payee_list:
        quotas_raw = _json.dumps({k: str(v) for k, v in p.quotas.items()}) if p.quotas else "{}"
        ramp_raw = _json.dumps(p.ramp.model_dump()) if p.ramp else None
        draw_raw = _json.dumps(p.draw.model_dump()) if p.draw else None
        cat_raw = _json.dumps({k: str(v) for k, v in p.category_quotas.items()}) if p.category_quotas else None
        rows.append({
            "id": p.id, "name": p.name, "quota": str(p.quota), "quotas": quotas_raw,
            "plan_id": p.plan_id,
            "effective_from": str(p.effective_from) if p.effective_from else "",
            "effective_to": str(p.effective_to) if p.effective_to else None,
            "ramp": ramp_raw, "draw": draw_raw, "email": p.email or None,
            "category_quotas": cat_raw,
            "manager_id": p.manager_id, "manager_override": str(p.manager_override) if p.manager_override else None,
            "team_id": p.team_id,
        })

    if replace:
        db.replace_all_payees(rows)
    else:
        db.save_payees_batch(rows)

    return {"imported": len(payee_list), "total_in_roster": len(db.list_payees()), "replace": replace}


# ------------------------------------------------------------------
# v1: Transactions
# ------------------------------------------------------------------


@v1.get("/transactions")
def list_transactions(
    period: str | None = None,
    limit: int = 1000,
    org: str = Depends(get_org),
) -> list[dict[str, Any]]:
    return _get_db(org).list_transactions(period=period, limit=limit)


@v1.get("/calculations/{calculation_id}/inputs")
def get_calculation_inputs(calculation_id: str, org: str = Depends(get_org)) -> list[dict[str, Any]]:
    return _get_db(org).get_transactions_for_calculation(calculation_id)


# ------------------------------------------------------------------
# v1: Settings
# ------------------------------------------------------------------


class SettingPutRequest(BaseModel):
    value: str

@v1.get("/settings/{key}")
def get_setting(key: str, org: str = Depends(get_org)) -> dict[str, str | None]:
    return {"key": key, "value": _get_db(org).get_setting(key)}


@v1.put("/settings/{key}")
def set_setting(key: str, req: SettingPutRequest, org: str = Depends(get_org)) -> dict[str, str]:
    _get_db(org).set_setting(key, req.value)
    return {"key": key, "status": "saved"}


@v1.delete("/settings/{key}")
def delete_setting(key: str, org: str = Depends(get_org)) -> dict[str, str]:
    _get_db(org).delete_setting(key)
    return {"key": key, "status": "deleted"}


# ------------------------------------------------------------------
# v1: Column mappings
# ------------------------------------------------------------------

class MappingSaveRequest(BaseModel):
    name: str
    mapping_data: dict[str, Any]


@v1.get("/mappings")
def list_mappings(org: str = Depends(get_org)) -> list[dict[str, Any]]:
    return _get_db(org).list_mappings()


@v1.post("/mappings")
def save_mapping(req: MappingSaveRequest, org: str = Depends(get_org)) -> dict[str, str]:
    return {"id": _get_db(org).save_mapping(req.name, req.mapping_data)}


@v1.delete("/mappings/{mapping_id}")
def delete_mapping(mapping_id: str, org: str = Depends(get_org)) -> dict[str, str]:
    if not _get_db(org).delete_mapping(mapping_id):
        raise HTTPException(status_code=404, detail="Mapping not found")
    return {"status": "deleted"}


# ------------------------------------------------------------------
# v1: Calculation history
# ------------------------------------------------------------------

@v1.get("/calculations")
def list_calculations(
    plan_id: str | None = None,
    period: str | None = None,
    limit: int = 50,
    org: str = Depends(get_org),
) -> list[dict[str, Any]]:
    return _get_db(org).list_calculations(plan_id=plan_id, period=period, limit=limit)


# ------------------------------------------------------------------
# v1: Ledger queries
# ------------------------------------------------------------------

@v1.get("/ledger")
def query_ledger(
    payee_id: str | None = None,
    calculation_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 200,
    org: str = Depends(get_org),
) -> list[dict[str, Any]]:
    return _get_db(org).query_ledger(
        payee_id=payee_id, calculation_id=calculation_id,
        from_date=from_date, to_date=to_date, limit=limit,
    )


# ------------------------------------------------------------------
# v1: File preview (column detection)
# ------------------------------------------------------------------

@v1.post("/preview")
async def preview_file(
    file: UploadFile = File(...),  # noqa: B008
    type: str = "transactions",
    org: str = Depends(get_org),
) -> dict[str, Any]:
    """Return detected headers and suggested column mappings for a file."""
    import csv
    import io

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail={"error": "File too large"})

    filename = file.filename or ""
    is_xlsx = filename.lower().endswith(".xlsx")

    headers: list[str] = []
    rows: list[list[str]] = []

    if is_xlsx:
        try:
            import tempfile as _tf

            from icm_engine.excel import read_xlsx_rows

            with _tf.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
                tf.write(content)
                tf.flush()
                headers, dict_rows = read_xlsx_rows(Path(tf.name))
            Path(tf.name).unlink(missing_ok=True)
            # read_xlsx_rows returns rows keyed by header; preview_rows is
            # positional, so flatten in header order.
            rows = [[str(r.get(h, "")) for h in headers] for r in dict_rows[:50]]
        except Exception:
            headers, rows = [], []
    else:
        text = content.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        try:
            headers = [h.strip() for h in next(reader)]
        except StopIteration:
            headers = []
        rows = list(reader)[:50]

    # Run fuzzy mapping on the detected headers
    mapping: dict[str, str] = {}
    if headers:
        try:
            from icm_engine.mapping import infer_mapping
            colmap = infer_mapping(headers, type if type in ("transactions", "payees") else "transactions")
            mapping = {src: tgt for src, tgt in colmap.mappings.items()}
        except Exception:
            pass

    return {
        "headers": headers,
        "preview_rows": rows,
        "mapping": mapping,
        "is_xlsx": is_xlsx,
    }


# ------------------------------------------------------------------
# v1: API key management
# ------------------------------------------------------------------

class ApiKeyResponse(BaseModel):
    id: str
    name: str
    created_at: str
    last_used_at: str | None


@v1.post("/api-keys")
def create_api_key(
    name: str = "", org: str = Depends(get_org),
) -> dict[str, str]:
    kid, plaintext = _get_db(org).create_api_key(name)
    return {"id": kid, "key": plaintext}


@v1.get("/api-keys")
def list_api_keys(org: str = Depends(get_org)) -> list[dict[str, Any]]:
    return _get_db(org).list_api_keys()


@v1.delete("/api-keys/{key_id}")
def delete_api_key(key_id: str, org: str = Depends(get_org)) -> dict[str, str]:
    if not _get_db(org).delete_api_key(key_id):
        raise HTTPException(status_code=404, detail="API key not found")
    return {"status": "deleted"}


# ------------------------------------------------------------------
# v1: Period locks
# ------------------------------------------------------------------

@v1.post("/periods/{plan_id}/{period}/lock")
def lock_period(
    plan_id: str, period: str,
    calculation_id: str | None = None,
    locked_by: str = "",
    reason: str = "",
    org: str = Depends(get_org),
) -> dict[str, Any]:
    """Lock a period to a calculation and generate the payout register.

    Defaults to the latest calculation for this plan/period. The payout
    register is automatically generated and saved alongside the database
    after a successful lock.
    """
    db = _get_db(org)
    if calculation_id is None:
        calcs = db.list_calculations(plan_id=plan_id, period=period, limit=1)
        if not calcs:
            raise HTTPException(status_code=404, detail="No calculations found for this plan and period")
        calculation_id = calcs[0]["id"]
    if not db.lock_period(plan_id, period, calculation_id, locked_by=locked_by, reason=reason):
        raise HTTPException(status_code=409, detail="Period already locked")

    # --- Generate payout register ---
    register_path_str: str | None = None
    try:
        from icm_engine.loader import load_plan
        from icm_engine.payout_register import (
            generate_payout_register,
            register_path,
            write_register,
        )

        # Load plan
        plan_row = db.get_plan(plan_id)
        if plan_row and plan_row.get("yaml_content"):
            import tempfile as _tf
            with _tf.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False, encoding="utf-8",
            ) as tf:
                tf.write(plan_row["yaml_content"])
                tf.flush()
                plan_obj = load_plan(Path(tf.name))
        else:
            plan_obj = None

        # Load commission lines and payees
        raw_lines = db.get_commission_lines(calculation_id)
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
                effective_from=(_date.today() if not pr.get("effective_from")
                                else _date.fromisoformat(str(pr["effective_from"])[:10])),
            )
            for pr in payee_rows
        ]

        if plan_obj is not None and commissions:
            calcs = db.list_calculations(plan_id=plan_id, period=period, limit=1)
            version = calcs[0].get("version", 1) if calcs else 1
            register = generate_payout_register(
                commissions, payees, plan_obj, period, version,
            )
            app_dir = Path(db.path).parent
            rp = register_path(app_dir, plan_id, period, version)
            rp.parent.mkdir(parents=True, exist_ok=True)
            write_register(register, commissions, rp)
            register_path_str = str(rp)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "Failed to generate payout register for %s/%s", plan_id, period, exc_info=True,
        )
        # Don't fail the lock — the register is supplementary

    return {
        "plan_id": plan_id, "period": period, "calculation_id": calculation_id,
        "status": "locked",
        "register_path": register_path_str,
    }


@v1.get("/periods/{plan_id}/{period}/register")
def download_register(
    plan_id: str, period: str,
    version: int | None = None,
    org: str = Depends(get_org),
) -> Response:
    """Download the payout register XLSX for a locked period.

    If version is not specified, returns the latest locked version.
    """
    from icm_engine.payout_register import register_path

    db = _get_db(org)
    path = db.path if hasattr(db, 'path') else default_db_path()
    app_dir = Path(str(path)).parent

    # Determine version
    if version is None:
        calcs = db.list_calculations(plan_id=plan_id, period=period, limit=1)
        if not calcs:
            raise HTTPException(status_code=404, detail="No calculations found for this period")
        version = calcs[0].get("version", 1)

    rp = register_path(app_dir, plan_id, period, version)
    if not rp.exists():
        raise HTTPException(status_code=404, detail={
            "error": "Payout register not found for this period/version",
            "hint": "Lock the period first to generate the register.",
        })

    return FileResponse(
        rp,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=rp.name,
    )


@v1.delete("/periods/{plan_id}/{period}/lock")
def unlock_period(plan_id: str, period: str, org: str = Depends(get_org)) -> dict[str, str]:
    if not _get_db(org).unlock_period(plan_id, period):
        raise HTTPException(status_code=404, detail="Period not locked")
    return {"plan_id": plan_id, "period": period, "status": "unlocked"}


@v1.get("/periods/{plan_id}/{period}/status")
def period_status(plan_id: str, period: str, org: str = Depends(get_org)) -> dict[str, Any]:
    db = _get_db(org)
    locked = db.is_locked(plan_id, period)
    official = db.get_official_calculation(plan_id, period) if locked else None
    return {
        "plan_id": plan_id, "period": period,
        "locked": locked,
        "official_calculation_id": official["id"] if official else None,
    }


@v1.get("/periods/{plan_id}")
def list_periods(plan_id: str, org: str = Depends(get_org)) -> list[dict[str, Any]]:
    return _get_db(org).get_period_status(plan_id)


# ------------------------------------------------------------------
# v1: Order trace
# ------------------------------------------------------------------

@v1.get("/trace")
def order_trace(
    transaction_id: str,
    payee_id: str,
    calculation_id: str | None = None,
    org: str = Depends(get_org),
) -> dict[str, Any]:
    """Return a read-only trace of how one order flowed through the plan."""
    from icm_engine.trace import build_order_trace

    entries = _get_db(org).query_ledger(
        payee_id=payee_id, calculation_id=calculation_id, limit=10000,
    )
    trace = build_order_trace(transaction_id, payee_id, entries)
    return cast(dict[str, Any], _serialize({
        "transaction_id": trace.transaction_id,
        "payee_id": trace.payee_id,
        "order": trace.order,
        "steps": [
            {
                "rule_id": s.rule_id,
                "status": s.status,
                "reason": s.reason,
                "events": s.events,
            }
            for s in trace.steps
        ],
        "total": trace.total,
        "summary": trace.summary,
    }))


# ------------------------------------------------------------------
# v1: Payee trace — full pipeline breakdown
# ------------------------------------------------------------------


@v1.get("/payee-trace")
def payee_trace(
    payee_id: str,
    period: str,
    calculation_id: str | None = None,
    org: str = Depends(get_org),
) -> dict[str, Any]:
    """Return the full pipeline breakdown for one payee in one period."""
    from icm_engine.engine import build_payee_trace

    db = _get_db(org)

    # Find the calculation
    if calculation_id:
        calc_id = calculation_id
    else:
        calcs = db.list_calculations(plan_id=None, limit=100)
        matching = [c for c in calcs if c.get("period") == period]
        calc_id = matching[0]["id"] if matching else ""

    if not calc_id:
        raise HTTPException(status_code=404, detail="No calculation found for this period")

    # Load commission lines for this payee from this calculation
    all_lines = db.get_commission_lines(calc_id)
    payee_lines = [li for li in all_lines if li.get("payee_id") == payee_id]
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
        for li in payee_lines
    ]

    # Load ledger entries
    all_ledger = db.query_ledger(calculation_id=calc_id, limit=5000)
    payee_ledger = [
        LedgerEntry(
            transaction_id=e.get("transaction_id", ""),
            payee_id=e.get("payee_id", ""),
            rule_id=e.get("rule_id", ""),
            event_type=e.get("event_type", ""),
            inputs=e.get("inputs", {}),
            outputs=e.get("outputs", {}),
            human_readable=e.get("human_readable", ""),
        )
        for e in all_ledger
        if e.get("payee_id") == payee_id
    ]

    # Attainment not available from DB retrospective — trace shows what it can
    attainment_entries: list[Any] = []

    return cast(dict[str, Any], _serialize(
        build_payee_trace(
            payee_id, period,
            commissions=commissions,
            ledger=payee_ledger,
            attainment=attainment_entries,
            plan_name="",
        )
    ))


def _write_internal_summary(
    commissions: list[Commission],
    payee_map: dict[str, Any],
    path: Path,
) -> None:
    """Write an internal-only summary workbook (all reps, totals)."""
    from collections import defaultdict

    from icm_engine.excel import write_xlsx

    totals: dict[str, Decimal] = defaultdict(Decimal)
    for c in commissions:
        totals[c.payee_id] += c.commission_amount

    rows: list[dict[str, str]] = []
    for pid in sorted(totals.keys()):
        name = payee_map.get(pid, {}).get("name", pid) if isinstance(payee_map.get(pid), dict) else (
            getattr(payee_map.get(pid), "name", pid) if payee_map.get(pid) else pid
        )
        rows.append({
            "payee_id": pid,
            "name": str(name),
            "total_commission": str(totals[pid]),
        })

    write_xlsx(path, {"Summary": rows})


@v1.post("/export")
async def export_statements(
    plan: UploadFile | None = File(None),  # noqa: B008
    transactions: UploadFile | None = File(None),  # noqa: B008
    payees: UploadFile | None = File(None),  # noqa: B008
    plan_text: str = Form(""),
    txn_text: str = Form(""),
    payee_text: str = Form(""),
    formats: str = Form("pdf"),
    period: str = Form(""),
    org: str = Depends(get_org),
    adjustments: UploadFile | None = File(None),  # noqa: B008
    mbos: UploadFile | None = File(None),  # noqa: B008
) -> Response:
    """Calculate commissions and return a ZIP of per-payee statements.

    Accepts plan/transactions/payees either as file uploads (UploadFile) OR
    as inline text (plan_text / txn_text / payee_text form fields). The text
    path supports in-app plan building where raw File objects are unavailable.
    """
    import io
    import zipfile

    from icm_engine.statements import generate_statements

    fmt_list = tuple(f.strip() for f in formats.split(",") if f.strip())
    if not fmt_list:
        fmt_list = ("pdf",)

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # --- Resolve plan ---
        plan_bytes = None
        plan_ext = ".yaml"
        if plan is not None:
            plan_bytes = await plan.read()
            if len(plan_bytes) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail={"error": "Plan file too large"})
            if plan.filename:
                plan_ext = Path(plan.filename).suffix or ".yaml"
        if plan_bytes:
            plan_path = root / f"plan{plan_ext}"
            plan_path.write_bytes(plan_bytes)
        elif plan_text.strip():
            plan_path = root / "plan.yaml"
            plan_path.write_text(plan_text.strip(), encoding="utf-8")
        else:
            raise HTTPException(status_code=400, detail={"error": "plan or plan_text required"})

        # --- Resolve transactions ---
        txn_bytes = None
        txn_ext = ".csv"
        if transactions is not None:
            txn_bytes = await transactions.read()
            if len(txn_bytes) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail={"error": "Transactions file too large"})
            if transactions.filename:
                txn_ext = Path(transactions.filename).suffix or ".csv"
        if txn_bytes:
            txn_path = root / f"transactions{txn_ext}"
            txn_path.write_bytes(txn_bytes)
        elif txn_text.strip():
            txn_path = root / "transactions.csv"
            txn_path.write_text(txn_text.strip(), encoding="utf-8")
        else:
            raise HTTPException(status_code=400, detail={"error": "transactions or txn_text required"})

        # --- Resolve payees ---
        pee_bytes = None
        pee_ext = ".csv"
        if payees is not None:
            pee_bytes = await payees.read()
            if len(pee_bytes) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail={"error": "Payees file too large"})
            if payees.filename:
                pee_ext = Path(payees.filename).suffix or ".csv"
        if pee_bytes:
            pee_path = root / f"payees{pee_ext}"
            pee_path.write_bytes(pee_bytes)
        elif payee_text.strip():
            pee_path = root / "payees.csv"
            pee_path.write_text(payee_text.strip(), encoding="utf-8")
        else:
            raise HTTPException(status_code=400, detail={"error": "payees or payee_text required"})

        # --- Load & calculate ---
        try:
            plan_obj = load_plan(plan_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"error": "Invalid plan", "detail": str(e)}) from e
        try:
            txn_list, _ = load_transactions(txn_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"error": "Invalid transactions", "detail": str(e)}) from e
        try:
            payee_list, _ = load_payees(pee_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"error": "Invalid payees", "detail": str(e)}) from e

        try:
            engine = CommissionEngine()
            adjustments_list = None
            if adjustments is not None:
                adj_bytes = await adjustments.read()
                if adj_bytes:
                    adj_path = root / "adjustments.csv"
                    adj_path.write_bytes(adj_bytes)
                    from icm_engine.loader import load_adjustments
                    adjustments_list = load_adjustments(adj_path)
            mbos_list = None
            if mbos is not None:
                mbo_bytes = await mbos.read()
                if mbo_bytes:
                    mbo_path = root / "mbos.csv"
                    mbo_path.write_bytes(mbo_bytes)
                    from icm_engine.loader import load_mbos
                    mbos_list = load_mbos(mbo_path)
            result = engine.calculate(
                plan_obj, txn_list, payee_list,
                adjustments=adjustments_list, mbos=mbos_list,
            )
        except Exception as e:
            import traceback as _tb2
            raise HTTPException(status_code=500, detail={
                "error": "Calculation failed",
                "detail": str(e),
                "traceback": _tb2.format_exc(),
            }) from e

        # Persist to database
        db = _get_db(org)
        commissions = [c.model_dump() for c in result.commissions]
        ledger_dicts = [e.to_dict() for e in result.ledger]
        by_period: dict[str, list[dict[str, Any]]] = {}
        for c_dict in commissions:
            p = c_dict["period"]
            by_period.setdefault(p, []).append(c_dict)
        calc_ids: dict[str, str] = {}
        for period_key in sorted(by_period.keys()):
            calc_id = db.record_calculation(
                plan_obj.plan_id, period=period_key,
                input_summary={"txn_count": len(txn_list), "payee_count": len(payee_list)},
            )
            db.save_commission_lines(calc_id, by_period[period_key])
            db.save_ledger_entries(calc_id, ledger_dicts)
            calc_ids[period_key] = calc_id
        txn_dicts = [t.model_dump() for t in txn_list]
        db.save_transactions(txn_dicts)
        for cid in calc_ids.values():
            db.link_transactions(cid, [t.id for t in txn_list])

        period_filter = period.strip() or None

        # --- Generate per-payee statements ---
        stmt_dir = root / "statements"
        _r = plan_obj.rounding
        _stmt_round: dict[str, Any] = {}
        if _r is not None:
            _stmt_round = {
                "rounding_mode": parse_rounding_mode(_r.mode),
                "rounding_places": _r.places,
            }
        stmt_files = generate_statements(
            result.commissions,
            payee_list,
            out_dir=stmt_dir,
            period=period_filter,
            formats=fmt_list,
            attainment=result.attainment,
            plan_name=plan_obj.name,
            **_stmt_round,
        )

        # --- Internal summary ---
        payee_map: dict[str, Any] = {}
        for p in payee_list:
            payee_map[p.id] = p
        _write_internal_summary(
            result.commissions, payee_map,
            stmt_dir / "_internal_all-reps-summary.xlsx",
        )

        # --- Build ZIP ---
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Per-rep files
            for sf in stmt_files:
                zf.write(sf.path, sf.path.name)
            # Internal summary
            summary_path = stmt_dir / "_internal_all-reps-summary.xlsx"
            if summary_path.exists():
                zf.write(summary_path, "internal/_all-reps-summary.xlsx")

        zip_buf.seek(0)
        content_bytes = zip_buf.read()

        # Desktop mode: save to Downloads folder and return the path
        if os.environ.get("ICM_DESKTOP") == "1":
            downloads = Path(os.path.expanduser("~")) / "Downloads"
            downloads.mkdir(parents=True, exist_ok=True)
            out_zip = downloads / "commission_statements.zip"
            out_zip.write_bytes(content_bytes)
            return JSONResponse({
                "saved_to": str(out_zip),
                "file_count": len(stmt_files),
                "payee_count": len(set(sf.payee_id for sf in stmt_files)),
            })

        return Response(
            content=content_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": "attachment; filename=commission_statements.zip",
            },
        )


# ------------------------------------------------------------------
# Mount v1 router
# ------------------------------------------------------------------

app.include_router(v1)


# ------------------------------------------------------------------
# Desktop-only: open folder in OS file manager
# ------------------------------------------------------------------


class OpenFolderRequest(BaseModel):
    path: str


@app.post("/v1/open-folder")
def open_folder(req: OpenFolderRequest) -> dict[str, str]:
    """Open a folder in the OS file manager. Desktop mode only."""
    if os.environ.get("ICM_DESKTOP") != "1":
        raise HTTPException(status_code=404, detail="Not available")
    target = Path(req.path)
    if not target.exists():
        raise HTTPException(status_code=404, detail={"error": f"Path not found: {target}"})
    import subprocess
    import sys

    if target.is_file():
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(target)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target.parent)])
    else:
        if sys.platform == "win32":
            os.startfile(str(target))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    return {"opened": str(target)}

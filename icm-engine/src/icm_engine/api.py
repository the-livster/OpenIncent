from __future__ import annotations

import os
import tempfile
from datetime import date as _date
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRouter
from pydantic import BaseModel

from icm_engine.database import Database, default_db_path
from icm_engine.engine import CommissionEngine
from icm_engine.loader import load_payees, load_plan, load_transactions
from icm_engine.models import Commission

app = FastAPI(title="icm-engine")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    import traceback as _tb
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "traceback": _tb.format_exc()},
    )

v1 = APIRouter(prefix="/v1")

_db: Database | None = None
_db_path: str | None = None


def _get_db(org_id: str = "default") -> Database:
    global _db, _db_path
    path = os.environ.get("ICM_DB_PATH", str(default_db_path()))
    if _db is None or _db_path != path:
        _db = Database(path, org_id=org_id)
        _db.init()
        _db_path = path
    elif _db.org_id != org_id:
        _db = Database(path, org_id=org_id)
        _db.init()
    return _db


def get_org(authorization: str | None = Header(None)) -> str:
    """Extract org_id from Bearer token, or return 'default' for unauthenticated requests."""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        db = _get_db()
        org = db.validate_api_key(token)
        if org:
            return org
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
    plan: UploadFile = File(...),  # noqa: B008
    transactions: UploadFile = File(...),  # noqa: B008
    payees: UploadFile = File(...),  # noqa: B008
    adjustments: UploadFile | None = File(None),  # noqa: B008
    effective_period: str | None = None,
    allow_recalculate_locked: bool = True,
    org: str = Depends(get_org),
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        files: dict[str, Path] = {}
        for name, upload in [("plan", plan), ("transactions", transactions), ("payees", payees)]:
            content = await upload.read()
            if len(content) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail={
                    "error": f"{name} file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit",
                })
            ext = Path(upload.filename or "").suffix or ".csv"
            filepath = root / f"{name}{ext}"
            filepath.write_bytes(content)
            files[name] = filepath

        try:
            plan_obj = load_plan(files["plan"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"error": "Invalid plan file", "detail": str(e)}) from e
        try:
            txn_list, _ = load_transactions(files["transactions"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"error": "Invalid transactions file", "detail": str(e)}) from e
        try:
            payee_list, _ = load_payees(files["payees"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"error": "Invalid payees file", "detail": str(e)}) from e

        # Persist to database
        db = _get_db(org)

        # Determine locked periods for this plan
        period_status = db.get_period_status(plan_obj.plan_id)
        locked_periods = {r["period"] for r in period_status if r.get("locked_calc_id") is not None}

        txn_periods = {t.period for t in txn_list}
        locked_relevant = txn_periods & locked_periods

        if locked_relevant:
            if not allow_recalculate_locked:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "Some periods are locked",
                        "locked_periods": sorted(locked_relevant),
                        "hint": "Set allow_recalculate_locked=true to create draft versions",
                    },
                )
            # Auto-default effective_period to current month
            if effective_period is None:
                today = _date.today()
                effective_period = today.strftime("%Y-%m")

        # Load prior official commission lines for locked periods
        prior_commissions: list[Commission] = []
        if locked_relevant:
            for period in locked_relevant:
                official = db.get_official_calculation(plan_obj.plan_id, period)
                if official:
                    lines = db.get_commission_lines(official["id"])
                    for line in lines:
                        prior_commissions.append(Commission(**{
                            "transaction_id": line["transaction_id"],
                            "payee_id": line["payee_id"],
                            "period": line["period"],
                            "origin_period": line.get("origin_period", ""),
                            "rule_id": line["rule_id"],
                            "base_amount": line["base_amount"],
                            "rate": line["rate"],
                            "commission_amount": line["commission_amount"],
                            "notes": line.get("notes", ""),
                        }))

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
            result = engine.calculate(
                plan_obj, txn_list, payee_list,
                locked_periods=locked_relevant if locked_relevant else None,
                effective_period=effective_period if locked_relevant else None,
                prior_commissions=prior_commissions if prior_commissions else None,
                adjustments=adjustments_list,
            )
        except Exception as e:
            import traceback as _tb
            raise HTTPException(status_code=500, detail={
                "error": "Calculation failed",
                "detail": str(e),
                "traceback": _tb.format_exc(),
            }) from e

        commissions = [c.model_dump() for c in result.commissions]
        ledger_dicts = [e.to_dict() for e in result.ledger]

        # Split commissions by effective period, record one calculation per period
        by_period: dict[str, list[dict[str, Any]]] = {}
        for c_dict in commissions:
            p = c_dict["period"]
            by_period.setdefault(p, []).append(c_dict)

        calc_ids: dict[str, str] = {}
        for period_key, comms in sorted(by_period.items()):
            calc_id = db.record_calculation(
                plan_obj.plan_id,
                period=period_key,
                input_summary={"txn_count": len(txn_list), "payee_count": len(payee_list)},
            )
            db.save_commission_lines(calc_id, comms)
            # Save ledger under every period calculation for full audit coverage
            db.save_ledger_entries(calc_id, ledger_dicts)
            calc_ids[period_key] = calc_id

        summary: dict[str, Decimal] = {}
        for c in result.commissions:
            summary[c.payee_id] = summary.get(c.payee_id, Decimal("0")) + c.commission_amount

        return cast(dict[str, Any], _serialize({
            "calculation_ids": calc_ids,
            "commissions": commissions,
            "ledger": ledger_dicts,
            "summary": {k: str(v) for k, v in summary.items()},
            "effective_period": effective_period,
            "locked_periods": sorted(locked_relevant) if locked_relevant else [],
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
    api_key: str
    base_url: str | None = None


@v1.post("/plan-from-text")
async def plan_from_text(req: PlanFromTextRequest) -> dict[str, Any]:
    from icm_engine.ai.plan_author import generate_plan_from_text
    from icm_engine.exceptions import MissingAPIKeyError, PlanGenerationError

    old_key = os.environ.get("ICM_LLM_API_KEY")
    os.environ["ICM_LLM_API_KEY"] = req.api_key
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
) -> dict[str, Any]:
    """Return detected headers and suggested column mappings for a file."""
    import csv
    import io

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail={"error": "File too large"})

    filename = file.filename or ""
    is_xlsx = filename.lower().endswith(".xlsx")

    if is_xlsx:
        try:
            import tempfile as _tf

            from icm_engine.excel import read_xlsx_rows

            with _tf.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
                tf.write(content)
                tf.flush()
                headers, rows = read_xlsx_rows(Path(tf.name))
            Path(tf.name).unlink(missing_ok=True)
        except Exception:
            headers, rows = [], []
    else:
        text = content.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        try:
            headers = [h.strip() for h in next(reader)]
        except StopIteration:
            headers = []
        rows = [dict(zip(headers, row, strict=False)) for row in list(reader)[:5]]

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
        "preview_rows": [[str(c) for c in row] for row in rows],
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
    """Lock a period to a calculation. Defaults to the latest calculation for this plan/period."""
    db = _get_db(org)
    if calculation_id is None:
        calcs = db.list_calculations(plan_id=plan_id, period=period, limit=1)
        if not calcs:
            raise HTTPException(status_code=404, detail="No calculations found for this plan and period")
        calculation_id = calcs[0]["id"]
    if not db.lock_period(plan_id, period, calculation_id, locked_by=locked_by, reason=reason):
        raise HTTPException(status_code=409, detail="Period already locked")
    return {"plan_id": plan_id, "period": period, "calculation_id": calculation_id, "status": "locked"}


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
# v1: Per-payee statement export (zip of per-rep files)
# ------------------------------------------------------------------


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
            result = engine.calculate(plan_obj, txn_list, payee_list, adjustments=adjustments_list)
        except Exception as e:
            import traceback as _tb2
            raise HTTPException(status_code=500, detail={
                "error": "Calculation failed",
                "detail": str(e),
                "traceback": _tb2.format_exc(),
            }) from e

        period_filter = period.strip() or None

        # --- Generate per-payee statements ---
        stmt_dir = root / "statements"
        stmt_files = generate_statements(
            result.commissions,
            payee_list,
            out_dir=stmt_dir,
            period=period_filter,
            formats=fmt_list,
            attainment=result.attainment,
            plan_name=plan_obj.name,
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

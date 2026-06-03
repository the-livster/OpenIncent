from __future__ import annotations

import os
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter
from pydantic import BaseModel

from icm_engine.database import Database, default_db_path
from icm_engine.engine import CommissionEngine
from icm_engine.loader import load_payees, load_plan, load_transactions

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
async def global_exception_handler(request, exc: Exception) -> JSONResponse:
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

        try:
            engine = CommissionEngine()
            result = engine.calculate(plan_obj, txn_list, payee_list)
        except Exception as e:
            import traceback as _tb
            raise HTTPException(status_code=500, detail={
                "error": "Calculation failed",
                "detail": str(e),
                "traceback": _tb.format_exc(),
            }) from e

        commissions = [c.model_dump() for c in result.commissions]
        ledger_dicts = [e.to_dict() for e in result.ledger]

        # Persist to database
        db = _get_db(org)
        calc_id = db.record_calculation(
            plan_obj.plan_id,
            input_summary={"txn_count": len(txn_list), "payee_count": len(payee_list)},
        )
        db.save_ledger_entries(calc_id, ledger_dicts)

        summary: dict[str, Decimal] = {}
        for c in result.commissions:
            summary[c.payee_id] = summary.get(c.payee_id, Decimal("0")) + c.commission_amount

        return cast(dict[str, Any], _serialize({
            "calculation_id": calc_id,
            "commissions": commissions,
            "ledger": ledger_dicts,
            "summary": {k: str(v) for k, v in summary.items()},
        }))


# ------------------------------------------------------------------
# v1: Plan from text (AI)
# ------------------------------------------------------------------

class PlanFromTextRequest(BaseModel):
    description: str
    plan_id: str | None = None
    api_key: str


@v1.post("/plan-from-text")
async def plan_from_text(req: PlanFromTextRequest) -> dict[str, Any]:
    from icm_engine.ai.plan_author import generate_plan_from_text
    from icm_engine.exceptions import MissingAPIKeyError, PlanGenerationError

    old_key = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = req.api_key
    try:
        plan = generate_plan_from_text(req.description, plan_id=req.plan_id or None)
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
            os.environ["ANTHROPIC_API_KEY"] = old_key
        elif "ANTHROPIC_API_KEY" in os.environ:
            del os.environ["ANTHROPIC_API_KEY"]

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
    plan_id: str | None = None, limit: int = 50, org: str = Depends(get_org),
) -> list[dict[str, Any]]:
    return _get_db(org).list_calculations(plan_id=plan_id, limit=limit)


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
        rows = [row for row in list(reader)[:5]]

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
    return _serialize({
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
    })


# ------------------------------------------------------------------
# v1: Per-payee XLSX export
# ------------------------------------------------------------------

@v1.post("/export")
async def export_xlsx(
    plan: UploadFile = File(...),  # noqa: B008
    transactions: UploadFile = File(...),  # noqa: B008
    payees: UploadFile = File(...),  # noqa: B008
    org: str = Depends(get_org),
):
    """Calculate commissions and return a per-payee XLSX statement file."""
    from fastapi.responses import Response

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
            raise HTTPException(status_code=400, detail={"error": "Invalid plan", "detail": str(e)}) from e
        try:
            txn_list, _ = load_transactions(files["transactions"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"error": "Invalid transactions", "detail": str(e)}) from e
        try:
            payee_list, _ = load_payees(files["payees"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"error": "Invalid payees", "detail": str(e)}) from e

        try:
            engine = CommissionEngine()
            result = engine.calculate(plan_obj, txn_list, payee_list)
        except Exception as e:
            import traceback as _tb2
            raise HTTPException(status_code=500, detail={
                "error": "Calculation failed",
                "detail": str(e),
                "traceback": _tb2.format_exc(),
            }) from e

        # Build per-payee sheets
        from collections import defaultdict

        from icm_engine.excel import write_xlsx

        by_payee: dict[str, list[dict[str, str]]] = defaultdict(list)
        totals: dict[str, Decimal] = defaultdict(Decimal)
        for c in result.commissions:
            by_payee[c.payee_id].append({
                "transaction_id": c.transaction_id,
                "period": c.period,
                "rule_id": c.rule_id,
                "base_amount": str(c.base_amount),
                "rate": str(c.rate),
                "commission": str(c.commission_amount),
                "notes": c.notes,
            })
            totals[c.payee_id] += c.commission_amount

        # Summary sheet
        summary_rows = [
            {"payee": pid, "total_commission": str(total)}
            for pid, total in sorted(totals.items())
        ]

        sheets: dict[str, list[dict[str, str]]] = {"Summary": summary_rows}
        for pid, rows in sorted(by_payee.items()):
            sheets[pid] = rows

        out_path = root / "statements.xlsx"
        write_xlsx(out_path, sheets)

        content_bytes = out_path.read_bytes()
        return Response(
            content=content_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=commission_statements.xlsx"},
        )


# ------------------------------------------------------------------
# Mount v1 router
# ------------------------------------------------------------------

app.include_router(v1)

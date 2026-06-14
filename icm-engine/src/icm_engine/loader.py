from __future__ import annotations

import csv
import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from icm_engine.mapping import ColumnMapping

import yaml

from icm_engine.models import Draw, Payee, Plan, RampSchedule, Transaction, parse_credits_spec

logger = logging.getLogger(__name__)


def load_plan(path: str | Path) -> Plan:
    """Load and validate a plan definition from a YAML file."""
    try:
        raw = _read_yaml(Path(path))
        return Plan.model_validate(raw)
    except Exception as e:
        raise ValueError(f"Invalid plan file '{path}': {e}") from e


def load_transactions(
    path: str | Path, mapping: ColumnMapping | None = None
) -> tuple[list[Transaction], ColumnMapping | None]:
    """Load transactions from a CSV or XLSX file.

    Returns (transactions, mapping_used). mapping_used is the ColumnMapping
    that was applied, or None for CSV files.
    """
    p = Path(path)
    if p.suffix.lower() == ".xlsx":
        return _load_transactions_xlsx(p, mapping)
    if p.suffix.lower() == ".parquet":
        return _load_transactions_parquet(p), None
    return _load_transactions_csv(p), None


def load_payees(
    path: str | Path, mapping: ColumnMapping | None = None
) -> tuple[list[Payee], ColumnMapping | None]:
    """Load payees from a CSV or XLSX file.

    Returns (payees, mapping_used). mapping_used is the ColumnMapping
    that was applied, or None for CSV files.
    """
    p = Path(path)
    if p.suffix.lower() == ".xlsx":
        return _load_payees_xlsx(p, mapping)
    if p.suffix.lower() == ".parquet":
        return _load_payees_parquet(p), None
    return _load_payees_csv(p), None


def _load_transactions_csv(path: Path) -> list[Transaction]:
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"CSV file '{path}' has no header row")
        for row in reader:
            rows.append(row)
    if not rows:
        return []

    # Normalize CSV headers: lowercase, spaces→underscores, strip parens
    remap = {k: _norm(k) for k in rows[0]}
    rows = [{remap.get(k, k): v for k, v in r.items()} for r in rows]

    # Build alias reverse-lookup so "hours" → units, "gp" → margin, etc.
    from icm_engine.mapping import _FIELD_ALIASES
    _canonical_aliases: dict[str, list[str]] = {
        canonical: [_norm(a) for a in aliases]
        for canonical, aliases in _FIELD_ALIASES.items()
    }

    def _get_field(row: dict[str, str], canonical: str) -> str:
        """Look up a field by canonical name, also checking normalized aliases."""
        val = row.get(canonical)
        if val is not None:
            return val.strip()
        for alias in _canonical_aliases.get(canonical, []):
            val = row.get(alias)
            if val is not None:
                return val.strip()
        return ""

    transactions: list[Transaction] = []
    known = {"id", "payee_id", "deal_id", "period", "amount", "product", "close_date",
             "bill_rate", "pay_rate", "units", "margin", "credits", "quota_amount"}
    for i, row in enumerate(rows, start=2):
        meta = {k: v for k, v in row.items() if k and k not in known}
        try:
            product = (_get_field(row, "product") or None) or None
            deal_id = _get_field(row, "deal_id")
            period = _get_field(row, "period")
            close_date_str = _get_field(row, "close_date")
            txn_id = _get_field(row, "id")
            if not txn_id:
                txn_id = f"T{i - 1:03d}"
            bill_raw = _get_field(row, "bill_rate")
            pay_raw = _get_field(row, "pay_rate")
            units_raw = _get_field(row, "units")
            margin_raw = _get_field(row, "margin")
            credits_raw = _get_field(row, "credits")
            quota_raw = _get_field(row, "quota_amount")
            t = Transaction(
                id=txn_id,
                payee_id=_get_field(row, "payee_id"),
                deal_id=deal_id,
                period=period,
                amount=Decimal(_get_field(row, "amount") or "0"),
                product=product,
                close_date=_parse_date(close_date_str) if close_date_str else None,
                metadata=meta,
                bill_rate=Decimal(bill_raw) if bill_raw else None,
                pay_rate=Decimal(pay_raw) if pay_raw else None,
                units=Decimal(units_raw) if units_raw else Decimal("1"),
                margin=Decimal(margin_raw) if margin_raw else None,
                credits=parse_credits_spec(credits_raw) if credits_raw else None,
                quota_amount=Decimal(quota_raw) if quota_raw else None,
            )
            transactions.append(t)
        except Exception as e:
            raise ValueError(f"Row {i} in '{path}': {e}") from e

    return transactions


def _load_transactions_xlsx(
    path: Path, mapping: Any,
) -> tuple[list[Transaction], Any]:
    from icm_engine.excel import read_xlsx_rows
    from icm_engine.mapping import apply_mapping, infer_mapping

    headers, rows = read_xlsx_rows(path)
    if mapping is None:
        mapping = infer_mapping(headers, "transactions")
        logger.info(
            "Inferred mapping: %s",
            {k: v for k, v in mapping.mappings.items()},
        )
    mapped = apply_mapping(rows, mapping, Transaction)
    return list(mapped), mapping


def _load_payees_csv(path: Path) -> list[Payee]:
    payees: list[Payee] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"CSV file '{path}' has no header row")

        has_period = "period" in reader.fieldnames

        # If period column exists, merge rows for same payee id into quotas map
        if has_period:
            by_id: dict[str, dict[str, Any]] = {}
            for i, row in enumerate(reader, start=2):
                try:
                    pid = row["id"].strip()
                    period = row.get("period", "").strip()
                    quota_val = Decimal(row["quota"].strip())
                    if pid not in by_id:
                        effective_to_raw = (row.get("effective_to") or "").strip()
                        by_id[pid] = {
                            "id": pid,
                            "name": row["name"].strip(),
                            "quota": Decimal("0"),
                            "quotas": {},
                            "plan_id": row["plan_id"].strip(),
                            "effective_from": _parse_date(ef_from) if (ef_from := row["effective_from"].strip()) else None,
                            "effective_to": _parse_date(effective_to_raw) if effective_to_raw else None,
                            "email": (row.get("email") or "").strip() or None,
                            "ramp": _parse_ramp(row),
                            "draw": _parse_draw(row),
                            "category_quotas": _parse_category_quotas(row),
                            "manager_id": (row.get("manager_id") or "").strip(),
                            "manager_override": _parse_optional_decimal(row.get("manager_override")),
                            "team_id": (row.get("team_id") or "").strip(),
                        }
                    if period:
                        by_id[pid]["quotas"][period] = quota_val
                    else:
                        by_id[pid]["quota"] = quota_val
                except Exception as e:
                    raise ValueError(f"Row {i} in '{path}': {e}") from e
            payees = [Payee(**data) for data in by_id.values()]
        else:
            for i, row in enumerate(reader, start=2):
                try:
                    effective_to_raw = (row.get("effective_to") or "").strip()
                    ef_from_raw = row["effective_from"].strip()
                    payees.append(
                        Payee(
                            id=row["id"].strip(),
                            name=row["name"].strip(),
                            quota=Decimal(row["quota"].strip()),
                            plan_id=row["plan_id"].strip(),
                            effective_from=_parse_date(ef_from_raw) if ef_from_raw else None,
                            effective_to=_parse_date(effective_to_raw) if effective_to_raw else None,
                            email=(row.get("email") or "").strip() or None,
                            ramp=_parse_ramp(row),
                            draw=_parse_draw(row),
                            category_quotas=_parse_category_quotas(row),
                            manager_id=(row.get("manager_id") or "").strip(),
                            manager_override=_parse_optional_decimal(row.get("manager_override")),
                            team_id=(row.get("team_id") or "").strip(),
                        )
                    )
                except Exception as e:
                    raise ValueError(f"Row {i} in '{path}': {e}") from e

    return payees


def _load_payees_xlsx(
    path: Path, mapping: Any,
) -> tuple[list[Payee], Any]:
    from icm_engine.excel import read_xlsx_rows
    from icm_engine.mapping import apply_mapping, infer_mapping

    headers, rows = read_xlsx_rows(path)
    if mapping is None:
        mapping = infer_mapping(headers, "payees")
        logger.info(
            "Inferred mapping: %s",
            {k: v for k, v in mapping.mappings.items()},
        )
    mapped = apply_mapping(rows, mapping, Payee)
    return list(mapped), mapping


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except UnicodeDecodeError as e:
        raise ValueError(f"YAML file '{path}' is not valid UTF-8: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError(f"YAML file '{path}' must contain a mapping at the top level")
    return raw


def _load_transactions_parquet(path: Path) -> list[Transaction]:
    """Load transactions from a Parquet file. Column names must match canonical
    field names exactly (no fuzzy mapping)."""
    import pyarrow.parquet as pq

    table = pq.read_table(path)  # type: ignore[no-untyped-call]
    transactions: list[Transaction] = []
    for i in range(table.num_rows):
        row = {col: table.column(col)[i].as_py() for col in table.column_names}
        try:
            product_raw = row.get("product")
            product = str(product_raw).strip() if product_raw is not None else None
            close_date_raw = row.get("close_date")
            close_date_str = str(close_date_raw) if close_date_raw is not None else ""
            known_txn = {"id", "payee_id", "deal_id", "period", "amount", "product", "close_date"}
            meta = {k: v for k, v in row.items() if k and k not in known_txn}
            t = Transaction(
                id=str(row["id"]),
                payee_id=str(row["payee_id"]),
                deal_id=str(row["deal_id"]),
                period=str(row["period"]),
                amount=Decimal(str(row["amount"])),
                product=product or None,
                close_date=_parse_date(close_date_str) if close_date_str else date.today(),
                metadata=meta,
            )
            transactions.append(t)
        except Exception as e:
            raise ValueError(f"Row {i} in '{path}': {e}") from e
    return transactions


def _load_payees_parquet(path: Path) -> list[Payee]:
    """Load payees from a Parquet file. Column names must match canonical
    field names exactly (no fuzzy mapping)."""
    import pyarrow.parquet as pq

    table = pq.read_table(path)  # type: ignore[no-untyped-call]
    payees: list[Payee] = []
    for i in range(table.num_rows):
        row = {col: table.column(col)[i].as_py() for col in table.column_names}
        try:
            effective_from_raw = row.get("effective_from")
            effective_from_str = str(effective_from_raw) if effective_from_raw is not None else ""
            effective_to_raw = row.get("effective_to")
            effective_to_str = str(effective_to_raw) if effective_to_raw is not None else ""
            ramp = _parse_ramp({k: str(v) if v is not None else "" for k, v in row.items()})
            payees.append(
                Payee(
                    id=str(row["id"]),
                    name=str(row["name"]),
                    quota=Decimal(str(row["quota"])),
                    plan_id=str(row["plan_id"]),
                    effective_from=(
                        _parse_date(effective_from_str) if effective_from_str else date.today()
                    ),
                    effective_to=_parse_date(effective_to_str) if effective_to_str else None,
                    ramp=ramp,
                )
            )
        except Exception as e:
            raise ValueError(f"Row {i} in '{path}': {e}") from e
    return payees


def _parse_optional_decimal(raw: object) -> Decimal | None:
    """Parse an optional decimal value. Returns None for empty/missing."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except Exception:
        return None


def _parse_date(s: str) -> date:
    if not s or not s.strip():
        raise ValueError(f"Cannot parse empty date")
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: '{s}'")


def _parse_ramp(row: dict[str, str]) -> RampSchedule | None:
    """Parse ramp_months and ramp_schedule columns into a RampSchedule.

    ramp_months: integer duration.
    ramp_schedule: space-separated decimal multipliers (e.g. "0.5 0.75 1.0").
    Returns None if either column is missing or empty.
    """
    months_raw = (row.get("ramp_months") or "").strip()
    schedule_raw = (row.get("ramp_schedule") or "").strip()
    if not months_raw or not schedule_raw:
        return None
    months = int(months_raw)
    schedule = [Decimal(v) for v in schedule_raw.split()]
    return RampSchedule(months=months, schedule=schedule)


def _parse_draw(row: dict[str, str]) -> Draw | None:
    """Parse draw_amount and draw_recoverable columns into a Draw."""
    amount_raw = (row.get("draw_amount") or "").strip()
    if not amount_raw:
        return None
    recoverable_raw = (row.get("draw_recoverable") or "").strip().lower()
    recoverable = recoverable_raw in ("true", "1", "yes", "y", "t", "on")
    return Draw(amount=Decimal(amount_raw), recoverable=recoverable)


def _parse_category_quotas(row: dict[str, str]) -> dict[str, Decimal]:
    """Parse category_quotas JSON column."""
    raw = (row.get("category_quotas") or "").strip()
    if not raw or raw == "{}":
        return {}
    import json as _json
    return {k: Decimal(str(v)) for k, v in _json.loads(raw).items()}


# ------------------------------------------------------------------
# Manual adjustments
# ------------------------------------------------------------------


def load_adjustments(path: str | Path) -> list[Any]:
    """Load manual adjustments from a CSV file.

    CSV columns: payee_id, period, amount, reason (required), id (optional).
    """
    from icm_engine.models import ManualAdjustment

    p = Path(path)
    with p.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"Adjustments CSV '{path}' has no header row")
        rows = list(reader)

    adjustments: list[Any] = []
    for i, row in enumerate(rows, start=2):
        try:
            adj = ManualAdjustment(
                id=(row.get("id") or "").strip(),
                payee_id=(row["payee_id"]).strip(),
                period=(row["period"]).strip(),
                amount=Decimal((row["amount"]).strip()),
                reason=(row["reason"]).strip(),
            )
            adjustments.append(adj)
        except Exception as e:
            raise ValueError(f"Row {i} in '{path}': {e}") from e
    return adjustments


# ------------------------------------------------------------------
# MBOs / bonuses
# ------------------------------------------------------------------


def load_mbos(path: str | Path) -> list[Any]:
    """Load MBOs from a CSV file.

    CSV columns: payee_id, period, amount, label (optional), id (optional).
    """
    from icm_engine.models import MBO

    p = Path(path)
    with p.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"MBOs CSV '{path}' has no header row")
        rows = list(reader)

    mbos: list[Any] = []
    for i, row in enumerate(rows, start=2):
        try:
            mbo = MBO(
                id=(row.get("id") or "").strip(),
                payee_id=(row["payee_id"]).strip(),
                period=(row["period"]).strip(),
                amount=Decimal((row["amount"]).strip()),
                label=(row.get("label") or "").strip(),
            )
            mbos.append(mbo)
        except Exception as e:
            raise ValueError(f"Row {i} in '{path}': {e}") from e
    return mbos


def _norm(h: str) -> str:
    """Normalize a CSV header: lowercase, spaces→underscores, strip parens."""
    return h.strip().lower().replace(" ", "_").replace("(", "").replace(")", "")

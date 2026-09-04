from __future__ import annotations

import csv
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
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

    rows = _normalise_rows(rows)
    aliases = _alias_index()

    def _get(row: dict[str, str], canonical: str) -> str:
        return _get_field(row, canonical, aliases)

    transactions: list[Transaction] = []
    known = {"id", "payee_id", "deal_id", "period", "amount", "product", "close_date",
             "bill_rate", "pay_rate", "units", "margin", "credits", "quota_amount"}
    for i, row in enumerate(rows, start=2):
        if _is_blank(row):
            continue
        meta = {k: v for k, v in row.items() if k and k not in known}
        try:
            product = (_get(row, "product") or None) or None
            deal_id = _get(row, "deal_id")
            period = _get(row, "period")
            close_date_str = _get(row, "close_date")
            txn_id = _get(row, "id")
            if not txn_id:
                txn_id = f"T{i - 1:03d}"
            bill_raw = _get(row, "bill_rate")
            pay_raw = _get(row, "pay_rate")
            units_raw = _get(row, "units")
            margin_raw = _get(row, "margin")
            credits_raw = _get(row, "credits")
            quota_raw = _get(row, "quota_amount")
            t = Transaction(
                id=txn_id,
                payee_id=_get(row, "payee_id"),
                deal_id=deal_id,
                period=period,
                amount=_number(_get(row, "amount") or "0", "amount"),
                product=product,
                close_date=_parse_date(close_date_str) if close_date_str else None,
                metadata=meta,
                bill_rate=_number(bill_raw, "bill_rate") if bill_raw else None,
                pay_rate=_number(pay_raw, "pay_rate") if pay_raw else None,
                units=_number(units_raw, "units") if units_raw else Decimal("1"),
                margin=_number(margin_raw, "margin") if margin_raw else None,
                credits=parse_credits_spec(credits_raw) if credits_raw else None,
                quota_amount=_number(quota_raw, "quota_amount") if quota_raw else None,
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


_REQUIRED_PAYEE_COLUMNS = ("id", "name", "quota", "plan_id")


def _is_blank(row: dict[str, str]) -> bool:
    """True for a row whose every cell is empty — trailing junk in real exports."""
    return not any((v or "").strip() for v in row.values())


def _payee_fields(row: dict[str, str], aliases: dict[str, list[str]]) -> dict[str, Any]:
    """Build Payee kwargs for one normalised row. Quota is handled by the caller,
    which has to decide between the flat quota and the per-period quotas map."""
    def g(field: str) -> str:
        return _get_field(row, field, aliases)

    ef_from, ef_to = g("effective_from"), g("effective_to")
    return {
        "id": g("id"),
        "name": g("name"),
        "plan_id": g("plan_id"),
        "effective_from": _parse_date(ef_from) if ef_from else None,
        "effective_to": _parse_date(ef_to) if ef_to else None,
        "email": g("email") or None,
        "ramp": _parse_ramp(row),
        "draw": _parse_draw(row),
        "category_quotas": _parse_category_quotas(row),
        "manager_id": g("manager_id"),
        "manager_override": _parse_optional_decimal(
            g("manager_override") or None, "manager_override"
        ),
        "team_id": g("team_id"),
    }


def _load_payees_csv(path: Path) -> list[Payee]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"CSV file '{path}' has no header row")
        raw_rows = list(reader)

    # Headers go through the same normalisation and alias table the transactions
    # loader uses. Without it this loader matched only exact canonical spellings,
    # so a "Manager Override %" column silently produced no override at all.
    rows = _normalise_rows(raw_rows)
    if not rows:
        return []

    aliases = _alias_index()
    present = set(rows[0])

    def _has(field: str) -> bool:
        return field in present or any(a in present for a in aliases.get(field, []))

    missing = [c for c in _REQUIRED_PAYEE_COLUMNS if not _has(c)]
    if missing:
        raise ValueError(
            f"'{path}' is missing required column(s): {', '.join(missing)}. "
            f"Columns found: {', '.join(sorted(present))}"
        )

    payees: list[Payee] = []
    by_id: dict[str, dict[str, Any]] = {}
    has_period = _has("period")

    for i, row in enumerate(rows, start=2):
        if _is_blank(row):
            continue
        try:
            fields = _payee_fields(row, aliases)
            if not fields["id"]:
                raise ValueError("id is empty")
            quota_val = _number(_get_field(row, "quota", aliases), "quota")
            if has_period:
                period = _get_field(row, "period", aliases)
                if fields["id"] not in by_id:
                    by_id[fields["id"]] = {**fields, "quota": Decimal("0"), "quotas": {}}
                if period:
                    by_id[fields["id"]]["quotas"][period] = quota_val
                else:
                    by_id[fields["id"]]["quota"] = quota_val
            else:
                payees.append(Payee(**fields, quota=quota_val))
        except Exception as e:
            raise ValueError(f"Row {i} in '{path}': {e}") from e

    if has_period:
        payees = [Payee(**data) for data in by_id.values()]
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


def _pq_str(row: dict[str, Any], key: str) -> str:
    """Read one Parquet cell as a trimmed string; missing and null both give ""."""
    v = row.get(key)
    return "" if v is None else str(v).strip()


def _load_transactions_parquet(path: Path) -> list[Transaction]:
    """Load transactions from a Parquet file. Column names must match canonical
    field names exactly (no fuzzy mapping)."""
    import pyarrow.parquet as pq

    table = pq.read_table(path)  # type: ignore[no-untyped-call]
    transactions: list[Transaction] = []
    for i in range(table.num_rows):
        row = {col: table.column(col)[i].as_py() for col in table.column_names}
        try:
            # Every field the model understands must be named here, or it silently
            # lands in metadata instead: a `credits` column falling through means
            # a split deal pays one person 100%.
            known_txn = {
                "id", "payee_id", "deal_id", "period", "amount", "product", "close_date",
                "bill_rate", "pay_rate", "units", "margin", "credits", "quota_amount",
            }
            meta = {k: v for k, v in row.items() if k and k not in known_txn}
            close_date_str = _pq_str(row, "close_date")
            bill_raw, pay_raw = _pq_str(row, "bill_rate"), _pq_str(row, "pay_rate")
            units_raw, margin_raw = _pq_str(row, "units"), _pq_str(row, "margin")
            credits_raw, quota_raw = _pq_str(row, "credits"), _pq_str(row, "quota_amount")
            t = Transaction(
                id=str(row["id"]),
                payee_id=str(row["payee_id"]),
                deal_id=_pq_str(row, "deal_id"),
                period=_pq_str(row, "period"),
                amount=Decimal(str(row["amount"])),
                product=_pq_str(row, "product") or None,
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


def _load_payees_parquet(path: Path) -> list[Payee]:
    """Load payees from a Parquet file. Column names must match canonical
    field names exactly (no fuzzy mapping)."""
    import pyarrow.parquet as pq

    table = pq.read_table(path)  # type: ignore[no-untyped-call]
    payees: list[Payee] = []
    for i in range(table.num_rows):
        raw = {col: table.column(col)[i].as_py() for col in table.column_names}
        # Normalise to the string dict the shared helper parsers expect, so this
        # path stays field-for-field identical to the CSV one. It previously
        # dropped draw, manager hierarchy, team and category quotas outright.
        row = {k: ("" if v is None else str(v)) for k, v in raw.items()}
        try:
            effective_from_str = row.get("effective_from", "").strip()
            effective_to_str = row.get("effective_to", "").strip()
            payees.append(
                Payee(
                    id=row["id"].strip(),
                    name=row["name"].strip(),
                    quota=Decimal(row["quota"].strip()),
                    plan_id=row["plan_id"].strip(),
                    effective_from=_parse_date(effective_from_str) if effective_from_str else None,
                    effective_to=_parse_date(effective_to_str) if effective_to_str else None,
                    email=row.get("email", "").strip() or None,
                    ramp=_parse_ramp(row),
                    draw=_parse_draw(row),
                    category_quotas=_parse_category_quotas(row),
                    manager_id=row.get("manager_id", "").strip(),
                    manager_override=_parse_optional_decimal(
                        row.get("manager_override"), "manager_override"),
                    team_id=row.get("team_id", "").strip(),
                )
            )
        except Exception as e:
            raise ValueError(f"Row {i} in '{path}': {e}") from e
    return payees


def _parse_optional_decimal(raw: object, field: str = "value") -> Decimal | None:
    """Parse an optional decimal. Empty or missing is None; unparseable raises.

    Trailing-percent notation is accepted and converted ("2%" -> 0.02), because
    the recognised column heading for manager_override is literally
    "manager override %" — writing 2% in it is the natural thing to do, and
    silently discarding it costs the manager their entire override.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    is_pct = s.endswith("%")
    if is_pct:
        s = s[:-1].strip()
    try:
        value = Decimal(s)
    except InvalidOperation as e:
        raise ValueError(
            f"{field}: cannot read {str(raw).strip()!r} as a number. "
            f"Use a decimal (0.02) or a percentage (2%)."
        ) from e
    return value / Decimal("100") if is_pct else value


def _parse_date(s: str) -> date:
    if not s or not s.strip():
        raise ValueError("Cannot parse empty date")
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: '{s}'")


_TRUTHY = frozenset({"true", "1", "yes", "y", "t", "on"})
_FALSEY = frozenset({"false", "0", "no", "n", "f", "off"})


def _parse_ramp(row: dict[str, str]) -> RampSchedule | None:
    """Parse ramp_months and ramp_schedule columns into a RampSchedule.

    ramp_months: integer duration.
    ramp_schedule: space-separated decimal multipliers (e.g. "0.5 0.75 1.0").
    Returns None if either column is missing or empty.
    """
    months_raw = (row.get("ramp_months") or "").strip()
    schedule_raw = (row.get("ramp_schedule") or "").strip()
    if not months_raw and not schedule_raw:
        return None
    if not months_raw or not schedule_raw:
        missing = "ramp_schedule" if months_raw else "ramp_months"
        raise ValueError(
            f"ramp is half-specified: {missing} is empty. Set both columns, or "
            f"neither — a partial ramp would silently apply no quota relief."
        )
    months = int(months_raw)
    schedule = [Decimal(v) for v in schedule_raw.split()]
    return RampSchedule(months=months, schedule=schedule)


def _parse_draw(row: dict[str, str]) -> Draw | None:
    """Parse draw_amount and draw_recoverable columns into a Draw."""
    amount_raw = (row.get("draw_amount") or "").strip()
    if not amount_raw:
        return None
    recoverable_raw = (row.get("draw_recoverable") or "").strip().lower()
    if recoverable_raw and recoverable_raw not in _TRUTHY | _FALSEY:
        raise ValueError(
            f"draw_recoverable: cannot read {recoverable_raw!r} as true or false. "
            f"A draw defaulting to non-recoverable is money the company never "
            f"recovers, so this has to be explicit."
        )
    return Draw(amount=Decimal(amount_raw), recoverable=recoverable_raw in _TRUTHY)


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


_PARENTHETICAL = re.compile(r"\([^)]*\)")


def _norm(h: str) -> str:
    """Normalise a CSV header to its canonical form.

    Real exports annotate units in the heading — "Bill Rate (GBP/hr)",
    "Amount ($)", "Manager Override %". Previously only the brackets were
    removed, so "Bill Rate (GBP/hr)" became "bill_rate_gbp/hr", matched no
    field, and fell into metadata: the rate silently vanished and margin
    rules stopped applying. Drop the whole parenthetical instead, and
    collapse runs of spaces and underscores.
    """
    text = _PARENTHETICAL.sub(" ", h).strip().lower().rstrip("%").strip()
    return re.sub(r"[\s_]+", "_", text).strip("_")


def _alias_index() -> dict[str, list[str]]:
    """Canonical field name -> its normalised aliases."""
    from icm_engine.mapping import _FIELD_ALIASES

    return {
        canonical: [_norm(a) for a in aliases]
        for canonical, aliases in _FIELD_ALIASES.items()
    }


def _normalise_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Rewrite every row's keys through _norm, dropping csv restkey overflow."""
    if not rows:
        return []
    remap = {k: _norm(k) for k in rows[0] if k is not None}
    return [
        {remap.get(k, k): v for k, v in r.items() if k is not None}
        for r in rows
    ]


def _get_field(row: dict[str, str], canonical: str, aliases: dict[str, list[str]]) -> str:
    """Read a field by canonical name, falling back to its normalised aliases."""
    val = row.get(canonical)
    if val is not None:
        return str(val).strip()
    for alias in aliases.get(canonical, []):
        val = row.get(alias)
        if val is not None:
            return str(val).strip()
    return ""


def _number(raw: str, field: str) -> Decimal:
    """Parse a numeric cell, naming the column and the offending value.

    Deliberately does not strip thousands separators: "1,234" is 1234 to a US
    export and 1.234 to a European one, and guessing wrong is a thousand-fold
    error on someone's pay. Refusing is the only safe reading.
    """
    text = raw.strip()
    try:
        return Decimal(text)
    except InvalidOperation as e:
        raise ValueError(
            f"{field}: cannot read {text!r} as a number. Write a plain decimal "
            f"(12500.00) — no currency symbol, thousands separator or brackets."
        ) from e

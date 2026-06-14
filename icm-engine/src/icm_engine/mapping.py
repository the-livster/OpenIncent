from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from rapidfuzz import fuzz

_FIELD_ALIASES: dict[str, list[str]] = {
    "id": ["id", "transaction id", "txn id", "trans id", "row id", "record id"],
    "payee_id": [
        "rep", "rep name", "sales rep", "salesperson", "sales person",
        "employee", "employee name", "name", "payee", "agent", "broker",
        "consultant", "owner", "account owner", "person", "associate",
    ],
    "name": [
        "name", "full name", "employee name", "employee", "rep name",
        "person", "individual", "contact",
    ],
    "deal_id": [
        "deal", "deal id", "opportunity", "opportunity id", "opp id",
        "oppty id", "opp", "quote id", "order id", "contract id",
    ],
    "period": ["period", "fiscal period", "fy period", "commission period"],
    "amount": [
        "amount", "acv", "tcv", "value", "deal size", "deal value",
        "revenue", "sale amount", "price", "net", "gross", "$",
        "acv ($)", "tcv ($)", "amount ($)", "value ($)",
    ],
    "product": [
        "product", "sku", "product name", "product family", "product line",
        "item", "solution", "offering", "edition", "tier",
    ],
    "close_date": [
        "close date", "close", "closed", "closed date", "won date",
        "date", "close_date", "deal date", "sign date", "effective date",
        "transaction date", "txn date", "booked date", "booking date",
    ],
    "quota": ["quota", "target", "goal", "annual quota", "yearly target"],
    "plan_id": ["plan", "plan id", "comp plan", "plan name", "plan_id"],
    "effective_from": [
        "effective from", "effective date", "start date", "start",
        "effective_from", "from date", "begin date",
    ],
    "effective_to": [
        "effective to", "end date", "to date", "effective_to", "until", "through",
    ],
    "manager_id": [
        "manager", "manager id", "reports to", "supervisor", "supervisor id",
        "team lead", "team lead id", "manager_id",
    ],
    "manager_override": [
        "manager override", "manager rate", "manager %", "override",
        "manager override %", "manager_override",
    ],
    "bill_rate": [
        "bill rate", "bill", "charge rate", "client rate", "bill_rate",
    ],
    "pay_rate": [
        "pay rate", "pay", "cost rate", "contractor rate", "candidate rate", "pay_rate",
    ],
    "units": [
        "hours", "units", "days", "quantity", "qty",
    ],
    "margin": [
        "margin", "gross profit", "gp", "gross margin", "spread",
    ],
    "credits": [
        "credits", "credit", "splits", "split", "deal split", "deal splits",
        "split credits", "credit splits", "commission split",
    ],
    "quota_amount": [
        "quota amount", "quota_amount", "quota credit", "quota value",
        "attainment amount", "booking amount", "quota retired",
    ],
}


@dataclass
class ColumnMapping:
    file_pattern: str  # "transactions" or "payees"
    mappings: dict[str, str]  # source_column -> target_field
    transformations: dict[str, str] = field(default_factory=dict)  # target_field -> transform
    confidence: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_pattern": self.file_pattern,
            "mappings": self.mappings,
            "transformations": self.transformations,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ColumnMapping:
        return cls(
            file_pattern=d["file_pattern"],
            mappings=d.get("mappings", {}),
            transformations=d.get("transformations", {}),
            confidence=d.get("confidence", {}),
        )


class MappingError(Exception):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        lines: list[str] = [f"Mapping failed with {len(errors)} row error(s):"]
        for e in errors[:10]:
            lines.append(f"  Row {e['row']}: {e['error']}")
        if len(errors) > 10:
            lines.append(f"  ... and {len(errors) - 10} more")
        super().__init__("\n".join(lines))


def infer_mapping(headers: list[str], file_pattern: str) -> ColumnMapping:
    """Infer a column mapping from source headers to target fields.

    Args:
        headers: Source column names.
        file_pattern: "transactions" or "payees".

    Returns:
        ColumnMapping with best-match mappings and confidence scores.
    """
    if file_pattern == "transactions":
        target_fields = ["id", "payee_id", "deal_id", "period", "amount", "product", "close_date",
                         "bill_rate", "pay_rate", "units", "margin", "credits", "quota_amount"]
    elif file_pattern == "payees":
        target_fields = ["id", "name", "quota", "plan_id", "effective_from", "effective_to"]
    else:
        raise ValueError(f"Unknown file_pattern: {file_pattern}")

    mappings: dict[str, str] = {}
    confidence: dict[str, float] = {}
    transformations: dict[str, str] = {}

    normalized_headers = [_normalize(h) for h in headers]

    # Build score matrix: (source_index, target, score, token_sort_score)
    candidates: list[tuple[int, str, float, float]] = []
    for i, nh in enumerate(normalized_headers):
        for target in target_fields:
            ts, combined = _match_score_pair(nh, target)
            if combined >= 70.0:
                candidates.append((i, target, combined, ts))

    # Greedy assignment by descending combined score; when tied, prefer
    # higher token_sort (more specific match over short-substring match).
    candidates.sort(key=lambda x: (x[2], x[3]), reverse=True)

    used_src: set[int] = set()
    used_target: set[str] = set()

    for src_idx, target, score, _ts in candidates:
        if src_idx in used_src or target in used_target:
            continue
        src = headers[src_idx]
        mappings[src] = target
        confidence[src] = score / 100.0
        used_src.add(src_idx)
        used_target.add(target)

    # Suggest transformations
    for _src, target in mappings.items():
        if target in ("amount", "quota"):
            transformations[target] = "strip_currency"

    return ColumnMapping(
        file_pattern=file_pattern,
        mappings=mappings,
        transformations=transformations,
        confidence=confidence,
    )


def apply_mapping(
    rows: list[dict[str, str]],
    mapping: ColumnMapping,
    target_schema: type[BaseModel],
) -> list[Any]:
    """Apply column mapping and transformations, returning validated model instances.

    Args:
        rows: Raw row dicts with source column names.
        mapping: ColumnMapping to apply.
        target_schema: Pydantic model class to validate against.

    Returns:
        List of validated model instances.

    Raises:
        MappingError: if any rows fail validation or transformation.
    """
    reverse_map: dict[str, str] = {v: k for k, v in mapping.mappings.items()}
    errors: list[dict[str, Any]] = []
    results: list[Any] = []

    for row_idx, row in enumerate(rows):
        mapped: dict[str, Any] = {}
        for target_field, src_col in reverse_map.items():
            val = row.get(src_col, "")
            tname = mapping.transformations.get(target_field)
            try:
                mapped[target_field] = _apply_transform(val, tname)
            except Exception as e:
                errors.append({
                    "row": row_idx + 1,
                    "field": target_field,
                    "value": val,
                    "error": f"Transform '{tname}' failed: {e}",
                })
                mapped[target_field] = None

        try:
            results.append(target_schema.model_validate(mapped))
        except Exception as e:
            errors.append({
                "row": row_idx + 1,
                "error": str(e),
            })

    if errors:
        raise MappingError(errors)

    return results


def save_mapping(mapping: ColumnMapping, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(
            mapping.to_dict(), f,
            default_flow_style=False, allow_unicode=True, sort_keys=False,
        )


def load_mapping(path: Path) -> ColumnMapping:
    with path.open(encoding="utf-8") as f:
        d = yaml.safe_load(f)
    if not isinstance(d, dict):
        raise ValueError(f"Mapping file '{path}' must be a YAML mapping")
    return ColumnMapping.from_dict(d)


def _normalize(s: str) -> str:
    return s.lower().replace("_", " ").replace("(", "").replace(")", "").strip()


def _match_score(source: str, target: str) -> float:
    _, combined = _match_score_pair(_normalize(source), target)
    return combined


def _match_score_pair(src_norm: str, target: str) -> tuple[float, float]:
    """Return (best_token_sort, best_combined) for a source/target pair."""
    aliases = _FIELD_ALIASES.get(target, [target])
    best_ts = 0.0
    best_combined = 0.0
    for alias in aliases:
        ts = fuzz.token_sort_ratio(src_norm, alias.lower())
        pr = fuzz.partial_ratio(src_norm, alias.lower())
        combined = max(ts, pr, ts * 0.7 + pr * 0.3)
        if combined > best_combined:
            best_combined = combined
            best_ts = ts
    return best_ts, best_combined


def _apply_transform(value: str, transform: str | None) -> Any:
    if not value:
        return value
    if transform == "strip_currency":
        return _tx_strip_currency(value)
    elif transform == "parse_date_us":
        return _tx_parse_date_us(value)
    elif transform == "parse_date_eu":
        return _tx_parse_date_eu(value)
    elif transform == "lowercase":
        return value.lower()
    elif transform == "uppercase":
        return value.upper()
    elif transform == "strip":
        return value.strip()
    return value


def _tx_strip_currency(s: str) -> str:
    cleaned = s.replace("$", "").replace(",", "").replace(" ", "")
    # Validate it parses as Decimal
    try:
        Decimal(cleaned)
        return cleaned
    except InvalidOperation:
        raise ValueError(f"Cannot parse currency value: '{s}'") from None


def _tx_parse_date_us(s: str) -> str:
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Cannot parse US date: '{s}'")


def _tx_parse_date_eu(s: str) -> str:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Cannot parse EU date: '{s}'")

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)

_MONEY_COLUMNS = {"rate", "commission", "commission_amount", "base_amount", "amount", "quota"}
_MONEY_SUFFIXES = ("_amount",)
_TOTAL_KEYWORDS = {"total", "grand total", "totals"}


def read_xlsx_rows(
    path: Path, sheet: str | None = None
) -> tuple[list[str], list[dict[str, str]]]:
    """Read an xlsx file into (headers, rows-as-dicts).

    Args:
        path: Path to .xlsx file.
        sheet: Sheet name. If None, uses the first non-empty sheet.

    Returns:
        Tuple of (header_list, list_of_row_dicts).
    """
    wb = load_workbook(path, data_only=True)

    sheet_name = sheet or _pick_sheet(wb)
    ws = wb[sheet_name]

    _unmerge(ws)
    raw_rows: list[list[Any]] = [
        [cell.value for cell in row] for row in ws.iter_rows()
    ]

    raw_rows = _drop_blank_rows(raw_rows)
    raw_rows = _drop_total_rows(raw_rows)

    if not raw_rows:
        return ([], [])

    header_idx = _find_header_row(raw_rows)
    headers = [_normalize_header(raw_rows[header_idx][i]) for i in range(len(raw_rows[header_idx]))]
    headers = _deduplicate_headers(headers)

    data_rows = raw_rows[header_idx + 1:]
    result: list[dict[str, str]] = []
    for row in data_rows:
        row_dict: dict[str, str] = {}
        for i, h in enumerate(headers):
            val = row[i] if i < len(row) else None
            row_dict[h] = _cell_to_str(val)
        # Skip fully blank rows
        if any(v for v in row_dict.values()):
            result.append(row_dict)

    return (headers, result)


def write_xlsx(
    path: Path, sheets: dict[str, list[dict[str, Any]]]
) -> None:
    """Write an xlsx workbook.

    Args:
        path: Output path.
        sheets: Dict of sheet_name -> list of row dicts.
    """
    wb = Workbook()
    wb.remove(wb.active)

    for sname, rows in sheets.items():
        ws = wb.create_sheet(title=sname)
        if not rows:
            continue

        headers = list(rows[0].keys())
        ws.append(headers)

        for row in rows:
            values = [row.get(h, "") for h in headers]
            ws.append(values)

        # Formatting
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font += Font(bold=True)

            # Determine if money column
            is_money = h in _MONEY_COLUMNS or any(
                h.endswith(s) for s in _MONEY_SUFFIXES
            )
            if is_money:
                for row_idx in range(2, ws.max_row + 1):
                    ws.cell(row=row_idx, column=col_idx).number_format = "$#,##0.0000"

            # Approximate column width
            max_len = len(str(h))
            for row_idx in range(2, ws.max_row + 1):
                v = ws.cell(row=row_idx, column=col_idx).value
                if v is not None:
                    max_len = max(max_len, len(str(v)))
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 40)

        ws.freeze_panes = ws.cell(row=2, column=1)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _pick_sheet(wb: Workbook) -> str:
    """Pick the first non-empty sheet; warn if multiple sheets exist."""
    sheets = wb.sheetnames
    if len(sheets) > 1:
        logger.warning(
            "Workbook has %d sheets: %s. Using '%s'. Pass --sheet to override.",
            len(sheets),
            ", ".join(sheets),
            sheets[0],
        )
    for name in sheets:
        ws = wb[name]
        if ws.max_row > 0 and ws.max_column > 0:
            return str(name)
    return str(sheets[0])


def _unmerge(ws: Any) -> None:
    """Unmerge all merged cells in-place, propagating the top-left value."""
    merged = list(ws.merged_cells.ranges)
    for rng in merged:
        top_left = ws.cell(rng.min_row, rng.min_col).value
        ws.unmerge_cells(str(rng))
        for row in range(rng.min_row, rng.max_row + 1):
            for col in range(rng.min_col, rng.max_col + 1):
                ws.cell(row, col).value = top_left


def _drop_blank_rows(rows: list[list[Any]]) -> list[list[Any]]:
    return [r for r in rows if any(c is not None for c in r)]


def _drop_total_rows(rows: list[list[Any]]) -> list[list[Any]]:
    result: list[list[Any]] = []
    for r in rows:
        if r and isinstance(r[0], str) and r[0].strip().lower() in _TOTAL_KEYWORDS:
            continue
        result.append(r)
    return result


def _find_header_row(rows: list[list[Any]]) -> int:
    """Find the row most likely to be the header.

    Scans first 10 rows; picks the row with the most non-empty cells
    that look like text (not numbers, not dates). Favors rows with
    unique cell values (headers) over rows with repeated text (merged titles).
    """
    scan = rows[:10]
    best_idx = 0
    best_score = -1
    best_unique = -1

    for i, r in enumerate(scan):
        text_cells = [str(c).strip().lower() for c in r if isinstance(c, str) and c.strip()]
        text_count = len(text_cells)
        unique_count = len(set(text_cells))
        score = text_count
        if score > best_score or (score == best_score and unique_count > best_unique):
            best_score = score
            best_unique = unique_count
            best_idx = i

    return best_idx


def _normalize_header(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    return s.lower().replace(" ", "_").replace("(", "").replace(")", "")


def _deduplicate_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for h in headers:
        if h in seen:
            seen[h] += 1
            result.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            result.append(h)
    return result


def _cell_to_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    if isinstance(val, date):
        return val.isoformat()
    if isinstance(val, float):
        # Preserve as string without rounding artifacts
        s = f"{val:.10f}".rstrip("0").rstrip(".")
        return s
    if isinstance(val, Decimal):
        return str(val)
    return str(val).strip()

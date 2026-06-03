"""Per-rep commission statement generation.

Generates one statement file PER payee, per period. Each file contains ONLY
that payee's data — no other payee's id, name, or amounts may appear.

Formats: xlsx, html (stdlib only), pdf (optional fpdf2 extra).
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any


@dataclass
class StatementFile:
    payee_id: str
    period: str | None
    path: Path
    fmt: str  # "xlsx" | "html" | "pdf"


def generate_statements(
    commissions: list[Any],
    payees: list[Any],
    *,
    out_dir: Path,
    period: str | None = None,
    formats: tuple[str, ...] = ("xlsx",),
    generated_on: date | None = None,
    emit_zero: bool = False,
    attainment: list[Any] | None = None,
) -> list[StatementFile]:
    """Generate per-payee commission statements.

    Args:
        commissions: Commission objects with payee_id, period, transaction_id,
                     deal_id, rule_id, base_amount, rate, commission_amount, notes.
        payees: Payee objects with id, name.
        out_dir: Output directory.
        period: If set, filter to this period only.
        formats: Which formats to generate ("xlsx", "html", "pdf").
        generated_on: Injectable date for deterministic output.
        emit_zero: If True, emit $0 statements for payees with no lines.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build payee lookup
    payee_map: dict[str, Any] = {}
    for p in payees:
        pid = getattr(p, "id", None)
        if pid is None and hasattr(p, "get"):
            pid = p.get("id", "")
        payee_map[pid or ""] = p

    # Group commissions by payee_id
    by_payee: dict[str, list[Any]] = {}
    for c in commissions:
        pid = _get_attr(c, "payee_id")
        c_period = _get_attr(c, "period")
        if period and c_period != period:
            continue
        by_payee.setdefault(pid, []).append(c)

    # Get all payee ids to process
    all_pids = set(by_payee.keys())
    if emit_zero:
        all_pids.update(payee_map.keys())

    files: list[StatementFile] = []
    period_label = period or "all"

    for pid in sorted(all_pids):
        lines = by_payee.get(pid, [])
        if not lines and not emit_zero:
            continue

        p = payee_map.get(pid)
        pname = _get(p, "name", pid) if p else pid
        total = sum(
            Decimal(_get_attr(c, "commission_amount", "0"))
            for c in lines
        )

        for fmt in formats:
            if fmt == "xlsx":
                path = _write_xlsx(pid, pname, period_label, lines, total, out_dir)
            elif fmt == "html":
                path = _write_html(pid, pname, period_label, lines, total, generated_on, out_dir, attainment)
            elif fmt == "pdf":
                path = _write_pdf(pid, pname, period_label, lines, total, generated_on, out_dir)
            else:
                raise ValueError(f"Unknown format: {fmt}")
            files.append(StatementFile(payee_id=pid, period=period, path=path, fmt=fmt))

    return files


# ------------------------------------------------------------------
# XLSX
# ------------------------------------------------------------------

def _write_xlsx(
    pid: str, pname: str, period: str,
    lines: list[Any], total: Decimal, out_dir: Path,
) -> Path:
    from icm_engine.excel import write_xlsx

    rows = []
    for c in lines:
        rows.append({
            "transaction_id": _get(c, "transaction_id"),
            "deal_id": _get(c, "deal_id", ""),
            "rule_id": _get(c, "rule_id"),
            "base_amount": str(_get_dec(c, "base_amount")),
            "rate": str(_get_dec(c, "rate")),
            "commission": str(_get_dec(c, "commission_amount")),
            "notes": _get(c, "notes", ""),
        })
    rows.append({
        "transaction_id": "TOTAL",
        "deal_id": "",
        "rule_id": "",
        "base_amount": "",
        "rate": "",
        "commission": str(total),
        "notes": "",
    })

    path = out_dir / f"statement_{pid}_{period}.xlsx"
    write_xlsx(path, {"Statement": rows})
    return path


# ------------------------------------------------------------------
# HTML (stdlib only)
# ------------------------------------------------------------------

def _write_html(
    pid: str, pname: str, period: str,
    lines: list[Any], total: Decimal, generated_on: date | None, out_dir: Path,
    attainment: list[Any] | None = None,
) -> Path:
    esc = html.escape
    date_str = f"<p>Generated: {esc(str(generated_on))}</p>" if generated_on else ""

    # Attainment line
    att_html = ""
    if attainment:
        for a in attainment:
            if _get(a, "payee_id") == pid:
                bookings = _get(a, "bookings")
                quota = _get(a, "quota")
                pct_raw = getattr(a, "attainment_pct", None)
                if pct_raw is not None:
                    pct = f"{float(str(pct_raw)) * 100:.1f}%"
                else:
                    pct = "N/A"
                att_html = (
                    f"<p class='attainment'>You booked ${esc(bookings)} "
                    f"against your ${esc(quota)} quota ({pct}).</p>"
                )
                break
    rows_html = ""
    for c in lines:
        rows_html += (
            f"<tr>"
            f"<td>{esc(str(_get(c, 'transaction_id')))}</td>"
            f"<td>{esc(str(_get(c, 'rule_id')))}</td>"
            f"<td class='num'>${esc(str(_get_dec(c, 'base_amount')))}</td>"
            f"<td class='num'>{esc(str(_get_dec(c, 'rate')))}</td>"
            f"<td class='num'>${esc(str(_get_dec(c, 'commission_amount')))}</td>"
            f"<td>{esc(str(_get(c, 'notes', '')))}</td>"
            f"</tr>\n"
        )

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Commission Statement</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 700px; margin: 2rem auto; color: #1a1a2e; }}
  h1 {{ font-size: 1.25rem; margin-bottom: 0.25rem; }}
  .meta {{ color: #666; font-size: 0.85rem; margin-bottom: 1.5rem; }}
  .attainment {{ color: #2d6a4f; font-weight: 600; font-size: 0.95rem; margin-bottom: 1.5rem; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ padding: 0.5rem 0.75rem; text-align: left; border-bottom: 1px solid #eee; font-size: 0.9rem; }}
  th {{ background: #f5f5f5; font-weight: 600; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .total {{ font-weight: 700; border-top: 2px solid #333; }}
  .footer {{ margin-top: 2rem; color: #999; font-size: 0.75rem; }}
</style></head>
<body>
  <h1>Commission Statement</h1>
  <div class="meta">
    <p><strong>{esc(pname)}</strong> ({esc(pid)}) - Period: {esc(period)}</p>
    {att_html}
    {date_str}
  </div>
  <table>
    <thead><tr><th>Transaction</th><th>Rule</th><th>Base</th><th>Rate</th><th>Commission</th><th>Notes</th></tr></thead>
    <tbody>{rows_html}</tbody>
    <tfoot><tr class="total"><td colspan="4">Total</td><td class="num">${esc(str(total))}</td><td></td></tr></tfoot>
  </table>
  <div class="footer">Generated by OpenIncent</div>
</body>
</html>"""

    path = out_dir / f"statement_{pid}_{period}.html"
    path.write_text(html_doc, encoding="utf-8")
    return path


# ------------------------------------------------------------------
# PDF (optional fpdf2 extra)
# ------------------------------------------------------------------

def _write_pdf(
    pid: str, pname: str, period: str,
    lines: list[Any], total: Decimal, generated_on: date | None, out_dir: Path,
) -> Path:
    try:
        from fpdf import FPDF
    except ImportError:
        raise ImportError(
            "PDF support requires fpdf2. Install with: pip install icm-engine[pdf] "
            "or: uv pip install fpdf2"
        ) from None

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Commission Statement", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"{pname} ({pid})  -  Period: {period}", new_x="LMARGIN", new_y="NEXT")
    if generated_on:
        pdf.cell(0, 6, f"Generated: {generated_on}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Table header
    pdf.set_font("Helvetica", "B", 9)
    cols = ("Transaction", "Rule", "Base", "Rate", "Commission")
    widths = (36, 28, 28, 18, 28)
    for col, w in zip(cols, widths, strict=True):
        pdf.cell(w, 7, col, border=1)
    pdf.ln()

    # Rows
    pdf.set_font("Helvetica", "", 9)
    for c in lines:
        row = (
            str(_get(c, "transaction_id"))[:20],
            str(_get(c, "rule_id"))[:15],
            f"${_get_dec(c, 'base_amount')}",
            str(_get_dec(c, "rate")),
            f"${_get_dec(c, 'commission_amount')}",
        )
        for val, w in zip(row, widths, strict=True):
            pdf.cell(w, 6, val, border=1)
        pdf.ln()

    # Total
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(sum(widths[:4]), 7, "Total", border=1)
    pdf.cell(widths[4], 7, f"${total}", border=1, align="R")

    path = out_dir / f"statement_{pid}_{period}.pdf"
    pdf.output(str(path))
    return path


# ------------------------------------------------------------------
# Helpers for attribute/dict access
# ------------------------------------------------------------------

def _get_attr(obj: Any, attr: str, default: str = "") -> str:
    """Safely get a string attribute from an object or dict."""
    if hasattr(obj, attr):
        return str(getattr(obj, attr))
    if isinstance(obj, dict):
        return str(obj.get(attr, default))
    return default


def _get(obj: Any, attr: str, default: str = "") -> str:
    return _get_attr(obj, attr, default)


def _get_dec(obj: Any, attr: str) -> Decimal:
    val = _get(obj, attr, "0")
    return Decimal(val)

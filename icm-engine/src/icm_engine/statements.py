"""Per-rep commission statement generation.

Generates one statement file PER payee, per period. Each file contains ONLY
that payee's data — no other payee's id, name, or amounts may appear.

Formats: xlsx, html (interactive), pdf (optional fpdf2 extra).
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any


@dataclass
class StatementFile:
    payee_id: str
    period: str | None
    path: Path
    fmt: str  # "xlsx" | "html" | "pdf"


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


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
    plan_name: str = "",
) -> list[StatementFile]:
    """Generate per-payee commission statements.

    Args:
        commissions: Commission objects with payee_id, period, transaction_id,
                     origin_period, rule_id, base_amount, rate, commission_amount,
                     notes.
        payees: Payee objects with id, name.
        out_dir: Output directory.
        period: If set, filter to this period only.
        formats: Which formats to generate ("xlsx", "html", "pdf").
        generated_on: Injectable date for deterministic output.
        emit_zero: If True, emit $0 statements for payees with no lines.
        attainment: AttainmentSummary objects for attainment display.
        plan_name: Plan name for the statement header.
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
            (Decimal(_get_attr(c, "commission_amount", "0"))
            for c in lines),
            Decimal("0")
        )

        # Find attainment for this payee
        payee_attainment: Any = None
        if attainment:
            for a in attainment:
                if _get_attr(a, "payee_id") == pid:
                    payee_attainment = a
                    break

        for fmt in formats:
            if fmt == "xlsx":
                path = _write_xlsx(pid, pname, period_label, lines, total, out_dir,
                                   plan_name=plan_name, attainment=payee_attainment)
            elif fmt == "html":
                path = _write_html(pid, pname, period_label, lines, total, generated_on, out_dir,
                                   plan_name=plan_name, attainment=payee_attainment)
            elif fmt == "pdf":
                path = _write_pdf(pid, pname, period_label, lines, total, generated_on, out_dir,
                                  plan_name=plan_name, attainment=payee_attainment)
            else:
                raise ValueError(f"Unknown format: {fmt}")
            files.append(StatementFile(payee_id=pid, period=period, path=path, fmt=fmt))

    return files


# ------------------------------------------------------------------
# Display rounding
# ------------------------------------------------------------------


def _d(amount: Decimal) -> str:
    """Round a Decimal to 2 decimal places (half-up) for display."""
    return str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# ------------------------------------------------------------------
# Adjustment detection
# ------------------------------------------------------------------


def _adj_label(c: Any, pid: str) -> str:
    """Return an adjustment label if this line is an adjustment/cap/draw/manual."""
    rule_id = _get(c, "rule_id", "")
    notes = _get(c, "notes", "")
    origin = _get(c, "origin_period", "")
    c_period = _get(c, "period", "")
    amount = _get_dec(c, "commission_amount")

    # Explicit rule IDs take priority
    if rule_id == "manual_adjustment":
        return "Manual Adj"
    if rule_id in ("payout_cap", "cap_adjustment"):
        return "Cap"
    if rule_id == "draw":
        if amount < 0:
            return "Draw Recovery"
        return "Draw Top-up"

    # Heuristics from notes/content
    if amount < 0:
        return "Clawback"
    if "true_up" in notes.lower() or (origin and origin != c_period):
        return f"True-up from {origin}" if origin else "True-up"
    return ""


# ------------------------------------------------------------------
# XLSX
# ------------------------------------------------------------------


def _write_xlsx(
    pid: str, pname: str, period: str,
    lines: list[Any], total: Decimal, out_dir: Path,
    *, plan_name: str = "", attainment: Any = None,
) -> Path:
    from icm_engine.excel import write_xlsx

    # Header info rows
    info_rows: list[dict[str, str]] = []
    info_rows.append({"Field": "Plan", "Value": plan_name or "-"})
    info_rows.append({"Field": "Payee", "Value": f"{pname} ({pid})"})
    info_rows.append({"Field": "Period", "Value": period})
    if attainment is not None:
        booked = _d(_get_dec(attainment, "bookings"))
        quota = _d(_get_dec(attainment, "quota"))
        pct_val = _get_attainment_pct(attainment)
        if pct_val is not None:
            pct_str = f"{pct_val * 100:.1f}%"
        else:
            pct_str = "N/A"
        info_rows.append({"Field": "Attainment", "Value": f"${booked} / ${quota} ({pct_str})"})

    # Line items
    data_rows: list[dict[str, str]] = []
    for c in lines:
        adj = _adj_label(c, pid)
        row = {
            "transaction_id": _get(c, "transaction_id"),
            "deal_id": _get(c, "deal_id", ""),
            "product": _get(c, "product", ""),
            "rule_id": _get(c, "rule_id"),
            "base_amount": _d(_get_dec(c, "base_amount")),
            "rate": str(_get_dec(c, "rate")),
            "commission": _d(_get_dec(c, "commission_amount")),
            "adjustment": adj,
            "notes": _get(c, "notes", ""),
        }
        data_rows.append(row)

    # Total row
    data_rows.append({
        "transaction_id": "TOTAL",
        "deal_id": "",
        "product": "",
        "rule_id": "",
        "base_amount": "",
        "rate": "",
        "commission": _d(total),
        "adjustment": "",
        "notes": "",
    })

    path = out_dir / f"statement_{pid}_{period}.xlsx"
    write_xlsx(path, {"Info": info_rows, "Statement": data_rows})
    return path


# ------------------------------------------------------------------
# HTML (interactive, stdlib only)
# ------------------------------------------------------------------


def _write_html(
    pid: str, pname: str, period: str,
    lines: list[Any], total: Decimal, generated_on: date | None, out_dir: Path,
    *, plan_name: str = "", attainment: Any = None,
) -> Path:
    esc = html.escape
    date_str = f"<p>Generated: {esc(str(generated_on))}</p>" if generated_on else ""

    # Attainment line
    att_html = ""
    if attainment is not None:
        booked = _d(_get_dec(attainment, "bookings"))
        quota = _d(_get_dec(attainment, "quota"))
        pct_val = _get_attainment_pct(attainment)
        if pct_val is not None:
            pct = f"{pct_val * 100:.1f}%"
        else:
            pct = "N/A"
        att_html = (
            f"<p class='attainment'>You booked ${esc(booked)} "
            f"against your ${esc(quota)} quota ({pct}).</p>"
        )

    # Build rows with data attributes for interactivity
    rows_html_parts: list[str] = []
    for c in lines:
        adj = _adj_label(c, pid)
        base = _d(_get_dec(c, "base_amount"))
        rate = str(_get_dec(c, "rate"))
        comm = _d(_get_dec(c, "commission_amount"))
        notes = esc(str(_get(c, "notes", "")))
        tid = esc(str(_get(c, "transaction_id")))
        rid = esc(str(_get(c, "rule_id")))
        deal = esc(str(_get(c, "deal_id", "")))
        product = esc(str(_get(c, "product", "")))
        origin = esc(str(_get(c, "origin_period", "")))

        adj_cell = f'<span class="adj-badge">{esc(adj)}</span>' if adj else ""

        rows_html_parts.append(
            f"<tr class='deal-row'"
            f" data-tid='{tid}' data-rid='{rid}'"
            f" data-base='{esc(base)}' data-rate='{esc(rate)}'"
            f" data-comm='{esc(comm)}' data-notes='{notes}'"
            f" data-deal='{deal}' data-product='{product}'"
            f" data-origin='{origin}' data-adj='{esc(adj)}'"
            f">"
            f"<td>{tid}</td>"
            f"<td>{rid}</td>"
            f"<td>{deal}</td>"
            f"<td class='num'>${base}</td>"
            f"<td class='num'>{rate}</td>"
            f"<td class='num'>${comm}</td>"
            f"<td>{adj_cell}</td>"
            f"<td class='expand-icon'>+</td>"
            f"</tr>\n"
        )

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Commission Statement — {esc(pname)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 760px; margin: 2rem auto; padding: 0 1rem; color: #1a1a2e; }}
  h1 {{ font-size: 1.25rem; margin-bottom: 0.25rem; }}
  .meta {{ color: #666; font-size: 0.85rem; margin-bottom: 0.5rem; }}
  .attainment {{ color: #2d6a4f; font-weight: 600; font-size: 0.95rem; margin-bottom: 1.5rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th, td {{ padding: 0.5rem 0.6rem; text-align: left; border-bottom: 1px solid #e8e8e8; }}
  th {{ background: #f7f7f7; font-weight: 600; position: sticky; top: 0; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .total td {{ font-weight: 700; border-top: 2px solid #333; }}
  .footer {{ margin-top: 2rem; color: #999; font-size: 0.75rem; }}
  .adj-badge {{ display:inline-block; background:#fff3cd; color:#856404; font-size:0.72rem;
                padding:0.1rem 0.4rem; border-radius:3px; }}
  .expand-icon {{ cursor:pointer; color:#376; font-weight:bold; font-size:1.1rem;
                  user-select:none; text-align:center; }}
  .expand-icon:hover {{ color:#298; }}
  .detail-row {{ display:none; }}
  .detail-row.show {{ display:table-row; }}
  .detail-cell {{ background:#fafafa; padding:0.75rem 1rem; font-size:0.82rem; color:#444; }}
  .detail-cell strong {{ color:#1a1a2e; }}
  .detail-grid {{ display:grid; grid-template-columns:auto 1fr; gap:0.25rem 1.5rem; }}
</style></head>
<body>
  <h1>Commission Statement</h1>
  <div class="meta">
    <p><strong>{esc(pname)}</strong> ({esc(pid)})</p>
    <p>Plan: {esc(plan_name or '-')} &middot; Period: {esc(period)}</p>
    {att_html}
    {date_str}
  </div>
  <table>
    <thead><tr>
      <th>Txn</th><th>Rule</th><th>Deal</th>
      <th class="num">Base</th><th class="num">Rate</th><th class="num">Comm</th>
      <th>Adj</th><th></th>
    </tr></thead>
    <tbody>{''.join(rows_html_parts)}</tbody>
    <tfoot><tr class="total">
      <td colspan="6">Total</td><td class="num">${_d(total)}</td><td></td>
    </tr></tfoot>
  </table>
  <div class="footer">
    <p>This statement is confidential and intended only for {esc(pname)}.</p>
    <p>Questions? Contact your manager. &middot; Generated by OpenIncent</p>
  </div>
<script>
(function() {{
  var rows = document.querySelectorAll('.deal-row');
  rows.forEach(function(row) {{
    row.addEventListener('click', function() {{
      var next = row.nextElementSibling;
      if (next && next.classList.contains('detail-row')) {{
        next.classList.toggle('show');
        var icon = row.querySelector('.expand-icon');
        if (icon) icon.textContent = next.classList.contains('show') ? '−' : '+';
        return;
      }}
      // Create detail row
      var detail = document.createElement('tr');
      detail.className = 'detail-row show';
      var base = row.dataset.base;
      var rate = row.dataset.rate;
      var comm = row.dataset.comm;
      var notes = row.dataset.notes;
      var deal = row.dataset.deal;
      var product = row.dataset.product;
      var origin = row.dataset.origin;
      var adj = row.dataset.adj;
      detail.innerHTML = '<td colspan="8"><div class="detail-cell"><div class="detail-grid">'
        + (deal ? '<span>Deal:</span><strong>' + deal + '</strong>' : '')
        + (product ? '<span>Product:</span><strong>' + product + '</strong>' : '')
        + '<span>Calculation:</span><span>$' + base + ' &times; ' + rate + ' = <strong>$' + comm + '</strong></span>'
        + (notes ? '<span>Note:</span><span>' + notes + '</span>' : '')
        + (adj ? '<span>Adjustment:</span><span class="adj-badge">' + adj + '</span>' : '')
        + (origin ? '<span>Origin:</span><span>' + origin + '</span>' : '')
        + '</div></div></td>';
      row.parentNode.insertBefore(detail, row.nextSibling);
      row.querySelector('.expand-icon').textContent = '−';
    }});
  }});
}})();
</script>
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
    *, plan_name: str = "", attainment: Any = None,
) -> Path:
    try:
        from fpdf import FPDF
    except ImportError:
        raise ImportError(
            "PDF support requires fpdf2. Install with: pip install icm-engine[pdf] "
            "or: uv pip install fpdf2"
        ) from None

    col_w = {"txn": 34, "rule": 24, "base": 28, "rate": 16, "comm": 28, "adj": 28}

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Commission Statement", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, f"{pname} ({pid})  -  Plan: {plan_name or '-'}  -  Period: {period}",
             new_x="LMARGIN", new_y="NEXT")
    if attainment is not None:
        booked = _d(_get_dec(attainment, "bookings"))
        quota = _d(_get_dec(attainment, "quota"))
        pct_val = _get_attainment_pct(attainment)
        pct = f"{pct_val * 100:.1f}%" if pct_val is not None else "N/A"
        pdf.cell(0, 5, f"Attainment: ${booked} / ${quota} ({pct})",
                 new_x="LMARGIN", new_y="NEXT")
    if generated_on:
        pdf.cell(0, 5, f"Generated: {generated_on}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # Table header
    pdf.set_font("Helvetica", "B", 8)
    headers = ("Txn", "Rule", "Base", "Rate", "Comm", "Adj")
    for h, w in zip(headers, col_w.values(), strict=True):
        pdf.cell(w, 6, h, border=1)
    pdf.ln()

    # Rows
    pdf.set_font("Helvetica", "", 8)
    for c in lines:
        adj = _adj_label(c, pid)
        row = (
            str(_get(c, "transaction_id"))[:20],
            str(_get(c, "rule_id"))[:12],
            f"${_d(_get_dec(c, 'base_amount'))}",
            str(_get_dec(c, "rate")),
            f"${_d(_get_dec(c, 'commission_amount'))}",
            adj[:18] if adj else "",
        )
        for val, w in zip(row, col_w.values(), strict=True):
            pdf.cell(w, 5, val, border=1)
        pdf.ln()

    # Total
    pdf.set_font("Helvetica", "B", 8)
    # Sum width of first 4 columns for "Total" label
    label_w = sum(list(col_w.values())[:4])
    pdf.cell(label_w, 6, "Total", border=1)
    pdf.cell(col_w["comm"], 6, f"${_d(total)}", border=1, align="R")
    pdf.cell(col_w["adj"], 6, "", border=1)
    pdf.ln(6)

    # Confidentiality footer
    pdf.set_font("Helvetica", "I", 7)
    pdf.cell(0, 4, f"This statement is confidential and intended only for {pname}.",
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 4, "Questions? Contact your manager.  |  Generated by OpenIncent",
             new_x="LMARGIN", new_y="NEXT")

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


def _get_attainment_pct(attainment: Any) -> float | None:
    """Safely extract attainment_pct as a float, or None."""
    raw = getattr(attainment, "attainment_pct", None)
    if raw is None:
        return None
    return float(str(raw))

import { useState } from "react";
import type { PayeeRow } from "./Pipeline";
import { previewFile } from "../api";
import { Td } from "./Table";
import { useTableSort } from "./useTableSort";
import { FilterBar, FilterTh, SortTh } from "./SortableTable";

interface Props {
  payees: PayeeRow[];
  setPayees: (p: PayeeRow[]) => void;
  onNext: () => void;
}

export default function StagePayees({ payees, setPayees, onNext }: Props) {
  const [loading, setLoading] = useState(false);
  // The roster is built from /preview, which returns at most 50 rows. Anyone
  // past that would be dropped from the run without a word, so say so.
  const [truncated, setTruncated] = useState(false);

  const { paginated, totalItems, page, totalPages, setPage, sortCol, sortDir, filters, toggleSort, setFilter, clearFilters } = useTableSort(payees, "id");

  async function handleFile(f: File) {
    setLoading(true);
    try {
      const preview = await previewFile(f, "payees");
      const rows: PayeeRow[] = [];
      setTruncated(preview.preview_rows.length >= 50);
      for (const row of preview.preview_rows.slice(0, 50)) {
        const get = (target: string, fallback: string) => {
          const src = preview.mapping[target];
          if (src !== undefined) {
            const idx = preview.headers.indexOf(src);
            if (idx >= 0 && idx < row.length) return String(row[idx]);
          }
          return fallback;
        };
        rows.push({
          id: get("id", ""), name: get("name", ""), quota: get("quota", "0"),
          plan_id: get("plan_id", ""), effective_from: get("effective_from", ""),
          effective_to: get("effective_to", ""), email: get("email", ""),
          ramp_months: get("ramp_months", ""), ramp_schedule: get("ramp_schedule", ""),
          category_quotas: get("category_quotas", "{}"),
          draw_amount: get("draw_amount", ""), draw_recoverable: get("draw_recoverable", ""),
          manager_id: get("manager_id", ""), manager_override: get("manager_override", ""),
          team_id: get("team_id", ""),
        });
      }
      setPayees(rows);
    } catch {
      const text = await f.text();
      const lines = text.trim().split(/\r?\n/);
      if (lines.length < 2) return;
      const headers = lines[0].split(",").map(h => h.trim().toLowerCase());
      const rows: PayeeRow[] = [];
      for (let i = 1; i < Math.min(lines.length, 51); i++) {
        const cells = lines[i].split(",").map(c => c.trim().replace(/^"|"$/g, ""));
        const get = (names: string[]) => {
          for (const n of names) { const idx = headers.indexOf(n); if (idx >= 0) return cells[idx] || ""; }
          return "";
        };
        rows.push({
          id: cells[0] || "", name: cells[1] || "", quota: cells[2] || "0",
          plan_id: cells[3] || "", effective_from: cells[4] || "",
          effective_to: cells[5] || "", email: cells[6] || "",
          ramp_months: get(["ramp_months"]), ramp_schedule: get(["ramp_schedule"]),
          category_quotas: get(["category_quotas"]) || "{}",
          draw_amount: get(["draw_amount"]), draw_recoverable: get(["draw_recoverable"]),
          manager_id: get(["manager_id", "manager"]),
          manager_override: get(["manager_override", "manager rate"]),
          team_id: get(["team_id", "team"]),
        });
      }
      setPayees(rows);
    }
    setLoading(false);
  }

  return (
    <div className="max-w-3xl space-y-4">
      <h1 className="text-lg font-bold text-zinc-800">1. Payees</h1>
      <p className="text-sm text-zinc-500">Upload your payee roster (CSV or XLSX). Columns: id, name, quota, plan_id, effective_from.</p>
      {truncated && (
        <div className="px-3 py-2 rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs">
          Only the first {payees.length} payees were loaded — this screen reads a
          preview of the file, not all of it. A larger roster will not calculate
          in full here. Import it under Payees, or use the CLI.
        </div>
      )}
      {payees.length === 0 ? (
        <div className="border-2 border-dashed border-zinc-300 rounded-xl p-8 text-center space-y-3">
          <p className="text-sm text-zinc-500">Drop a CSV or XLSX file with columns: id, name, quota, plan_id, effective_from</p>
          <label className="inline-block px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 cursor-pointer">
            {loading ? "Loading..." : "Upload Payees"}
            <input type="file" accept=".csv,.xlsx" className="hidden" onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f); }} />
          </label>
        </div>
      ) : (
        <div className="space-y-3">
          <FilterBar total={payees.length} shown={totalItems} filters={filters} onClear={clearFilters} />
          <table className="w-full text-xs border rounded-lg overflow-hidden">
            <thead className="bg-zinc-100">
              <tr>
                <SortTh col="id" label="ID" current={sortCol} dir={sortDir} onClick={toggleSort} />
                <SortTh col="name" label="Name" current={sortCol} dir={sortDir} onClick={toggleSort} />
                <SortTh col="quota" label="Quota" current={sortCol} dir={sortDir} onClick={toggleSort} />
                <SortTh col="plan_id" label="Plan" current={sortCol} dir={sortDir} onClick={toggleSort} />
                <SortTh col="effective_from" label="From" current={sortCol} dir={sortDir} onClick={toggleSort} />
                <SortTh col="effective_to" label="To" current={sortCol} dir={sortDir} onClick={toggleSort} />
              </tr>
              <tr className="bg-zinc-50">
                <FilterTh value={filters["id"] || ""} onChange={v => setFilter("id", v)} />
                <FilterTh value={filters["name"] || ""} onChange={v => setFilter("name", v)} />
                <FilterTh value={filters["quota"] || ""} onChange={v => setFilter("quota", v)} />
                <FilterTh value={filters["plan_id"] || ""} onChange={v => setFilter("plan_id", v)} />
                <FilterTh value={filters["effective_from"] || ""} onChange={v => setFilter("effective_from", v)} />
                <FilterTh value={filters["effective_to"] || ""} onChange={v => setFilter("effective_to", v)} />
              </tr>
            </thead>
            <tbody>
              {paginated.map(p => (
                <tr key={p.id} className="border-b border-zinc-100 hover:bg-zinc-50">
                  <Td mono>{p.id}</Td><Td>{p.name}</Td><Td>{p.quota}</Td>
                  <Td mono>{p.plan_id}</Td><Td>{p.effective_from}</Td><Td>{p.effective_to}</Td>
                </tr>
              ))}
            </tbody>
          </table>
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-3 text-xs text-zinc-500">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="px-2 py-1 rounded hover:bg-zinc-100 disabled:opacity-30 cursor-pointer">← Prev</button>
              <span>Page {page} of {totalPages}</span>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="px-2 py-1 rounded hover:bg-zinc-100 disabled:opacity-30 cursor-pointer">Next →</button>
            </div>
          )}
          <div className="flex gap-2">
            <button onClick={() => setPayees([])} className="text-xs text-zinc-500 hover:text-zinc-700">Clear</button>
            <button onClick={() => document.getElementById("payee-reupload")?.click()} className="text-xs text-blue-600 hover:underline">Re-upload</button>
            <input id="payee-reupload" type="file" accept=".csv,.xlsx" className="hidden" onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f); }} />
          </div>
          <div className="flex justify-end">
            <button onClick={onNext} className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 cursor-pointer">
              {payees.length} payees loaded →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

import { useState } from "react";
import type { PayeeRow } from "./Pipeline";
import { parsePayees } from "../api";
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
  const [error, setError] = useState("");

  const { paginated, totalItems, page, totalPages, setPage, sortCol, sortDir, filters, toggleSort, setFilter, clearFilters } = useTableSort(payees, "id");

  async function handleFile(f: File) {
    if (loading) return;
    setLoading(true); setError("");
    try {
      const rows = await parsePayees(f);
      setPayees(rows); setPage(1); clearFilters();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not import the roster.");
    } finally { setLoading(false); }
  }

  return (
    <div className="max-w-3xl space-y-4">
      <h1 className="text-lg font-bold text-zinc-800">1. Payees</h1>
      <p className="text-sm text-zinc-500">Upload your payee roster (CSV or XLSX). Columns: id, name, quota, plan_id, effective_from.</p>
      {error && <p role="alert" className="p-3 rounded-lg bg-red-50 text-red-700 text-sm whitespace-pre-wrap">{error}</p>}
      {payees.length === 0 ? (
        <div className="border-2 border-dashed border-zinc-300 rounded-xl p-8 text-center space-y-3">
          <p className="text-sm text-zinc-500">Choose a CSV or XLSX file with columns: id, name, quota, plan_id, effective_from</p>
          <label className="inline-block px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 cursor-pointer">
            {loading ? "Loading..." : "Upload Payees"}
            <input aria-label="Upload Payees" disabled={loading} type="file" accept=".csv,.xlsx" className="hidden" onChange={e => { const f = e.target.files?.[0]; e.target.value = ""; if (f) handleFile(f); }} />
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
            <input id="payee-reupload" aria-label="Upload Payees" disabled={loading} type="file" accept=".csv,.xlsx" className="hidden" onChange={e => { const f = e.target.files?.[0]; e.target.value = ""; if (f) handleFile(f); }} />
          </div>
          <div className="flex justify-end">
            <button disabled={loading} onClick={onNext} className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 cursor-pointer">
              {payees.length} payees loaded →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

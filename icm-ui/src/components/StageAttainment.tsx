import { useMemo } from "react";
import type { CalculateResponse } from "../types";
import { Td } from "./Table";
import { useTableSort } from "./useTableSort";
import { FilterBar, FilterTh, SortTh } from "./SortableTable";

interface Props { result: CalculateResponse; onNext: () => void; onBack: () => void; }

type AttRow = Record<string, string> & { payee_id: string; period: string; bookings: string; quota: string; attainment_pct: string };

export default function StageAttainment({ result, onNext, onBack }: Props) {
  const raw = (result.attainment ?? []) as Record<string, unknown>[];

  const rows: AttRow[] = useMemo(() => raw.map(a => ({
    payee_id: String(a.payee_id ?? ""),
    period: String(a.period ?? ""),
    bookings: String(a.bookings ?? "0"),
    quota: String(a.quota ?? "0"),
    attainment_pct: a.attainment_pct != null ? (Number(a.attainment_pct) * 100).toFixed(1) : "N/A",
  })), [raw]);

  const { result: sorted, sortCol, sortDir, filters, toggleSort, setFilter, clearFilters } = useTableSort(rows, "payee_id");

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-bold text-zinc-800">6. Attainment</h1>
      <p className="text-sm text-zinc-500">Bookings vs quota per payee and window. This is what drives tier advancement and threshold gates.</p>

      <FilterBar total={rows.length} shown={sorted.length} filters={filters} onClear={clearFilters} />

      <table className="w-full text-xs border rounded-lg overflow-hidden">
        <thead className="bg-zinc-100">
          <tr>
            <SortTh col="payee_id" label="Payee" current={sortCol} dir={sortDir} onClick={toggleSort} />
            <SortTh col="period" label="Window" current={sortCol} dir={sortDir} onClick={toggleSort} />
            <SortTh col="bookings" label="Bookings" current={sortCol} dir={sortDir} onClick={toggleSort} />
            <SortTh col="quota" label="Quota" current={sortCol} dir={sortDir} onClick={toggleSort} />
            <SortTh col="attainment_pct" label="Attainment %" current={sortCol} dir={sortDir} onClick={toggleSort} />
          </tr>
          <tr className="bg-zinc-50">
            <FilterTh value={filters["payee_id"] || ""} onChange={v => setFilter("payee_id", v)} />
            <FilterTh value={filters["period"] || ""} onChange={v => setFilter("period", v)} />
            <FilterTh value={filters["bookings"] || ""} onChange={v => setFilter("bookings", v)} />
            <FilterTh value={filters["quota"] || ""} onChange={v => setFilter("quota", v)} />
            <FilterTh value={filters["attainment_pct"] || ""} onChange={v => setFilter("attainment_pct", v)} />
          </tr>
        </thead>
        <tbody>
          {sorted.map((a, i) => (
            <tr key={i} className="border-b border-zinc-100 hover:bg-zinc-50">
              <Td mono>{a.payee_id}</Td>
              <Td mono>{a.period}</Td>
              <Td>{a.bookings}</Td>
              <Td>{a.quota}</Td>
              <Td>{a.attainment_pct}{a.attainment_pct !== "N/A" ? "%" : ""}</Td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex justify-between">
        <button onClick={onBack} className="text-sm text-zinc-500 hover:text-zinc-700">← Back</button>
        <button onClick={onNext} className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 cursor-pointer">Continue →</button>
      </div>
    </div>
  );
}

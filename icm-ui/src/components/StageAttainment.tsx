import { useMemo } from "react";
import type { CalculateResponse } from "../types";
import { money, percent } from "../format";
import { Td } from "./Table";
import { useTableSort } from "./useTableSort";
import { FilterBar, FilterTh, SortTh } from "./SortableTable";
import StageShell from "./StageShell";
import { Stat } from "./ui";
import { cx } from "./ui/cx";

interface Props { result: CalculateResponse; onNext: () => void; onBack: () => void; }

type AttRow = Record<string, string> & { payee_id: string; period: string; bookings: string; quota: string; attainment_pct: string };

const COLUMNS = [
  { col: "payee_id", label: "Payee" },
  { col: "period", label: "Window" },
  { col: "bookings", label: "Bookings" },
  { col: "quota", label: "Quota" },
  { col: "attainment_pct", label: "Attainment" },
] as const;

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

  const measured = rows.filter(r => r.attainment_pct !== "N/A").map(r => parseFloat(r.attainment_pct));
  const average = measured.length ? measured.reduce((a, b) => a + b, 0) / measured.length : null;
  const atQuota = measured.filter(v => v >= 100).length;

  return (
    <StageShell
      n={6}
      group="Results"
      title="Attainment"
      description="Bookings against quota for each payee and window. This is what moves payees up tiers and through threshold gates."
      onBack={onBack}
      onNext={onNext}
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Payees measured" value={measured.length} hint={`${rows.length - measured.length} without a quota`} icon="users" />
        <Stat label="Average attainment" value={average === null ? "N/A" : percent(average / 100)} icon="target" />
        <Stat label="At or above quota" value={`${atQuota} of ${measured.length}`} icon="trendingUp" />
      </div>

      <FilterBar total={rows.length} shown={sorted.length} filters={filters} onClear={clearFilters} />

      <div className="table-wrap max-h-[60vh] rounded-xl border border-line">
        <table>
          <thead>
            <tr>
              {COLUMNS.map(c => (
                <SortTh key={c.col} col={c.col} label={c.label} current={sortCol} dir={sortDir} onClick={toggleSort}
                  align={c.col === "bookings" || c.col === "quota" ? "right" : undefined} />
              ))}
            </tr>
            <tr>
              {COLUMNS.map(c => (
                <FilterTh key={c.col} label={c.label} value={filters[c.col] || ""} onChange={v => setFilter(c.col, v)} />
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((a, i) => (
              <tr key={i}>
                <Td mono>{a.payee_id}</Td>
                <Td mono className="text-ink-2">{a.period}</Td>
                <Td num>{money(a.bookings)}</Td>
                <Td num className="text-ink-2">{money(a.quota)}</Td>
                <Td><AttainmentBar value={a.attainment_pct} /></Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </StageShell>
  );
}

function AttainmentBar({ value }: { value: string }) {
  if (value === "N/A") return <span className="text-ink-3">N/A</span>;
  const pct = parseFloat(value);
  const width = Math.min(pct, 150) / 1.5;
  return (
    <div className="flex items-center gap-3">
      <div className="relative h-2 w-32 overflow-hidden rounded-full bg-surface-3">
        <div className={cx("h-full rounded-full", pct >= 100 ? "bg-success" : "bg-accent")} style={{ width: `${width}%` }} />
        <div className="absolute inset-y-0 w-px bg-ink/40" style={{ left: `${100 / 1.5}%` }} />
      </div>
      <span className={cx("num w-16 text-right font-medium", pct >= 100 ? "text-success-ink" : "text-ink")}>{percent(pct / 100)}</span>
    </div>
  );
}

import type { ReactNode } from "react";

/**
 * Sortable table header cell. Click to toggle sort direction.
 */
export function SortTh({
  col, label, current, dir, onClick, className,
}: {
  col: string; label: string; current: string; dir: string; onClick: (c: string) => void; className?: string;
}) {
  const active = current === col;
  return (
    <th
      onClick={() => onClick(col)}
      className={`px-3 py-2 text-left font-medium text-zinc-500 whitespace-nowrap cursor-pointer hover:text-zinc-700 select-none ${className ?? ""}`}
    >
      {label}{active ? (dir === "asc" ? " ↑" : " ↓") : ""}
    </th>
  );
}

/**
 * Filter input below a sortable header. Type to filter that column.
 */
export function FilterTh({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <th className="px-1 py-0.5">
      <input
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder="…"
        className="w-full px-1.5 py-0.5 rounded border border-zinc-200 text-[10px] bg-white placeholder:text-zinc-300 focus:outline-none focus:border-blue-300"
      />
    </th>
  );
}

/**
 * Empty header cell for non-filterable columns.
 */
export function EmptyTh() {
  return <th className="px-1 py-0.5" />;
}

/**
 * Summary bar above a filtered table.
 */
export function FilterBar({ total, shown, filters, onClear, children }: {
  total: number; shown: number; filters: Record<string, unknown>; onClear: () => void; children?: ReactNode;
}) {
  const active = Object.keys(filters).length;
  return (
    <div className="flex items-center gap-3 text-xs">
      {active > 0 && <span className="text-zinc-400">{shown} of {total} shown</span>}
      {children}
      {active > 0 && (
        <button onClick={onClear} className="text-accent hover:underline cursor-pointer">
          Clear filters
        </button>
      )}
    </div>
  );
}

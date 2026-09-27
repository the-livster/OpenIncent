import type { ReactNode } from "react";
import { Icon } from "./ui";
import { cx } from "./ui/cx";

/**
 * Sortable table header cell. Click to toggle sort direction.
 */
export function SortTh<C extends string>({
  col, label, current, dir, onClick, className, align,
}: {
  col: C; label: string; current: string; dir: string; onClick: (c: C) => void; className?: string;
  align?: "right";
}) {
  const active = current === col;
  return (
    <th
      className={cx(align === "right" && "text-right", className)}
      aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : undefined}
    >
      <button
        type="button"
        onClick={() => onClick(col)}
        className={cx("inline-flex items-center gap-1 transition-colors hover:text-ink", active && "text-ink")}
      >
        {label}
        <Icon
          name={active ? (dir === "asc" ? "arrowUp" : "arrowDown") : "arrowUpDown"}
          size={12}
          className={active ? "" : "opacity-35"}
        />
      </button>
    </th>
  );
}

/**
 * Filter input below a sortable header. Type to filter that column.
 */
export function FilterTh({ value, onChange, label }: { value: string; onChange: (v: string) => void; label?: string }) {
  return (
    <th className="bg-surface px-2 py-1.5">
      <input
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder="Filter"
        aria-label={label ? `Filter ${label}` : "Filter column"}
        className="w-full min-w-16 rounded-md px-2 py-1 text-xs font-normal"
      />
    </th>
  );
}

/**
 * Empty header cell for non-filterable columns.
 */
export function EmptyTh() {
  return <th className="bg-surface py-1.5" />;
}

/**
 * Summary bar above a filtered table.
 */
export function FilterBar({ total, shown, filters, onClear, children }: {
  total: number; shown: number; filters: Record<string, unknown>; onClear: () => void; children?: ReactNode;
}) {
  const active = Object.keys(filters).length;
  if (!active && !children) return null;
  return (
    <div className="flex flex-wrap items-center gap-3 text-[12.5px]">
      {active > 0 && (
        <span className="inline-flex items-center gap-1.5 text-ink-2">
          <Icon name="filter" size={13} className="text-ink-3" />
          <span className="num">{shown} of {total} shown</span>
        </span>
      )}
      {children}
      {active > 0 && (
        <button onClick={onClear} className="font-medium text-accent-ink hover:underline">
          Clear filters
        </button>
      )}
    </div>
  );
}

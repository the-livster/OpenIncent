import { Fragment, useCallback, useMemo, useState } from "react";
import type { Commission, LedgerEntry, SortState } from "../types";

interface Props {
  commissions: Commission[];
  ledger: LedgerEntry[];
}

const COLUMNS: { key: keyof Commission; label: string; align?: "right" }[] = [
  { key: "transaction_id", label: "Transaction" },
  { key: "payee_id", label: "Payee" },
  { key: "period", label: "Period" },
  { key: "rule_id", label: "Rule" },
  { key: "base_amount", label: "Base", align: "right" },
  { key: "rate", label: "Rate", align: "right" },
  { key: "commission_amount", label: "Commission", align: "right" },
];

const PAGE_SIZE = 50;

export default function CommissionsTable({ commissions, ledger }: Props) {
  const [sort, setSort] = useState<SortState>({ column: "commission_amount", dir: "desc" });
  const [search, setSearch] = useState("");
  const [filterPayee, setFilterPayee] = useState("");
  const [filterRule, setFilterRule] = useState("");
  const [filterPeriod, setFilterPeriod] = useState("");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [page, setPage] = useState(0);

  // Derived filter options
  const payees = useMemo(() => [...new Set(commissions.map((c) => c.payee_id))].sort(), [commissions]);
  const rules = useMemo(() => [...new Set(commissions.map((c) => c.rule_id))].sort(), [commissions]);
  const periods = useMemo(() => [...new Set(commissions.map((c) => c.period))].sort(), [commissions]);

  // Ledger by txn
  const ledgerByTxn = useMemo(() => {
    const m: Record<string, LedgerEntry[]> = {};
    for (const e of ledger) {
      if (!m[e.transaction_id]) m[e.transaction_id] = [];
      m[e.transaction_id].push(e);
    }
    return m;
  }, [ledger]);

  // Filter + sort
  const filtered = useMemo(() => {
    let result = commissions;
    if (filterPayee) result = result.filter((c) => c.payee_id === filterPayee);
    if (filterRule) result = result.filter((c) => c.rule_id === filterRule);
    if (filterPeriod) result = result.filter((c) => c.period === filterPeriod);
    if (search) {
      const q = search.toLowerCase();
      result = result.filter((c) =>
        Object.values(c).some((v) => String(v).toLowerCase().includes(q))
      );
    }

    const col = sort.column as keyof Commission;
    const dir = sort.dir === "asc" ? 1 : -1;
    const isNum = col === "base_amount" || col === "rate" || col === "commission_amount";
    result = [...result].sort((a, b) => {
      const av = a[col];
      const bv = b[col];
      if (isNum) return (parseFloat(av) - parseFloat(bv)) * dir;
      return av.localeCompare(bv) * dir;
    });

    return result;
  }, [commissions, filterPayee, filterRule, filterPeriod, search, sort]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const paged = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const toggleSort = useCallback((col: string) => {
    setSort((prev) =>
      prev.column === col
        ? { column: col, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { column: col, dir: "desc" }
    );
    setPage(0);
  }, []);

  const toggle = useCallback((idx: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  }, []);

  const sortIcon = (col: string) => {
    if (sort.column !== col) return "↕";
    return sort.dir === "asc" ? "↑" : "↓";
  };

  return (
    <div className="space-y-3 animate-in">
      {/* Search + filters */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-ink2 text-sm">🔍</span>
          <input
            type="text"
            placeholder="Search commissions..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(0); }}
            className="
              w-full pl-9 pr-3 py-2 rounded-lg text-sm
              bg-soft border border-line
              text-ink placeholder:text-ink2
              focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/10
              transition-all
            "
          />
        </div>
        <FilterSelect label="Payee" value={filterPayee} options={payees} onChange={(v) => { setFilterPayee(v); setPage(0); }} />
        <FilterSelect label="Rule" value={filterRule} options={rules} onChange={(v) => { setFilterRule(v); setPage(0); }} />
        <FilterSelect label="Period" value={filterPeriod} options={periods} onChange={(v) => { setFilterPeriod(v); setPage(0); }} />
        <span className="text-xs text-ink2 ml-auto">
          {filtered.length} of {commissions.length} rows
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-xl glass">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line">
              <th className="w-8 px-3 py-3" />
              {COLUMNS.map((col) => (
                <th
                  key={col.key}
                  onClick={() => toggleSort(col.key)}
                  className={`
                    px-3 py-3 text-xs font-semibold uppercase tracking-wider
                    text-ink2 cursor-pointer select-none
                    hover:text-ink transition-colors
                    ${col.align === "right" ? "text-right" : "text-left"}
                  `}
                >
                  {col.label}{" "}
                  <span className="text-ink2">{sortIcon(col.key)}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {paged.map((c, i) => {
              const globalIdx = page * PAGE_SIZE + i;
              const open = expanded.has(globalIdx);
              const txnLedger = ledgerByTxn[c.transaction_id] ?? [];
              return (
                <Fragment key={globalIdx}>
                  <tr
                    onClick={() => toggle(globalIdx)}
                    className="
                      border-b border-line
                      hover:bg-soft cursor-pointer
                      transition-colors
                    "
                  >
                    <td className="px-3 py-2.5 text-ink2 text-xs">
                      {open ? "▾" : "▸"}
                    </td>
                    <td className="px-3 py-2.5 font-mono text-xs text-ink">{c.transaction_id}</td>
                    <td className="px-3 py-2.5 text-ink">{c.payee_id}</td>
                    <td className="px-3 py-2.5 text-ink2">{c.period}</td>
                    <td className="px-3 py-2.5">
                      <span className="px-1.5 py-0.5 rounded text-xs bg-accent/15 text-accent font-medium">
                        {c.rule_id}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-xs text-ink">${c.base_amount}</td>
                    <td className="px-3 py-2.5 text-right font-mono text-xs text-ink2">{c.rate}</td>
                    <td className="px-3 py-2.5 text-right font-mono text-xs font-semibold text-success">${c.commission_amount}</td>
                  </tr>
                  {open && (
                    <tr>
                      <td colSpan={8} className="px-4 py-3 bg-soft">
                        <div className="space-y-2 text-xs">
                          {c.notes && (
                            <div className="text-ink2">
                              <span className="text-ink2 font-medium">Notes:</span> {c.notes}
                            </div>
                          )}
                          {txnLedger.length > 0 && (
                            <div className="mt-2">
                              <div className="text-ink2 font-semibold mb-1.5">Audit Trail</div>
                              {txnLedger.map((e, j) => (
                                <div
                                  key={j}
                                  className="ml-2 pl-3 border-l-2 border-brand-500/30 mb-2"
                                >
                                  <div className="text-ink">{e.human_readable}</div>
                                  <div className="text-ink2 mt-0.5 flex flex-wrap gap-2">
                                    <EventBadge type={e.event_type} />
                                    <span>rule: {e.rule_id}</span>
                                    <span>{e.timestamp}</span>
                                  </div>
                                  {Object.keys(e.inputs).length > 0 && (
                                    <div className="mt-1 font-mono text-ink2">
                                      inputs: {JSON.stringify(e.inputs)}
                                    </div>
                                  )}
                                  {Object.keys(e.outputs).length > 0 && (
                                    <div className="font-mono text-ink2">
                                      outputs: {JSON.stringify(e.outputs)}
                                    </div>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {pageCount > 1 && (
        <div className="flex items-center justify-center gap-2">
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            className="px-2 py-1 rounded text-xs text-ink2 hover:bg-surface-200 disabled:opacity-30 cursor-pointer disabled:cursor-not-allowed transition-colors"
          >
            ← Prev
          </button>
          <span className="text-xs text-ink2">
            Page {page + 1} of {pageCount}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
            disabled={page >= pageCount - 1}
            className="px-2 py-1 rounded text-xs text-ink2 hover:bg-surface-200 disabled:opacity-30 cursor-pointer disabled:cursor-not-allowed transition-colors"
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

function FilterSelect({
  label, value, options, onChange,
}: { label: string; value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="
        px-2 py-2 rounded-lg text-xs
        bg-soft border border-line
        text-ink cursor-pointer
        focus:outline-none focus:border-accent
        transition-colors
      "
    >
      <option value="">All {label}s</option>
      {options.map((o) => (
        <option key={o} value={o}>{o}</option>
      ))}
    </select>
  );
}

function EventBadge({ type }: { type: string }) {
  const colors: Record<string, string> = {
    commission_computed: "bg-success/15 text-success",
    tier_crossed: "bg-warn/15 text-warn",
    rule_skipped: "bg-danger/15 text-danger",
    rule_evaluated: "bg-accent/15 text-accent",
  };
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${colors[type] ?? "bg-surface-200 text-ink2"}`}>
      {type}
    </span>
  );
}

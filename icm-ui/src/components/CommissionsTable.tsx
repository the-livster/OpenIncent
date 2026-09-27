import { Fragment, useCallback, useMemo, useState } from "react";
import type { Commission, LedgerEntry, SortState } from "../types";
import { useDebounce } from "./useDebounce";
import TracePanel from "./TracePanel";
import { Button, Card, CardHeader, EmptyState, Icon, Pagination } from "./ui";
import { cx } from "./ui/cx";

interface Props {
  commissions: Commission[];
  ledger: LedgerEntry[];
  /** Pins per-deal traces to one stored run; omitted for a live result. */
  calculationId?: string;
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

export default function CommissionsTable({ commissions, ledger, calculationId }: Props) {
  const [sort, setSort] = useState<SortState>({ column: "commission_amount", dir: "desc" });
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search, 200);
  const [filterPayee, setFilterPayee] = useState("");
  const [filterRule, setFilterRule] = useState("");
  const [filterPeriod, setFilterPeriod] = useState("");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [page, setPage] = useState(0);

  // Trace panel state
  const [traceTxn, setTraceTxn] = useState("");
  const [tracePayee, setTracePayee] = useState("");

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
    if (debouncedSearch) {
      const q = debouncedSearch.toLowerCase();
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
  }, [commissions, filterPayee, filterRule, filterPeriod, debouncedSearch, sort]);

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

  return (
    <Card className="overflow-hidden">
      <CardHeader
        title={`Commission lines (${commissions.length})`}
        description="Every line the engine paid. Open a row for its audit trail, or trace a deal through each rule."
      />
      {/* Search + filters */}
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-5 py-3">
        <div className="relative min-w-[220px] flex-1">
          <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-3" />
          <input
            type="search"
            aria-label="Search commissions"
            placeholder="Search commissions..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(0); }}
            className="w-full pl-9"
          />
        </div>
        <FilterSelect label="Payee" value={filterPayee} options={payees} onChange={(v) => { setFilterPayee(v); setPage(0); }} />
        <FilterSelect label="Rule" value={filterRule} options={rules} onChange={(v) => { setFilterRule(v); setPage(0); }} />
        <FilterSelect label="Period" value={filterPeriod} options={periods} onChange={(v) => { setFilterPeriod(v); setPage(0); }} />
        <span className="ml-auto text-[12.5px] text-ink-3 num">
          {filtered.length} of {commissions.length} rows
        </span>
      </div>

      {filtered.length === 0 ? (
        <EmptyState icon="search" title="No commission lines match" compact />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="w-8" />
                {COLUMNS.map((col) => {
                  const active = sort.column === col.key;
                  return (
                    <th key={col.key} className={col.align === "right" ? "text-right" : undefined}
                      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}>
                      <button type="button" onClick={() => toggleSort(col.key)}
                        className={cx("inline-flex items-center gap-1 hover:text-ink", active && "text-ink")}>
                        {col.label}
                        <Icon name={active ? (sort.dir === "asc" ? "arrowUp" : "arrowDown") : "arrowUpDown"} size={12}
                          className={active ? "" : "opacity-35"} />
                      </button>
                    </th>
                  );
                })}
                <th className="w-0" />
              </tr>
            </thead>
            <tbody>
              {paged.map((c, i) => {
                const globalIdx = page * PAGE_SIZE + i;
                const open = expanded.has(globalIdx);
                const txnLedger = ledgerByTxn[c.transaction_id] ?? [];
                const negative = parseFloat(c.commission_amount) < 0;
                return (
                  <Fragment key={globalIdx}>
                    <tr onClick={() => toggle(globalIdx)} className="cursor-pointer">
                      <td className="text-ink-3">
                        <Icon name="chevronRight" size={14} className={cx("transition-transform", open && "rotate-90")} />
                      </td>
                      <td className="font-mono text-[12.5px]">{c.transaction_id}</td>
                      <td className="font-mono text-[12.5px]">{c.payee_id}</td>
                      <td className="font-mono text-[12.5px] text-ink-2">{c.period}</td>
                      <td><span className="badge badge-accent">{c.rule_id}</span></td>
                      <td className="num text-right text-ink-2">{c.base_amount}</td>
                      <td className="num text-right text-ink-2">{c.rate}</td>
                      <td className={cx("num text-right font-semibold", negative ? "text-danger-ink" : "text-ink")}>
                        {c.commission_amount}
                      </td>
                      <td className="text-right">
                        {c.transaction_id !== "*" && (
                          <Button
                            size="sm"
                            variant="ghost"
                            icon="search"
                            aria-label={`Trace ${c.transaction_id}`}
                            title="Trace this deal through every rule"
                            onClick={(e) => { e.stopPropagation(); setTraceTxn(c.transaction_id); setTracePayee(c.payee_id); }}
                          />
                        )}
                      </td>
                    </tr>
                    {open && (
                      <tr>
                        <td colSpan={9} className="bg-surface-2/60 px-5 py-4">
                          <div className="space-y-3 text-[12.5px]">
                            {c.notes && (
                              <p className="text-ink-2"><span className="font-medium text-ink">Notes:</span> {c.notes}</p>
                            )}
                            {txnLedger.length > 0 ? (
                              <div>
                                <p className="eyebrow mb-2">Audit trail</p>
                                <ol className="space-y-2">
                                  {txnLedger.map((e, j) => (
                                    <li key={j} className="border-l-2 border-accent/40 pl-3">
                                      <div className="text-ink">{e.human_readable}</div>
                                      <div className="mt-1 flex flex-wrap items-center gap-2 text-ink-3">
                                        <EventBadge type={e.event_type} />
                                        <span>rule {e.rule_id}</span>
                                        <span>{e.timestamp}</span>
                                      </div>
                                      {Object.keys(e.inputs).length > 0 && (
                                        <div className="mt-1 font-mono text-[11.5px] text-ink-2">inputs: {JSON.stringify(e.inputs)}</div>
                                      )}
                                      {Object.keys(e.outputs).length > 0 && (
                                        <div className="font-mono text-[11.5px] text-ink-2">outputs: {JSON.stringify(e.outputs)}</div>
                                      )}
                                    </li>
                                  ))}
                                </ol>
                              </div>
                            ) : !c.notes && <p className="text-ink-3">No audit entries for this line.</p>}
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
      )}

      <Pagination page={page + 1} totalPages={pageCount} onPage={p => setPage(p - 1)} className="border-t border-line" />

      {traceTxn && tracePayee && (
        <TracePanel
          transactionId={traceTxn}
          payeeId={tracePayee}
          calculationId={calculationId}
          onClose={() => { setTraceTxn(""); setTracePayee(""); }}
        />
      )}
    </Card>
  );
}

function FilterSelect({
  label, value, options, onChange,
}: { label: string; value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <select aria-label={`Filter by ${label.toLowerCase()}`} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">All {label.toLowerCase()}s</option>
      {options.map((o) => (
        <option key={o} value={o}>{o}</option>
      ))}
    </select>
  );
}

const EVENT_TONES: Record<string, string> = {
  commission_computed: "badge-success",
  tier_crossed: "badge-warning",
  rule_skipped: "badge-danger",
  rule_evaluated: "badge-accent",
};

function EventBadge({ type }: { type: string }) {
  return <span className={cx("badge", EVENT_TONES[type])}>{type}</span>;
}

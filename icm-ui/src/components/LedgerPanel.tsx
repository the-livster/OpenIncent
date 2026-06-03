import { useMemo, useState } from "react";
import type { LedgerEntry } from "../types";

interface Props {
  ledger: LedgerEntry[];
}

export default function LedgerPanel({ ledger }: Props) {
  const [filterEvent, setFilterEvent] = useState("");
  const [search, setSearch] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const eventTypes = useMemo(
    () => [...new Set(ledger.map((e) => e.event_type))].sort(),
    [ledger]
  );

  const filtered = useMemo(() => {
    let result = ledger;
    if (filterEvent) result = result.filter((e) => e.event_type === filterEvent);
    if (search) {
      const q = search.toLowerCase();
      result = result.filter(
        (e) =>
          e.human_readable.toLowerCase().includes(q) ||
          e.transaction_id.toLowerCase().includes(q) ||
          e.payee_id.toLowerCase().includes(q)
      );
    }
    return result;
  }, [ledger, filterEvent, search]);

  // Group by transaction
  const grouped = useMemo(() => {
    const m = new Map<string, LedgerEntry[]>();
    for (const e of filtered) {
      if (!m.has(e.transaction_id)) m.set(e.transaction_id, []);
      m.get(e.transaction_id)!.push(e);
    }
    return m;
  }, [filtered]);

  const toggleGroup = (txnId: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(txnId)) next.delete(txnId);
      else next.add(txnId);
      return next;
    });
  };

  const eventColor: Record<string, string> = {
    commission_computed: "border-l-success bg-success/5",
    tier_crossed: "border-l-warn bg-warn/5",
    rule_skipped: "border-l-danger bg-danger/5",
    rule_evaluated: "border-l-brand-400 bg-accent/5",
  };

  return (
    <div className="space-y-3 animate-in">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-ink2 text-sm">🔍</span>
          <input
            type="text"
            placeholder="Search ledger..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="
              w-full pl-9 pr-3 py-2 rounded-lg text-sm
              bg-soft border border-line
              text-ink placeholder:text-ink2
              focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/10
              transition-all
            "
          />
        </div>
        <select
          value={filterEvent}
          onChange={(e) => setFilterEvent(e.target.value)}
          className="
            px-2 py-2 rounded-lg text-xs
            bg-soft border border-line
            text-ink cursor-pointer
            focus:outline-none focus:border-accent
            transition-colors
          "
        >
          <option value="">All Events</option>
          {eventTypes.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <span className="text-xs text-ink2">{filtered.length} entries</span>
      </div>

      <div className="space-y-2">
        {[...grouped.entries()].map(([txnId, entries]) => {
          const isCollapsed = collapsed.has(txnId);
          return (
            <div key={txnId} className="card overflow-hidden">
              <button
                onClick={() => toggleGroup(txnId)}
                className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-surface-200/30 transition-colors cursor-pointer"
              >
                <span className="text-ink2 text-xs">{isCollapsed ? "▸" : "▾"}</span>
                <span className="font-mono text-xs text-ink">{txnId}</span>
                <span className="text-xs text-ink2 ml-auto">{entries.length} entries</span>
              </button>
              {!isCollapsed && (
                <div className="px-4 pb-3 space-y-1.5">
                  {entries.map((e, i) => (
                    <div
                      key={i}
                      className={`border-l-3 rounded-r-lg px-3 py-2 text-xs ${eventColor[e.event_type] ?? "border-l-surface-400 bg-soft/50"}`}
                    >
                      <div className="text-ink">{e.human_readable}</div>
                      <div className="flex flex-wrap gap-2 mt-1 text-ink2">
                        <span className="font-medium">{e.event_type}</span>
                        <span>rule: {e.rule_id}</span>
                        {e.payee_id !== "*" && <span>payee: {e.payee_id}</span>}
                      </div>
                      {Object.keys(e.inputs).length > 0 && (
                        <details className="mt-1">
                          <summary className="cursor-pointer text-ink2 hover:text-ink2 transition-colors">
                            inputs / outputs
                          </summary>
                          <pre className="font-mono text-[11px] text-ink2 mt-1 overflow-x-auto">
                            {JSON.stringify({ inputs: e.inputs, outputs: e.outputs }, null, 2)}
                          </pre>
                        </details>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {grouped.size === 0 && (
        <div className="text-center text-ink2 text-sm py-8">
          No ledger entries match your filters.
        </div>
      )}
    </div>
  );
}

import { useEffect, useMemo, useState } from "react";
import { listPlans } from "../api";
import type { SavedPlan } from "../types";
import type { PayeeRow } from "./Pipeline";
import { Th, Td } from "./Table";

interface Props {
  payees: PayeeRow[];
  setPayees: (p: PayeeRow[]) => void;
  plans: SavedPlan[];
  setPlans: (p: SavedPlan[]) => void;
  onNext: () => void;
  onBack: () => void;
}

type SortCol = "id" | "name" | "plan_id";

type QuickFilter = null | "unassigned" | "wrong";

export default function StageEligibility({ payees, setPayees, plans, setPlans, onNext, onBack }: Props) {
  const [sortCol, setSortCol] = useState<SortCol>("id");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [colFilters, setColFilters] = useState<Record<string, string>>({});
  const [quickFilter, setQuickFilter] = useState<QuickFilter>(null);

  useEffect(() => {
    listPlans().then(setPlans).catch(() => {});
  }, [setPlans]);

  function update(payeeId: string, field: keyof PayeeRow, value: string) {
    setPayees(payees.map(p => p.id === payeeId ? { ...p, [field]: value } : p));
  }

  const toggleSort = (col: SortCol) => {
    if (sortCol === col) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortCol(col); setSortDir("asc"); }
  };

  const setFilter = (col: string, value: string) => {
    setQuickFilter(null);
    setColFilters(prev => {
      const next = { ...prev };
      if (value) next[col] = value.toLowerCase();
      else delete next[col];
      return next;
    });
  };

  const planIds = new Set(plans.map(p => p.id));

  const sorted = useMemo(() => {
    let list = [...payees];
    // Quick-filters (mutually exclusive with column filters)
    if (quickFilter === "unassigned") {
      list = list.filter(p => !p.plan_id);
    } else if (quickFilter === "wrong") {
      list = list.filter(p => p.plan_id && !planIds.has(p.plan_id));
    } else {
      // Column filters (AND logic)
      for (const [col, val] of Object.entries(colFilters)) {
        list = list.filter(p => String(p[col as keyof PayeeRow] ?? "").toLowerCase().includes(val));
      }
    }
    // Sort
    list.sort((a, b) => {
      const av = (a[sortCol] ?? "").toLowerCase();
      const bv = (b[sortCol] ?? "").toLowerCase();
      return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
    });
    return list;
  }, [payees, sortCol, sortDir, colFilters, quickFilter, planIds]);

  const missingPlans = payees.filter(p => p.plan_id && !planIds.has(p.plan_id));
  const unassigned = payees.filter(p => !p.plan_id);

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-bold text-zinc-800">4. Eligibility</h1>
      <p className="text-sm text-zinc-500">Assign each payee to a plan, set effective dates, and configure manager/team hierarchy.</p>

      {missingPlans.length > 0 && (
        <div className="px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-red-800 text-xs select-text">
          ⚠ {missingPlans.length} payee(s) reference a plan_id not in the saved library:{" "}
          {[...new Set(missingPlans.map(p => p.plan_id))].join(", ")}.
          Save these plans via the Plans tab or change the assignment below.
        </div>
      )}
      {unassigned.length > 0 && (
        <div className="px-3 py-2 rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs select-text">
          ⚠ {unassigned.length} payee(s) have no plan assigned.
        </div>
      )}

      {/* Quick-filter links */}
      <div className="flex items-center gap-3 text-xs">
        <span className="text-zinc-400">{sorted.length} of {payees.length} shown</span>
        {unassigned.length > 0 && (
          <button onClick={() => { setQuickFilter(quickFilter === "unassigned" ? null : "unassigned"); setColFilters({}); }}
            className={`${quickFilter === "unassigned" ? "text-amber-700 font-semibold" : "text-amber-600"} hover:underline cursor-pointer`}>
            Show unassigned ({unassigned.length})
          </button>
        )}
        {missingPlans.length > 0 && (
          <button onClick={() => { setQuickFilter(quickFilter === "wrong" ? null : "wrong"); setColFilters({}); }}
            className={`${quickFilter === "wrong" ? "text-red-700 font-semibold" : "text-red-600"} hover:underline cursor-pointer`}>
            {missingPlans.length} wrong plans
          </button>
        )}
        {(quickFilter || Object.keys(colFilters).length > 0) && (
          <button onClick={() => { setColFilters({}); setQuickFilter(null); }} className="text-accent hover:underline cursor-pointer">
            Clear filters
          </button>
        )}
      </div>

      <div className="overflow-auto max-h-[60vh] border rounded-lg">
        <table className="w-full text-xs">
          <thead className="bg-zinc-100 sticky top-0">
            <tr>
              <SortTh col="id" label="ID" current={sortCol} dir={sortDir} onClick={toggleSort} />
              <SortTh col="name" label="Name" current={sortCol} dir={sortDir} onClick={toggleSort} />
              <SortTh col="plan_id" label="Plan" current={sortCol} dir={sortDir} onClick={toggleSort} />
              <Th>From</Th><Th>To</Th><Th>Manager</Th><Th>Mgr Override</Th><Th>Team</Th>
            </tr>
            {/* Filter row */}
            <tr className="bg-zinc-50">
              <FilterTh col="id" value={colFilters["id"] || ""} onChange={v => setFilter("id", v)} />
              <FilterTh col="name" value={colFilters["name"] || ""} onChange={v => setFilter("name", v)} />
              <FilterTh col="plan_id" value={colFilters["plan_id"] || ""} onChange={v => setFilter("plan_id", v)} />
              <th className="px-1 py-0.5"></th><th className="px-1 py-0.5"></th>
              <th className="px-1 py-0.5"></th><th className="px-1 py-0.5"></th><th className="px-1 py-0.5"></th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(p => (
              <tr key={p.id} className="border-b border-zinc-100 hover:bg-zinc-50">
                <Td mono>{p.id}</Td><Td>{p.name}</Td>
                <Td>
                  <select value={p.plan_id} onChange={e => update(p.id, "plan_id", e.target.value)}
                    className={`w-28 px-1 py-0.5 rounded border text-xs bg-white ${p.plan_id && !planIds.has(p.plan_id) ? "border-red-300 bg-red-50" : "border-zinc-200"}`}>
                    <option value="">—</option>
                    {plans.map(pl => <option key={pl.id} value={pl.id}>{pl.name || pl.id}</option>)}
                  </select>
                </Td>
                <Td><input value={p.effective_from} onChange={e => update(p.id, "effective_from", e.target.value)} placeholder="YYYY-MM-DD" className="w-24 px-1 py-0.5 rounded border border-zinc-200 text-xs bg-white" /></Td>
                <Td><input value={p.effective_to} onChange={e => update(p.id, "effective_to", e.target.value)} placeholder="optional" className="w-24 px-1 py-0.5 rounded border border-zinc-200 text-xs bg-white" /></Td>
                <Td><input value={p.manager_id} onChange={e => update(p.id, "manager_id", e.target.value)} placeholder="optional" className="w-20 px-1 py-0.5 rounded border border-zinc-200 text-xs bg-white" /></Td>
                <Td><input value={p.manager_override} onChange={e => update(p.id, "manager_override", e.target.value)} placeholder="e.g. 0.05" className="w-20 px-1 py-0.5 rounded border border-zinc-200 text-xs bg-white" /></Td>
                <Td><input value={p.team_id} onChange={e => update(p.id, "team_id", e.target.value)} placeholder="optional" className="w-20 px-1 py-0.5 rounded border border-zinc-200 text-xs bg-white" /></Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex justify-between">
        <button onClick={onBack} className="text-sm text-zinc-500 hover:text-zinc-700">← Back</button>
        <button onClick={onNext} className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 cursor-pointer">
          Continue →
        </button>
      </div>
    </div>
  );
}

function SortTh({ col, label, current, dir, onClick }: {
  col: SortCol; label: string; current: SortCol; dir: string; onClick: (c: SortCol) => void;
}) {
  const active = current === col;
  return (
    <th
      onClick={() => onClick(col)}
      className="px-3 py-2 text-left font-medium text-zinc-500 whitespace-nowrap cursor-pointer hover:text-zinc-700 select-none"
    >
      {label}{active ? (dir === "asc" ? " ↑" : " ↓") : ""}
    </th>
  );
}

function FilterTh({ value, onChange }: { col: string; value: string; onChange: (v: string) => void }) {
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

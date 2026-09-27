import { useEffect, useMemo, useState } from "react";
import { listPlans } from "../api";
import type { SavedPlan } from "../types";
import type { PayeeRow } from "./Pipeline";
import { Th, Td } from "./Table";
import { EmptyTh, FilterTh, SortTh } from "./SortableTable";
import StageShell from "./StageShell";
import { Badge, Callout } from "./ui";
import { cx } from "./ui/cx";

interface Props {
  payees: PayeeRow[];
  setPayees: (p: PayeeRow[]) => void;
  plans: SavedPlan[];
  setPlans: (p: SavedPlan[]) => void;
  onNext: () => void;
  onBack: () => void;
  sample?: boolean;
}

type SortCol = "id" | "name" | "plan_id";

type QuickFilter = null | "unassigned" | "wrong";

export default function StageEligibility({ payees, setPayees, plans, setPlans, onNext, onBack, sample }: Props) {
  const [sortCol, setSortCol] = useState<SortCol>("id");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [colFilters, setColFilters] = useState<Record<string, string>>({});
  const [quickFilter, setQuickFilter] = useState<QuickFilter>(null);

  useEffect(() => {
    if (!sample) listPlans().then(setPlans).catch(() => {});
  }, [setPlans, sample]);

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

  const planIds = useMemo(() => new Set(plans.map(p => p.id)), [plans]);

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
  const filtering = quickFilter !== null || Object.keys(colFilters).length > 0;

  return (
    <StageShell
      n={4}
      group="Setup"
      title="Eligibility"
      description="Assign each payee to a plan, set effective dates, and configure the manager and team hierarchy."
      actions={payees.length > 0 && missingPlans.length === 0 && unassigned.length === 0 && (
        <Badge tone="success" icon="check">Everyone has a plan</Badge>
      )}
      onBack={onBack}
      onNext={onNext}
    >
      {missingPlans.length > 0 && (
        <Callout tone="danger">
          {missingPlans.length} payee(s) reference a plan_id not in the saved library:{" "}
          <span className="font-mono">{[...new Set(missingPlans.map(p => p.plan_id))].join(", ")}</span>.
          Save these plans in the Plans step, or change the assignment below.
        </Callout>
      )}
      {unassigned.length > 0 && (
        <Callout tone="warning">{unassigned.length} payee(s) have no plan assigned.</Callout>
      )}

      {payees.length === 0 ? (
        <p className="text-[13.5px] text-ink-2">Upload a roster in step 1 to assign plans.</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
            <span className="num text-ink-2">{sorted.length} of {payees.length} shown</span>
            {unassigned.length > 0 && (
              <QuickChip active={quickFilter === "unassigned"} tone="warning"
                onClick={() => { setQuickFilter(quickFilter === "unassigned" ? null : "unassigned"); setColFilters({}); }}>
                Show unassigned ({unassigned.length})
              </QuickChip>
            )}
            {missingPlans.length > 0 && (
              <QuickChip active={quickFilter === "wrong"} tone="danger"
                onClick={() => { setQuickFilter(quickFilter === "wrong" ? null : "wrong"); setColFilters({}); }}>
                {missingPlans.length} wrong plans
              </QuickChip>
            )}
            {filtering && (
              <button onClick={() => { setColFilters({}); setQuickFilter(null); }}
                className="font-medium text-accent-ink hover:underline">
                Clear filters
              </button>
            )}
          </div>

          <div className="table-wrap max-h-[60vh] rounded-xl border border-line">
            <table>
              <thead>
                <tr>
                  <SortTh col="id" label="ID" current={sortCol} dir={sortDir} onClick={toggleSort} />
                  <SortTh col="name" label="Name" current={sortCol} dir={sortDir} onClick={toggleSort} />
                  <SortTh col="plan_id" label="Plan" current={sortCol} dir={sortDir} onClick={toggleSort} />
                  <Th>From</Th><Th>To</Th><Th>Manager</Th><Th>Override</Th><Th>Team</Th>
                </tr>
                <tr>
                  <FilterTh label="ID" value={colFilters["id"] || ""} onChange={v => setFilter("id", v)} />
                  <FilterTh label="Name" value={colFilters["name"] || ""} onChange={v => setFilter("name", v)} />
                  <FilterTh label="Plan" value={colFilters["plan_id"] || ""} onChange={v => setFilter("plan_id", v)} />
                  <EmptyTh /><EmptyTh /><EmptyTh /><EmptyTh /><EmptyTh />
                </tr>
              </thead>
              <tbody>
                {sorted.map(p => {
                  const wrong = !!p.plan_id && !planIds.has(p.plan_id);
                  return (
                    <tr key={p.id}>
                      <Td mono>{p.id}</Td>
                      <Td className="font-medium">{p.name}</Td>
                      <Td>
                        <select aria-label={`Plan for ${p.name}`} value={p.plan_id} onChange={e => update(p.id, "plan_id", e.target.value)}
                          className={cx("w-40", wrong && "border-danger bg-danger-soft", !p.plan_id && "border-warning")}>
                          <option value="">No plan</option>
                          {wrong && <option value={p.plan_id}>{p.plan_id} (missing)</option>}
                          {plans.map(pl => <option key={pl.id} value={pl.id}>{pl.name || pl.id}</option>)}
                        </select>
                      </Td>
                      <Td><input aria-label={`Effective from for ${p.name}`} value={p.effective_from} onChange={e => update(p.id, "effective_from", e.target.value)} placeholder="YYYY-MM-DD" className="w-32" /></Td>
                      <Td><input aria-label={`Effective to for ${p.name}`} value={p.effective_to} onChange={e => update(p.id, "effective_to", e.target.value)} placeholder="Optional" className="w-32" /></Td>
                      <Td><input aria-label={`Manager for ${p.name}`} value={p.manager_id} onChange={e => update(p.id, "manager_id", e.target.value)} placeholder="Optional" className="w-24" /></Td>
                      <Td><input aria-label={`Manager override for ${p.name}`} value={p.manager_override} onChange={e => update(p.id, "manager_override", e.target.value)} placeholder="e.g. 0.05" className="w-24" /></Td>
                      <Td><input aria-label={`Team for ${p.name}`} value={p.team_id} onChange={e => update(p.id, "team_id", e.target.value)} placeholder="Optional" className="w-24" /></Td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </StageShell>
  );
}

function QuickChip({ active, tone, onClick, children }: {
  active: boolean; tone: "warning" | "danger"; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={cx(
        "badge transition-colors",
        tone === "warning" ? "badge-warning" : "badge-danger",
        active ? "ring-2 ring-current/30" : "opacity-85 hover:opacity-100",
      )}
    >
      {children}
    </button>
  );
}

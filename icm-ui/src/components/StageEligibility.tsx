import { useEffect } from "react";
import { listPlans } from "../api";
import type { SavedPlan } from "../types";
import type { PayeeRow } from "./Pipeline";

interface Props {
  payees: PayeeRow[];
  setPayees: (p: PayeeRow[]) => void;
  plans: SavedPlan[];
  setPlans: (p: SavedPlan[]) => void;
  onNext: () => void;
  onBack: () => void;
}

export default function StageEligibility({ payees, setPayees, plans, setPlans, onNext, onBack }: Props) {
  useEffect(() => {
    listPlans().then(setPlans).catch(() => {});
  }, [setPlans]);

  function update(idx: number, field: keyof PayeeRow, value: string) {
    const next = [...payees];
    next[idx] = { ...next[idx], [field]: value };
    setPayees(next);
  }

  const planIds = new Set(plans.map(p => p.id));
  const missingPlans = payees.filter(p => p.plan_id && !planIds.has(p.plan_id));
  const unassigned = payees.filter(p => !p.plan_id);

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-bold text-zinc-800">3. Eligibility</h1>
      <p className="text-sm text-zinc-500">Assign each payee to a plan, set effective dates, and configure manager/team hierarchy.</p>

      {missingPlans.length > 0 && (
        <div className="px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-red-800 text-xs">
          ⚠ {missingPlans.length} payee(s) reference a plan_id not in the saved library:{" "}
          {[...new Set(missingPlans.map(p => p.plan_id))].join(", ")}.
          Save these plans via the Plans tab or change the assignment below.
        </div>
      )}
      {unassigned.length > 0 && (
        <div className="px-3 py-2 rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs">
          ⚠ {unassigned.length} payee(s) have no plan assigned.
        </div>
      )}

      <div className="overflow-auto max-h-[60vh] border rounded-lg">
        <table className="w-full text-xs">
          <thead className="bg-zinc-100 sticky top-0">
            <tr>
              <Th>ID</Th><Th>Name</Th><Th>Plan</Th><Th>From</Th><Th>To</Th><Th>Manager</Th><Th>Mgr Override</Th><Th>Team</Th>
            </tr>
          </thead>
          <tbody>
            {payees.map((p, i) => (
              <tr key={p.id || i} className="border-b border-zinc-100 hover:bg-zinc-50">
                <Td mono>{p.id}</Td><Td>{p.name}</Td>
                <Td>
                  <select value={p.plan_id} onChange={e => update(i, "plan_id", e.target.value)}
                    className={`w-28 px-1 py-0.5 rounded border text-xs bg-white ${p.plan_id && !planIds.has(p.plan_id) ? "border-red-300 bg-red-50" : "border-zinc-200"}`}>
                    <option value="">—</option>
                    {plans.map(pl => <option key={pl.id} value={pl.id}>{pl.name || pl.id}</option>)}
                  </select>
                </Td>
                <Td><input value={p.effective_from} onChange={e => update(i, "effective_from", e.target.value)} placeholder="YYYY-MM-DD" className="w-24 px-1 py-0.5 rounded border border-zinc-200 text-xs bg-white" /></Td>
                <Td><input value={p.effective_to} onChange={e => update(i, "effective_to", e.target.value)} placeholder="optional" className="w-24 px-1 py-0.5 rounded border border-zinc-200 text-xs bg-white" /></Td>
                <Td><input value={p.manager_id} onChange={e => update(i, "manager_id", e.target.value)} placeholder="optional" className="w-20 px-1 py-0.5 rounded border border-zinc-200 text-xs bg-white" /></Td>
                <Td><input value={p.manager_override} onChange={e => update(i, "manager_override", e.target.value)} placeholder="e.g. 0.05" className="w-20 px-1 py-0.5 rounded border border-zinc-200 text-xs bg-white" /></Td>
                <Td><input value={p.team_id} onChange={e => update(i, "team_id", e.target.value)} placeholder="optional" className="w-20 px-1 py-0.5 rounded border border-zinc-200 text-xs bg-white" /></Td>
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

function Th({ children }: { children: React.ReactNode }) {
  return <th className="px-3 py-2 text-left font-medium text-zinc-500 whitespace-nowrap">{children}</th>;
}
function Td({ children, mono }: { children: React.ReactNode; mono?: boolean }) {
  return <td className={`px-3 py-1.5 whitespace-nowrap ${mono ? "font-mono text-zinc-600" : "text-zinc-700"}`}>{children}</td>;
}

import { useEffect, useState } from "react";
import {
  listPlans,
  listPayees,
  listCalculations,
  listTransactions,
  listPeriods,
  listMappings,
  type SavedPlan,
  type CalculationRow,
  type TransactionRow,
  type PeriodStatusRow,
} from "../api";

type Section = {
  id: string;
  label: string;
  count: number;
  open: boolean;
};

export default function DataModel() {
  const [sections, setSections] = useState<Section[]>([]);
  const [plans, setPlans] = useState<SavedPlan[]>([]);
  const [payees, setPayees] = useState<Record<string, unknown>[]>([]);
  const [calculations, setCalculations] = useState<CalculationRow[]>([]);
  const [transactions, setTransactions] = useState<TransactionRow[]>([]);
  const [periods, setPeriods] = useState<Record<string, PeriodStatusRow[]>>({});
  const [mappings, setMappings] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    loadAll();
  }, []);

  async function loadAll() {
    setLoading(true);
    setError("");
    try {
      const [p, pee, calcs, txns, maps] = await Promise.all([
        listPlans(),
        listPayees(),
        listCalculations(),
        listTransactions(),
        listMappings(),
      ]);
      setPlans(p);
      setPayees(pee);
      setCalculations(calcs);
      setTransactions(txns);
      setMappings(maps);

      // Load period status per plan
      const perMap: Record<string, PeriodStatusRow[]> = {};
      for (const plan of p) {
        try {
          perMap[plan.id] = await listPeriods(plan.id);
        } catch {
          perMap[plan.id] = [];
        }
      }
      setPeriods(perMap);

      setSections([
        { id: "plans", label: "Plans", count: p.length, open: true },
        { id: "payees", label: "Payees", count: pee.length, open: false },
        { id: "transactions", label: "Transactions", count: txns.length, open: false },
        { id: "calculations", label: "Calculations", count: calcs.length, open: false },
        { id: "periods", label: "Period Status", count: Object.values(perMap).flat().length, open: false },
        { id: "mappings", label: "Column Mappings", count: maps.length, open: false },
      ]);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }

  function toggle(id: string) {
    setSections(prev => prev.map(s => s.id === id ? { ...s, open: !s.open } : s));
  }

  if (loading) return <div className="p-6 text-zinc-500">Loading data model...</div>;
  if (error) return <div className="p-6 text-red-500">{error}</div>;

  const sectionOpen = (id: string) => sections.find(s => s.id === id)?.open ?? false;

  return (
    <div className="p-4 max-w-screen-xl mx-auto space-y-3">
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-lg font-bold text-zinc-800">Data Model</h1>
        <button onClick={loadAll} className="text-xs text-blue-600 hover:underline">Refresh</button>
      </div>

      {/* Plans */}
      <Section label="Plans" count={plans.length} open={sectionOpen("plans")} onToggle={() => toggle("plans")}>
        {plans.length === 0 ? (
          <Empty />
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-zinc-500 border-b">
                <Th>ID</Th><Th>Name</Th><Th>Updated</Th>
              </tr>
            </thead>
            <tbody>
              {plans.map(p => (
                <tr key={p.id} className="border-b border-zinc-100 hover:bg-zinc-50">
                  <Td mono>{p.id}</Td>
                  <Td>{p.name}</Td>
                  <Td>{p.updated_at?.slice(0, 16)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {/* Payees */}
      <Section label="Payees" count={payees.length} open={sectionOpen("payees")} onToggle={() => toggle("payees")}>
        {payees.length === 0 ? <Empty /> : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-zinc-500 border-b">
                <Th>ID</Th><Th>Name</Th><Th>Plan</Th><Th>Quota</Th><Th>Team</Th><Th>Manager</Th>
              </tr>
            </thead>
            <tbody>
              {payees.map((p: Record<string, unknown>) => (
                <tr key={String(p.id)} className="border-b border-zinc-100 hover:bg-zinc-50">
                  <Td mono>{String(p.id)}</Td>
                  <Td>{String(p.name)}</Td>
                  <Td mono>{String(p.plan_id ?? "")}</Td>
                  <Td>{String(p.quota ?? "")}</Td>
                  <Td mono>{String(p.team_id ?? "")}</Td>
                  <Td mono>{String(p.manager_id ?? "")}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {/* Transactions */}
      <Section label="Transactions" count={transactions.length} open={sectionOpen("transactions")} onToggle={() => toggle("transactions")}>
        {transactions.length === 0 ? <Empty /> : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-zinc-500 border-b">
                <Th>ID</Th><Th>Payee</Th><Th>Deal</Th><Th>Period</Th><Th>Amount</Th><Th>Product</Th>
              </tr>
            </thead>
            <tbody>
              {transactions.slice(0, 200).map(t => (
                <tr key={t.id} className="border-b border-zinc-100 hover:bg-zinc-50">
                  <Td mono>{t.id}</Td>
                  <Td mono>{t.payee_id}</Td>
                  <Td mono>{t.deal_id}</Td>
                  <Td mono>{t.period}</Td>
                  <Td>{t.amount}</Td>
                  <Td>{t.product ?? ""}</Td>
                </tr>
              ))}
              {transactions.length > 200 && (
                <tr><td colSpan={6} className="p-2 text-zinc-400 text-center">+ {transactions.length - 200} more</td></tr>
              )}
            </tbody>
          </table>
        )}
      </Section>

      {/* Calculations */}
      <Section label="Calculations" count={calculations.length} open={sectionOpen("calculations")} onToggle={() => toggle("calculations")}>
        {calculations.length === 0 ? <Empty /> : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-zinc-500 border-b">
                <Th>ID</Th><Th>Plan</Th><Th>Period</Th><Th>Version</Th><Th>Status</Th><Th>Created</Th>
              </tr>
            </thead>
            <tbody>
              {calculations.map(c => (
                <tr key={c.id} className="border-b border-zinc-100 hover:bg-zinc-50">
                  <Td mono>{c.id.slice(0, 12)}</Td>
                  <Td mono>{c.plan_id}</Td>
                  <Td mono>{c.period}</Td>
                  <Td>{c.version}</Td>
                  <Td>{c.status}</Td>
                  <Td>{c.created_at?.slice(0, 16)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {/* Period Status */}
      <Section label="Period Status" count={Object.values(periods).flat().length} open={sectionOpen("periods")} onToggle={() => toggle("periods")}>
        {Object.values(periods).flat().length === 0 ? <Empty /> : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-zinc-500 border-b">
                <Th>Plan</Th><Th>Period</Th><Th>Versions</Th><Th>Latest</Th><Th>Locked</Th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(periods).flatMap(([planId, rows]) =>
                rows.map(r => (
                  <tr key={`${planId}-${r.period}`} className="border-b border-zinc-100 hover:bg-zinc-50">
                    <Td mono>{planId}</Td>
                    <Td mono>{r.period}</Td>
                    <Td>{r.versions}</Td>
                    <Td>{r.latest_version}</Td>
                    <Td>{r.locked_calc_id ? "🔒" : ""}</Td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        )}
      </Section>

      {/* Column Mappings */}
      <Section label="Column Mappings" count={mappings.length} open={sectionOpen("mappings")} onToggle={() => toggle("mappings")}>
        {mappings.length === 0 ? <Empty /> : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-zinc-500 border-b">
                <Th>ID</Th><Th>Name</Th><Th>Created</Th>
              </tr>
            </thead>
            <tbody>
              {mappings.map((m: Record<string, unknown>) => (
                <tr key={String(m.id)} className="border-b border-zinc-100 hover:bg-zinc-50">
                  <Td mono>{String(m.id)}</Td>
                  <Td>{String(m.name)}</Td>
                  <Td>{String(m.created_at ?? "").slice(0, 16)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
    </div>
  );
}

function Section({ label, count, open, onToggle, children }: {
  label: string; count: number; open: boolean; onToggle: () => void; children: React.ReactNode;
}) {
  return (
    <div className="border border-zinc-200 rounded-lg bg-white">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-4 py-2.5 text-left hover:bg-zinc-50 rounded-lg"
      >
        <span className="font-semibold text-sm text-zinc-700">
          {label} <span className="text-zinc-400 font-normal ml-1">({count})</span>
        </span>
        <span className="text-zinc-400 text-xs">{open ? "▲" : "▼"}</span>
      </button>
      {open && <div className="border-t border-zinc-100 max-h-80 overflow-auto">{children}</div>}
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="px-3 py-2 font-medium whitespace-nowrap">{children}</th>;
}

function Td({ children, mono }: { children: React.ReactNode; mono?: boolean }) {
  return (
    <td className={`px-3 py-1.5 whitespace-nowrap ${mono ? "font-mono text-zinc-600" : "text-zinc-700"}`}>
      {children}
    </td>
  );
}

function Empty() {
  return <div className="p-4 text-zinc-400 text-xs">No data yet.</div>;
}

import { useEffect, useState, type ReactNode } from "react";
import {
  listPlans,
  listPayees,
  listCalculations,
  listTransactions,
  listPeriods,
  listMappings,
  type CalculationRow,
  type TransactionRow,
  type PeriodStatusRow,
} from "../api";
import type { Payee, SavedPlan } from "../types";
import { Th, Td } from "./Table";
import { Badge, Button, Callout, Icon, PageHeader, Spinner, type IconName } from "./ui";
import { cx } from "./ui/cx";

type Section = {
  id: string;
  label: string;
  count: number;
  open: boolean;
};

const ICONS: Record<string, IconName> = {
  plans: "fileText",
  payees: "users",
  transactions: "table",
  calculations: "calculator",
  periods: "lock",
  mappings: "sliders",
};

export default function DataModel() {
  const [sections, setSections] = useState<Section[]>([]);
  const [plans, setPlans] = useState<SavedPlan[]>([]);
  const [payees, setPayees] = useState<Payee[]>([]);
  const [calculations, setCalculations] = useState<CalculationRow[]>([]);
  const [transactions, setTransactions] = useState<TransactionRow[]>([]);
  const [periods, setPeriods] = useState<Record<string, PeriodStatusRow[]>>({});
  const [mappings, setMappings] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [txnPeriod, setTxnPeriod] = useState("");

  useEffect(() => {
    loadAll();
  }, []);

  // A period filter beats a truncated list: the server already supports it.
  useEffect(() => {
    let cancelled = false;
    listTransactions(txnPeriod || undefined)
      .then(rows => { if (!cancelled) setTransactions(rows); })
      .catch(() => { /* the section simply stays as it was */ });
    return () => { cancelled = true; };
  }, [txnPeriod]);

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
        { id: "periods", label: "Period status", count: Object.values(perMap).flat().length, open: false },
        { id: "mappings", label: "Column mappings", count: maps.length, open: false },
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

  const header = (
    <PageHeader
      title="Data"
      description="Everything stored in the local database, read-only. Useful for checking what a run was built from."
      actions={<Button icon="refresh" onClick={loadAll} disabled={loading}>Refresh</Button>}
    />
  );

  if (loading) return <div className="animate-in">{header}<div className="flex items-center gap-2 text-[13px] text-ink-2"><Spinner /> Loading data model...</div></div>;
  if (error) return <div className="animate-in">{header}<Callout tone="danger">{error}</Callout></div>;

  const sectionOpen = (id: string) => sections.find(s => s.id === id)?.open ?? false;
  const periodRows = Object.values(periods).flat().length;

  return (
    <div className="animate-in">
      {header}

      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {sections.map(s => (
          <button key={s.id} onClick={() => toggle(s.id)}
            className={cx("card flex items-center gap-3 px-3.5 py-3 text-left transition-colors hover:border-line-strong",
              s.open && "border-accent/40 bg-accent-soft")}>
            <Icon name={ICONS[s.id]} className={s.open ? "text-accent-ink" : "text-ink-3"} />
            <span className="min-w-0">
              <span className="block text-[18px] font-semibold leading-tight num text-ink">{s.count}</span>
              <span className="block truncate text-[12px] text-ink-2">{s.label}</span>
            </span>
          </button>
        ))}
      </div>

      <div className="space-y-3">
        {/* Plans */}
        <Section id="plans" label="Plans" count={plans.length} open={sectionOpen("plans")} onToggle={() => toggle("plans")}>
          {plans.length === 0 ? <Empty /> : (
            <table>
              <thead><tr><Th>ID</Th><Th>Name</Th><Th>Updated</Th></tr></thead>
              <tbody>
                {plans.map(p => (
                  <tr key={p.id}>
                    <Td mono>{p.id}</Td>
                    <Td>{p.name}</Td>
                    <Td className="text-ink-2">{p.updated_at?.slice(0, 16)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>

        {/* Payees */}
        <Section id="payees" label="Payees" count={payees.length} open={sectionOpen("payees")} onToggle={() => toggle("payees")}>
          {payees.length === 0 ? <Empty /> : (
            <table>
              <thead><tr><Th>ID</Th><Th>Name</Th><Th>Plan</Th><Th className="text-right">Quota</Th><Th>Team</Th><Th>Manager</Th></tr></thead>
              <tbody>
                {payees.map((p) => (
                  <tr key={String(p.id)}>
                    <Td mono>{String(p.id)}</Td>
                    <Td>{String(p.name)}</Td>
                    <Td mono>{String(p.plan_id ?? "")}</Td>
                    <Td num>{String(p.quota ?? "")}</Td>
                    <Td mono>{String(p.team_id ?? "")}</Td>
                    <Td mono>{String(p.manager_id ?? "")}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>

        {/* Transactions */}
        <Section id="transactions" label="Transactions" count={transactions.length} open={sectionOpen("transactions")} onToggle={() => toggle("transactions")}
          tools={
            <input
              aria-label="Filter transactions by period"
              value={txnPeriod}
              onChange={e => setTxnPeriod(e.target.value)}
              placeholder="All periods"
              className="w-32 py-1 text-xs"
            />
          }>
          {transactions.length === 0 ? <Empty /> : (
            <table>
              <thead><tr><Th>ID</Th><Th>Payee</Th><Th>Deal</Th><Th>Period</Th><Th className="text-right">Amount</Th><Th>Product</Th></tr></thead>
              <tbody>
                {transactions.map(t => (
                  <tr key={t.id}>
                    <Td mono>{t.id}</Td>
                    <Td mono>{t.payee_id}</Td>
                    <Td mono>{t.deal_id}</Td>
                    <Td mono>{t.period}</Td>
                    <Td num>{t.amount}</Td>
                    <Td>{t.product ?? ""}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>

        {/* Calculations */}
        <Section id="calculations" label="Calculations" count={calculations.length} open={sectionOpen("calculations")} onToggle={() => toggle("calculations")}>
          {calculations.length === 0 ? <Empty /> : (
            <table>
              <thead><tr><Th>ID</Th><Th>Plan</Th><Th>Period</Th><Th>Version</Th><Th>Status</Th><Th>Created</Th></tr></thead>
              <tbody>
                {calculations.map(c => (
                  <tr key={c.id}>
                    <Td mono className="select-all text-ink-2">{c.id}</Td>
                    <Td mono>{c.plan_id}</Td>
                    <Td mono>{c.period}</Td>
                    <Td>{c.version}</Td>
                    <Td><Badge tone={c.status === "completed" ? "success" : "neutral"}>{c.status}</Badge></Td>
                    <Td className="text-ink-2">{c.created_at?.slice(0, 16)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>

        {/* Period Status */}
        <Section id="periods" label="Period status" count={periodRows} open={sectionOpen("periods")} onToggle={() => toggle("periods")}>
          {periodRows === 0 ? <Empty /> : (
            <table>
              <thead><tr><Th>Plan</Th><Th>Period</Th><Th>Versions</Th><Th>Latest</Th><Th>Locked</Th></tr></thead>
              <tbody>
                {Object.entries(periods).flatMap(([planId, rows]) =>
                  rows.map(r => (
                    <tr key={`${planId}-${r.period}`}>
                      <Td mono>{planId}</Td>
                      <Td mono>{r.period}</Td>
                      <Td>{r.versions}</Td>
                      <Td>{r.latest_version}</Td>
                      <Td>{r.locked_calc_id ? <Badge tone="warning" icon="lock">Locked</Badge> : <span className="text-ink-3">Open</span>}</Td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          )}
        </Section>

        {/* Column Mappings */}
        <Section id="mappings" label="Column mappings" count={mappings.length} open={sectionOpen("mappings")} onToggle={() => toggle("mappings")}>
          {mappings.length === 0 ? <Empty /> : (
            <table>
              <thead><tr><Th>ID</Th><Th>Name</Th><Th>Created</Th></tr></thead>
              <tbody>
                {mappings.map((m: Record<string, unknown>) => (
                  <tr key={String(m.id)}>
                    <Td mono>{String(m.id)}</Td>
                    <Td>{String(m.name)}</Td>
                    <Td className="text-ink-2">{String(m.created_at ?? "").slice(0, 16)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      </div>
    </div>
  );
}

function Section({ id, label, count, open, onToggle, tools, children }: {
  id: string; label: string; count: number; open: boolean; onToggle: () => void; tools?: ReactNode; children: ReactNode;
}) {
  return (
    <div className="card overflow-hidden">
      <div className="flex items-center gap-3 pr-4">
        <button
          onClick={onToggle}
          aria-expanded={open}
          className="flex flex-1 items-center gap-3 px-4 py-3 text-left hover:bg-surface-2/60"
        >
          <Icon name="chevronRight" size={14} className={cx("text-ink-3 transition-transform", open && "rotate-90")} />
          <Icon name={ICONS[id]} className="text-ink-2" />
          <span className="text-[13.5px] font-semibold text-ink">{label}</span>
          <span className="badge num">{count}</span>
        </button>
        {open && tools}
      </div>
      {open && <div className="table-wrap max-h-80 border-t border-line">{children}</div>}
    </div>
  );
}

function Empty() {
  return <p className="px-5 py-4 text-[13px] text-ink-3">No data yet.</p>;
}

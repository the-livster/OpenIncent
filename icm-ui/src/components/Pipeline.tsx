import { useCallback, useState } from "react";
import type { CalculateResponse, SavedPlan } from "../types";
import StagePayees from "./StagePayees";
import StageQuotas from "./StageQuotas";
import StagePlans from "./StagePlans";
import StageEligibility from "./StageEligibility";
import StageCrediting from "./StageCrediting";
import StageAttainment from "./StageAttainment";
import StageEarnings from "./StageEarnings";
import StageAdjustments from "./StageAdjustments";
import StageReporting from "./StageReporting";
import PipelineWelcome from "./PipelineWelcome";
import { parsePayees, previewFile } from "../api";
import { Button, Callout, Icon, PageHeader } from "./ui";
import { cx } from "./ui/cx";

export interface PayeeRow {
  id: string; name: string; quota: string; plan_id: string;
  quotas?: Record<string, string>;
  effective_from: string; effective_to: string; email: string;
  ramp_months: string; ramp_schedule: string; category_quotas: string;
  // A recoverable draw that never reaches the engine is money the company
  // never recovers, so these travel with the row like every other field.
  draw_amount: string; draw_recoverable: string;
  manager_id: string; manager_override: string; team_id: string;
}

export interface TransactionRow {
  id: string; payee_id: string; deal_id: string; period: string;
  amount: string; product: string; close_date: string;
}

const STAGES = [
  { n: 1, id: "payees", label: "Payees", group: "Setup" },
  { n: 2, id: "quotas", label: "Quotas", group: "Setup" },
  { n: 3, id: "plans", label: "Plans", group: "Setup" },
  { n: 4, id: "eligibility", label: "Eligibility", group: "Setup" },
  { n: 5, id: "crediting", label: "Crediting", group: "Run" },
  { n: 6, id: "attainment", label: "Attainment", group: "Results" },
  { n: 7, id: "earnings", label: "Earnings", group: "Results" },
  { n: 8, id: "adjustments", label: "Adjustments", group: "Results" },
  { n: 9, id: "reporting", label: "Reporting", group: "Results" },
];

export default function Pipeline() {
  const [stage, setStage] = useState(1);
  const [payees, setPayees] = useState<PayeeRow[]>([]);
  const [transactions, setTransactions] = useState<TransactionRow[]>([]);
  const [plans, setPlans] = useState<SavedPlan[]>([]);
  const [result, setResult] = useState<CalculateResponse | null>(null);
  const [resultsAvailable, setResultsAvailable] = useState(false);
  // Track which result stages have been visited
  const [visitedResults, setVisitedResults] = useState<Set<number>>(new Set());
  const [txnFile, setTxnFile] = useState<File | null>(null);
  const [samplePlan, setSamplePlan] = useState<File | null>(null);
  const [sampleLoading, setSampleLoading] = useState(false);
  const [sampleError, setSampleError] = useState("");

  const invalidate = useCallback(() => {
    setResult(null);
    setResultsAvailable(false);
    setVisitedResults(new Set());
  }, []);
  const updatePayees = useCallback((rows: PayeeRow[]) => { setPayees(rows); invalidate(); }, [invalidate]);
  const updateTransactions = useCallback((rows: TransactionRow[]) => { setTransactions(rows); invalidate(); }, [invalidate]);
  const updatePlans = useCallback((rows: SavedPlan[]) => {
    setPlans(previous => {
      if (JSON.stringify(previous) === JSON.stringify(rows)) return previous;
      return rows;
    });
  }, []);

  async function loadSample() {
    setSampleLoading(true); setSampleError("");
    try {
      const files = await Promise.all(["payees.csv", "transactions.csv", "openincent_sample.yaml"].map(async name => {
        const response = await fetch(`/sample/${name}`);
        if (!response.ok) throw new Error("Sample files could not be loaded. Please try again.");
        return new File([await response.text()], name);
      }));
      const roster = await parsePayees(files[0]);
      const preview = await previewFile(files[1]);
      const rows = preview.preview_rows.map(row => Object.fromEntries(preview.headers.map((key, i) => [key, row[i]])) as unknown as TransactionRow);
      setPayees(roster); setTransactions(rows); setTxnFile(files[1]); setSamplePlan(files[2]);
      setPlans([{ id: "openincent_sample", name: "Sample monthly commission", description: "10% of sales", yaml_content: await files[2].text(), created_at: "", updated_at: "" }]);
      invalidate(); setStage(1);
    } catch (e) { setSampleError(e instanceof Error ? e.message : "Could not load sample data."); }
    finally { setSampleLoading(false); }
  }

  function exitSample() {
    setSamplePlan(null); setPayees([]); setTransactions([]); setTxnFile(null); setPlans([]);
    invalidate(); setStage(1);
  }

  const canAccess = useCallback((s: number) => {
    if (s <= 5) return true; // input stages: 1-5
    return resultsAvailable;  // result stages: 6-9
  }, [resultsAvailable]);

  const goTo = useCallback((s: number) => {
    if (canAccess(s)) {
      setStage(s);
      if (s >= 6) setVisitedResults(prev => new Set(prev).add(s));
    }
  }, [canAccess]);

  const onCalculated = useCallback((r: CalculateResponse) => {
    setResult(r);
    setResultsAvailable(true);
    setVisitedResults(new Set([6]));
    setStage(6); // auto-advance to Attainment
  }, []);

  // Per-stage completion: whether the data for that stage has been provided
  const stageComplete = useCallback((n: number): boolean => {
    switch (n) {
      case 1: return payees.length > 0;
      case 2: return payees.some(p => p.quota && p.quota !== "0");
      case 3: return plans.length > 0;
      case 4: return payees.some(p => p.plan_id);
      case 5: return resultsAvailable;
      case 6: case 7: case 8:
        return visitedResults.has(n);
      case 9: return visitedResults.has(9);
      default: return false;
    }
  }, [payees, plans, resultsAvailable, visitedResults]);

  const groups = [...new Set(STAGES.map(s => s.group))];

  return (
    <div className="space-y-5">
      <PageHeader
        title="Pipeline"
        description="Set up once, then run each pay cycle: people and plans, the period's deals, then review every result before statements go out."
      />

      <nav aria-label="Pipeline steps" className="card">
        {/* Groups wrap onto a second line on narrow screens rather than scroll. */}
        <ol className="flex flex-wrap items-stretch gap-2 p-2">
          {groups.map(group => (
            <li key={group} className="flex flex-col rounded-[10px] bg-surface-2/70 px-1 pb-1">
              <span className="eyebrow px-2 pb-0.5 pt-1.5">{group}</span>
              <ol className="flex flex-wrap items-center gap-0.5">
                {STAGES.filter(s => s.group === group).map(s => {
                  const complete = stageComplete(s.n);
                  const current = stage === s.n;
                  const enabled = canAccess(s.n);
                  return (
                    <li key={s.n} className="flex items-center">
                      <button
                        onClick={() => goTo(s.n)}
                        disabled={!enabled}
                        aria-current={current ? "step" : undefined}
                        className={cx(
                          "flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[13px] font-medium transition-colors",
                          current && "bg-accent-soft text-accent-ink",
                          !current && enabled && "text-ink-2 hover:bg-surface hover:text-ink",
                          !enabled && "text-ink-3 opacity-55",
                        )}
                      >
                        <span
                          aria-hidden="true"
                          className={cx(
                            "grid h-[22px] w-[22px] shrink-0 place-items-center rounded-full text-[11px] font-semibold num",
                            current ? "bg-accent text-on-accent"
                              : complete ? "bg-success-soft text-success-ink"
                              : "bg-surface-3 text-ink-2",
                          )}
                        >
                          {complete && !current ? <Icon name="check" size={12} strokeWidth={2.8} /> : s.n}
                        </span>
                        {s.label}
                      </button>
                    </li>
                  );
                })}
              </ol>
            </li>
          ))}
        </ol>
      </nav>

      {samplePlan && (
        <Callout
          tone="warning"
          title="Sample workspace"
          action={<Button size="sm" onClick={exitSample}>Exit sample</Button>}
        >
          Fictional data, kept apart from your business data. The unchanged sample pays USD 3,500.00 — follow the steps
          through to download statements.
        </Callout>
      )}

      {stage === 1 && !samplePlan && payees.length === 0 && <PipelineWelcome onSample={loadSample} loading={sampleLoading} error={sampleError} />}
      {stage === 1 && <StagePayees payees={payees} setPayees={updatePayees} onNext={() => goTo(2)} />}
      {stage === 2 && <StageQuotas payees={payees} setPayees={updatePayees} onNext={() => goTo(3)} onBack={() => goTo(1)} />}
      {stage === 3 && <StagePlans plans={plans} setPlans={updatePlans} sample={!!samplePlan} onChanged={invalidate} onNext={() => goTo(4)} onBack={() => goTo(2)} />}
      {stage === 4 && <StageEligibility payees={payees} setPayees={updatePayees} plans={plans} setPlans={updatePlans} sample={!!samplePlan} onNext={() => goTo(5)} onBack={() => goTo(3)} />}
      {stage === 5 && (
        <StageCrediting
          payees={payees} transactions={transactions} setTransactions={updateTransactions}
          txnFile={txnFile} setTxnFile={setTxnFile} samplePlan={samplePlan} onCalculated={onCalculated}
          onBack={() => goTo(4)}
        />
      )}
      {stage === 6 && result && <StageAttainment result={result} onNext={() => goTo(7)} onBack={() => goTo(5)} />}
      {stage === 7 && result && <StageEarnings result={result} onNext={() => goTo(8)} onBack={() => goTo(6)} />}
      {stage === 8 && result && <StageAdjustments result={result} onNext={() => goTo(9)} onBack={() => goTo(7)} />}
      {stage === 9 && result && <StageReporting result={result} onBack={() => goTo(8)} />}
    </div>
  );
}

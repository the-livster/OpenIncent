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

export interface PayeeRow {
  id: string; name: string; quota: string; plan_id: string;
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

  const canAccess = useCallback((s: number) => {
    if (s <= 5) return true; // input stages: 1-5
    return resultsAvailable;  // result stages: 6-9
  }, [resultsAvailable]);

  const goTo = useCallback((s: number) => {
    if (canAccess(s) || s <= stage) {
      setStage(s);
      if (s >= 6) setVisitedResults(prev => new Set(prev).add(s));
    }
  }, [canAccess, stage]);

  const onCalculated = useCallback((r: CalculateResponse) => {
    setResult(r);
    setResultsAvailable(true);
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

  const groupLabels = [...new Set(STAGES.map(s => s.group))];

  return (
    <div className="flex min-h-screen">
      {/* Left rail */}
      <nav className="w-56 shrink-0 border-r border-zinc-200 bg-zinc-50/50 p-4 space-y-6">
        <div className="text-sm font-bold text-zinc-800">Pipeline</div>
        {groupLabels.map(group => (
          <div key={group} className="space-y-0">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400 mb-1 ml-3">
              {group}
            </div>
            {STAGES.filter(s => s.group === group).map((s, i, arr) => {
              const complete = stageComplete(s.n);
              const prevComplete = i > 0 ? stageComplete(arr[i - 1].n) : false;
              const isFirst = i === 0;
              const isLast = i === arr.length - 1;
              return (
                <div key={s.n} className="flex items-stretch">
                  {/* Connecting line column */}
                  <div className="w-5 shrink-0 flex flex-col items-center mr-2.5">
                    {/* Incoming line: fills when the PREVIOUS stage is complete */}
                    <div className={`w-0.5 flex-1 min-h-[4px] ${isFirst ? "bg-transparent" : prevComplete ? "bg-green-400" : "bg-zinc-200"}`} />
                    {/* Circle */}
                    <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0
                      ${stage === s.n ? "bg-blue-600 text-white ring-2 ring-blue-200" : s.n < stage ? "bg-green-100 text-green-700" : canAccess(s.n) ? "bg-zinc-200 text-zinc-500" : "bg-zinc-100 text-zinc-300"}`}>
                      {s.n < stage ? "✓" : s.n}
                    </span>
                    {/* Outgoing line: fills when THIS stage is complete */}
                    <div className={`w-0.5 flex-1 min-h-[4px] ${isLast ? "bg-transparent" : complete ? "bg-green-400" : "bg-zinc-200"}`} />
                  </div>
                  {/* Button */}
                  <button
                    onClick={() => goTo(s.n)}
                    disabled={!canAccess(s.n) && s.n > stage}
                    className={`flex-1 text-left px-3 py-2 rounded-lg text-sm transition-colors
                      ${stage === s.n
                        ? "bg-blue-50 text-blue-700 font-semibold"
                        : canAccess(s.n)
                        ? "text-zinc-600 hover:bg-zinc-100 cursor-pointer"
                        : "text-zinc-300 cursor-not-allowed"
                      }`}
                  >
                    {s.label}
                  </button>
                </div>
              );
            })}
          </div>
        ))}
      </nav>

      {/* Main content */}
      <main className="flex-1 p-6 overflow-auto">
        {stage === 1 && <StagePayees payees={payees} setPayees={setPayees} onNext={() => goTo(2)} />}
        {stage === 2 && <StageQuotas payees={payees} setPayees={setPayees} onNext={() => goTo(3)} onBack={() => goTo(1)} />}
        {stage === 3 && <StagePlans plans={plans} setPlans={setPlans} onNext={() => goTo(4)} onBack={() => goTo(2)} />}
        {stage === 4 && <StageEligibility payees={payees} setPayees={setPayees} plans={plans} setPlans={setPlans} onNext={() => goTo(5)} onBack={() => goTo(3)} />}
        {stage === 5 && (
          <StageCrediting
            payees={payees} transactions={transactions} setTransactions={setTransactions}
            plans={plans} onCalculated={onCalculated}
            onBack={() => goTo(4)}
          />
        )}
        {stage === 6 && result && <StageAttainment result={result} onNext={() => goTo(7)} onBack={() => goTo(5)} />}
        {stage === 7 && result && <StageEarnings result={result} onNext={() => goTo(8)} onBack={() => goTo(6)} />}
        {stage === 8 && result && <StageAdjustments result={result} onNext={() => goTo(9)} onBack={() => goTo(7)} />}
        {stage === 9 && result && <StageReporting result={result} onBack={() => goTo(8)} />}
      </main>
    </div>
  );
}

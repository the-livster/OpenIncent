import { useCallback, useState } from "react";
import type { CalculateResponse, SavedPlan } from "../types";
import StagePayees from "./StagePayees";
import StageQuotas from "./StageQuotas";
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
  manager_id: string; manager_override: string; team_id: string;
}

export interface TransactionRow {
  id: string; payee_id: string; deal_id: string; period: string;
  amount: string; product: string; close_date: string;
}

const STAGES = [
  { n: 1, id: "payees", label: "Payees", group: "Setup" },
  { n: 2, id: "quotas", label: "Quotas", group: "Setup" },
  { n: 3, id: "eligibility", label: "Eligibility", group: "Setup" },
  { n: 4, id: "crediting", label: "Crediting", group: "Run" },
  { n: 5, id: "attainment", label: "Attainment", group: "Results" },
  { n: 6, id: "earnings", label: "Earnings", group: "Results" },
  { n: 7, id: "adjustments", label: "Adjustments", group: "Results" },
  { n: 8, id: "reporting", label: "Reporting", group: "Results" },
];

export default function Pipeline() {
  const [stage, setStage] = useState(1);
  const [payees, setPayees] = useState<PayeeRow[]>([]);
  const [transactions, setTransactions] = useState<TransactionRow[]>([]);
  const [plans, setPlans] = useState<SavedPlan[]>([]);
  const [result, setResult] = useState<CalculateResponse | null>(null);
  const [resultsAvailable, setResultsAvailable] = useState(false);

  const canAccess = useCallback((s: number) => {
    if (s <= 4) return true; // input stages always accessible
    return resultsAvailable; // result stages only after calculation
  }, [resultsAvailable]);

  const goTo = useCallback((s: number) => {
    if (canAccess(s) || s <= stage) setStage(s);
  }, [canAccess, stage]);

  const onCalculated = useCallback((r: CalculateResponse) => {
    setResult(r);
    setResultsAvailable(true);
    setStage(5); // auto-advance to Attainment
  }, []);

  const groupLabels = [...new Set(STAGES.map(s => s.group))];

  return (
    <div className="flex min-h-screen">
      {/* Left rail */}
      <nav className="w-56 shrink-0 border-r border-zinc-200 bg-zinc-50/50 p-4 space-y-6">
        <div className="text-sm font-bold text-zinc-800">Pipeline</div>
        {groupLabels.map(group => (
          <div key={group} className="space-y-1">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400 mb-1">
              {group}
            </div>
            {STAGES.filter(s => s.group === group).map(s => (
              <button
                key={s.n}
                onClick={() => goTo(s.n)}
                disabled={!canAccess(s.n) && s.n > stage}
                className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors flex items-center gap-2.5
                  ${stage === s.n
                    ? "bg-blue-50 text-blue-700 font-semibold"
                    : canAccess(s.n)
                    ? "text-zinc-600 hover:bg-zinc-100 cursor-pointer"
                    : "text-zinc-300 cursor-not-allowed"
                  }`}
              >
                <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0
                  ${stage === s.n ? "bg-blue-600 text-white" : s.n < stage ? "bg-green-100 text-green-700" : canAccess(s.n) ? "bg-zinc-200 text-zinc-500" : "bg-zinc-100 text-zinc-300"}`}>
                  {s.n < stage ? "✓" : s.n}
                </span>
                {s.label}
              </button>
            ))}
          </div>
        ))}
      </nav>

      {/* Main content */}
      <main className="flex-1 p-6 overflow-auto">
        {stage === 1 && <StagePayees payees={payees} setPayees={setPayees} onNext={() => goTo(2)} />}
        {stage === 2 && <StageQuotas payees={payees} setPayees={setPayees} onNext={() => goTo(3)} onBack={() => goTo(1)} />}
        {stage === 3 && <StageEligibility payees={payees} setPayees={setPayees} plans={plans} setPlans={setPlans} onNext={() => goTo(4)} onBack={() => goTo(2)} />}
        {stage === 4 && (
          <StageCrediting
            payees={payees} transactions={transactions} setTransactions={setTransactions}
            plans={plans} onCalculated={onCalculated}
            onBack={() => goTo(3)}
          />
        )}
        {stage === 5 && result && <StageAttainment result={result} onNext={() => goTo(6)} onBack={() => goTo(4)} />}
        {stage === 6 && result && <StageEarnings result={result} onNext={() => goTo(7)} onBack={() => goTo(5)} />}
        {stage === 7 && result && <StageAdjustments result={result} onNext={() => goTo(8)} onBack={() => goTo(6)} />}
        {stage === 8 && result && <StageReporting result={result} onBack={() => goTo(7)} />}
      </main>
    </div>
  );
}

import { useCallback, useState } from "react";

import CalculatorWizard from "./components/CalculatorWizard";
import PlanGenerator from "./components/PlanGenerator";
import PlanLibrary from "./components/PlanLibrary";
import Settings from "./components/Settings";

type Tab = "calculator" | "plans" | "ai" | "settings";

export default function App() {
  const [tab, setTab] = useState<Tab>("calculator");

  // Plan library ↔ calculator bridge
  const [loadedPlan, setLoadedPlan] = useState<{ yaml: string; name: string } | null>(null);
  // AI builder → plan library bridge
  const [planToSave, setPlanToSave] = useState<{ yaml: string; name: string } | null>(null);

  const handleLoadPlan = useCallback((yaml: string, name: string) => {
    setLoadedPlan({ yaml, name });
    setTab("calculator");
  }, []);

  const handlePlanSaved = useCallback(() => {
    setPlanToSave(null);
  }, []);

  return (
    <div className="min-h-screen gradient-bg">
      {/* Header */}
      <header className="border-b border-surface-300/20">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-brand-400 to-brand-600 flex items-center justify-center text-white text-sm font-bold shadow-lg shadow-brand-500/30">
              O
            </div>
            <div>
              <h1 className="text-base font-bold text-surface-900 tracking-tight">OpenIncent</h1>
              <p className="text-[10px] text-surface-500 -mt-0.5 tracking-wider uppercase">Incentive Compensation</p>
            </div>
          </div>

          <nav className="flex items-center gap-1 bg-surface-100/50 rounded-xl p-1 border border-surface-300/30">
            <NavTab active={tab === "calculator"} onClick={() => setTab("calculator")} label="Calculator" icon="📊" />
            <NavTab active={tab === "plans"} onClick={() => setTab("plans")} label="Plans" icon="📋" />
            <NavTab active={tab === "ai"} onClick={() => setTab("ai")} label="AI Builder" icon="✨" />
            <NavTab active={tab === "settings"} onClick={() => setTab("settings")} label="Settings" icon="⚙️" />
          </nav>
        </div>
      </header>

      {/* Content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 py-8">
        {tab === "calculator" && <CalculatorWizard loadedPlan={loadedPlan} onPlanConsumed={() => setLoadedPlan(null)} />}
        {tab === "plans" && (
          <PlanLibrary
            onLoadPlan={handleLoadPlan}
            planToSave={planToSave}
            onSaved={handlePlanSaved}
          />
        )}
        {tab === "ai" && <PlanGenerator onPlanGenerated={setPlanToSave} />}
        {tab === "settings" && <Settings />}
      </main>
    </div>
  );
}

function NavTab({
  active, onClick, label, icon,
}: { active: boolean; onClick: () => void; label: string; icon: string }) {
  return (
    <button
      onClick={onClick}
      className={`
        flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
        transition-all cursor-pointer
        ${active
          ? "bg-brand-500/15 text-brand-400 shadow-sm"
          : "text-surface-600 hover:text-surface-700 hover:bg-surface-200/50"
        }
      `}
    >
      <span>{icon}</span> {label}
    </button>
  );
}

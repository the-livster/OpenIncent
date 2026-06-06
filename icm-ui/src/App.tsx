import { useCallback, useState } from "react";

import CalculatorWizard from "./components/CalculatorWizard";
import DataModel from "./components/DataModel";
import PayeeManager from "./components/PayeeManager";
import Pipeline from "./components/Pipeline";
import PlanGenerator from "./components/PlanGenerator";
import PlanLibrary from "./components/PlanLibrary";
import Settings from "./components/Settings";

type Tab = "pipeline" | "calculator" | "plans" | "ai" | "payees" | "data" | "settings";

export default function App() {
  const [tab, setTab] = useState<Tab>("pipeline");

  const [loadedPlan, setLoadedPlan] = useState<{ yaml: string; name: string } | null>(null);
  const [planToSave, setPlanToSave] = useState<{ yaml: string; name: string } | null>(null);

  const handleLoadPlan = useCallback((yaml: string, name: string) => {
    setLoadedPlan({ yaml, name });
    setTab("calculator");
  }, []);

  const handlePlanSaved = useCallback(() => {
    setPlanToSave(null);
  }, []);

  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-line">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="logo-wordmark">
              <span className="logo-open">Open</span><span className="logo-incent">Incent</span>
            </span>
          </div>

          <nav className="flex items-center gap-1">
            <NavTab active={tab === "pipeline"} onClick={() => setTab("pipeline")} label="Pipeline" />
            <NavTab active={tab === "calculator"} onClick={() => setTab("calculator")} label="Quick Calc" />
            <NavTab active={tab === "plans"} onClick={() => setTab("plans")} label="Plans" />
            <NavTab active={tab === "payees"} onClick={() => setTab("payees")} label="Payees" />
            <NavTab active={tab === "data"} onClick={() => setTab("data")} label="Data" />
            <NavTab active={tab === "ai"} onClick={() => setTab("ai")} label="AI Builder" />
            <NavTab active={tab === "settings"} onClick={() => setTab("settings")} label="Settings" />
          </nav>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 py-8">
        {tab === "pipeline" && <Pipeline />}
        {tab === "calculator" && <CalculatorWizard loadedPlan={loadedPlan} onPlanConsumed={() => setLoadedPlan(null)} />}
        {tab === "plans" && (
          <PlanLibrary onLoadPlan={handleLoadPlan} planToSave={planToSave} onSaved={handlePlanSaved} />
        )}
        {tab === "ai" && <PlanGenerator onPlanGenerated={setPlanToSave} />}
        {tab === "payees" && <PayeeManager />}
        {tab === "data" && <DataModel />}
        {tab === "settings" && <Settings />}
      </main>
    </div>
  );
}

function NavTab({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className={`
        px-3 py-1.5 rounded-md text-sm font-medium transition-colors cursor-pointer
        ${active
          ? "text-accent bg-soft2"
          : "text-ink2 hover:text-ink hover:bg-soft"
        }
      `}
    >
      {label}
    </button>
  );
}

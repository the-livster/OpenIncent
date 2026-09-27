import { useCallback, useEffect, useState } from "react";

import { healthCheck } from "./api";
import CalculatorWizard from "./components/CalculatorWizard";
import DataModel from "./components/DataModel";
import History from "./components/History";
import Pipeline from "./components/Pipeline";
import PlanGenerator from "./components/PlanGenerator";
import PlanLibrary from "./components/PlanLibrary";
import Roster from "./components/Roster";
import Settings from "./components/Settings";
import { Button, Icon, LogoMark, Segmented, type IconName } from "./components/ui";
import { cx } from "./components/ui/cx";
import { useTheme, type ThemePref } from "./theme";

type Tab = "pipeline" | "history" | "calculator" | "plans" | "ai" | "payees" | "data" | "settings";

const NAV: { group: string; items: { id: Tab; label: string; icon: IconName }[] }[] = [
  {
    group: "Run",
    items: [
      { id: "pipeline", label: "Pipeline", icon: "workflow" },
      { id: "history", label: "History", icon: "history" },
      { id: "calculator", label: "Quick Calc", icon: "calculator" },
    ],
  },
  {
    group: "Configure",
    items: [
      { id: "plans", label: "Plans", icon: "fileText" },
      { id: "ai", label: "AI Builder", icon: "sparkles" },
      { id: "payees", label: "Payees", icon: "users" },
    ],
  },
  {
    group: "System",
    items: [
      { id: "data", label: "Data", icon: "database" },
      { id: "settings", label: "Settings", icon: "settings" },
    ],
  },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("pipeline");
  const [navOpen, setNavOpen] = useState(false);
  const [theme, setTheme] = useTheme();

  const [loadedPlan, setLoadedPlan] = useState<{ yaml: string; name: string } | null>(null);
  const [planToSave, setPlanToSave] = useState<{ yaml: string; name: string } | null>(null);

  const go = useCallback((next: Tab) => {
    setTab(next);
    setNavOpen(false);
  }, []);

  const handleLoadPlan = useCallback((yaml: string, name: string) => {
    setLoadedPlan({ yaml, name });
    setTab("calculator");
  }, []);

  const handlePlanSaved = useCallback(() => {
    setPlanToSave(null);
  }, []);

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[236px_minmax(0,1fr)]">
      {/* Narrow screens: a top bar that opens the sidebar over the page. */}
      <div className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-line bg-surface/90 px-3 backdrop-blur lg:hidden">
        <Button variant="ghost" size="sm" icon="menu" aria-label="Open navigation" onClick={() => setNavOpen(true)} />
        <Brand />
      </div>
      {navOpen && (
        <div className="fixed inset-0 z-40 bg-[rgb(10_12_18/0.38)] animate-fade lg:hidden" onClick={() => setNavOpen(false)} />
      )}

      <header
        className={cx(
          "fixed inset-y-0 left-0 z-50 flex w-[236px] flex-col border-r border-line bg-surface transition-transform duration-200",
          "lg:sticky lg:top-0 lg:z-auto lg:h-screen lg:translate-x-0",
          navOpen ? "translate-x-0 shadow-[var(--shadow-pop)]" : "-translate-x-full",
        )}
      >
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-line px-4">
          <Brand />
          <Button variant="ghost" size="sm" icon="x" aria-label="Close navigation" className="lg:hidden"
            onClick={() => setNavOpen(false)} />
        </div>

        <nav aria-label="Main" className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
          {NAV.map(group => (
            <div key={group.group}>
              <p className="eyebrow mb-1.5 px-2.5">{group.group}</p>
              <ul className="space-y-0.5">
                {group.items.map(item => (
                  <li key={item.id}>
                    <NavItem item={item} active={tab === item.id} onClick={() => go(item.id)} />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <div className="shrink-0 space-y-3 border-t border-line p-3">
          <EngineStatus />
          <Segmented<ThemePref>
            label="Theme"
            value={theme}
            onChange={setTheme}
            className="flex w-full [&>button]:flex-1 [&>button]:justify-center"
            options={[
              { value: "light", label: <span className="sr-only">Light</span>, icon: "sun", title: "Light" },
              { value: "dark", label: <span className="sr-only">Dark</span>, icon: "moon", title: "Dark" },
              { value: "system", label: <span className="sr-only">System</span>, icon: "monitor", title: "Match system" },
            ]}
          />
        </div>
      </header>

      <main className="min-w-0 px-4 py-6 sm:px-6 lg:px-10 lg:py-8">
        <div className="mx-auto max-w-[1200px]">
          <div hidden={tab !== "pipeline"}><Pipeline /></div>
          <div hidden={tab !== "history"}><History /></div>
          <div hidden={tab !== "calculator"}>
            <CalculatorWizard loadedPlan={loadedPlan} onPlanConsumed={() => setLoadedPlan(null)} />
          </div>
          {tab === "plans" && (
            <PlanLibrary onLoadPlan={handleLoadPlan} planToSave={planToSave} onSaved={handlePlanSaved} />
          )}
          {tab === "ai" && <PlanGenerator onPlanGenerated={plan => { setPlanToSave(plan); setTab("plans"); }} />}
          {tab === "payees" && <Roster />}
          {tab === "data" && <DataModel />}
          {tab === "settings" && <Settings />}
        </div>
      </main>
    </div>
  );
}

function Brand() {
  return (
    <span className="flex items-center gap-2.5">
      <LogoMark />
      <span className="text-[15px] font-bold tracking-[-0.02em] text-ink">
        Open<span className="text-accent-ink">Incent</span>
      </span>
    </span>
  );
}

function NavItem({ item, active, onClick }: {
  item: { label: string; icon: IconName }; active: boolean; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={cx(
        "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-[13.5px] transition-colors",
        active
          ? "bg-accent-soft font-semibold text-accent-ink"
          : "font-medium text-ink-2 hover:bg-surface-2 hover:text-ink",
      )}
    >
      <Icon name={item.icon} className={active ? "text-accent-ink" : "text-ink-3"} />
      {item.label}
    </button>
  );
}

/** Whether the local engine API answers, so a dead backend is obvious
 *  before anyone uploads a file. */
function EngineStatus() {
  const [state, setState] = useState<"checking" | "ok" | "down">("checking");

  const check = useCallback(() => {
    setState("checking");
    healthCheck().then(ok => setState(ok ? "ok" : "down"));
  }, []);

  useEffect(() => {
    let cancelled = false;
    healthCheck().then(ok => { if (!cancelled) setState(ok ? "ok" : "down"); });
    return () => { cancelled = true; };
  }, []);

  const label = state === "ok" ? "Engine connected" : state === "down" ? "Engine not reachable" : "Checking engine...";
  return (
    <button
      onClick={check}
      title="Check the connection again"
      className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12.5px] text-ink-2 hover:bg-surface-2"
    >
      <span className={cx(
        "h-2 w-2 shrink-0 rounded-full",
        state === "ok" && "bg-success shadow-[0_0_0_3px_var(--c-success-soft)]",
        state === "down" && "bg-danger shadow-[0_0_0_3px_var(--c-danger-soft)]",
        state === "checking" && "animate-pulse bg-warning",
      )} />
      {label}
    </button>
  );
}

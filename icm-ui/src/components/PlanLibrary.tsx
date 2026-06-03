import { useCallback, useEffect, useState } from "react";
import { deletePlan, listPlans, savePlan } from "../api";
import type { SavedPlan } from "../types";

interface Props {
  onLoadPlan: (yaml: string, name: string) => void;
  planToSave: { yaml: string; name: string } | null;
  onSaved: () => void;
}

export default function PlanLibrary({ onLoadPlan, planToSave, onSaved }: Props) {
  const [plans, setPlans] = useState<SavedPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listPlans();
      setPlans(data);
    } catch {
      setError("Failed to load plans");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Auto-populate save name when a plan is passed in
  useEffect(() => {
    if (planToSave) {
      setSaveName(planToSave.name);
    }
  }, [planToSave]);

  const handleSave = useCallback(async () => {
    if (!planToSave || !saveName.trim()) return;
    setSaving(true);
    setError("");
    try {
      await savePlan(saveName.trim(), planToSave.yaml);
      setSaveName("");
      onSaved();
      await refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }, [planToSave, saveName, onSaved, refresh]);

  const handleDelete = useCallback(async (id: string) => {
    try {
      await deletePlan(id);
      setPlans((prev) => prev.filter((p) => p.id !== id));
    } catch {
      setError("Failed to delete plan");
    }
  }, []);

  return (
    <div className="max-w-3xl mx-auto space-y-6 animate-in">
      {/* Save section — only shown when a plan is ready to save */}
      {planToSave && (
        <div className="glass rounded-xl p-5 space-y-3 border border-brand-400/20">
          <h3 className="text-sm font-semibold text-surface-800">Save to Library</h3>
          <div className="flex gap-2">
            <input
              type="text"
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
              placeholder="Plan name"
              className="
                flex-1 px-3 py-2 rounded-lg text-sm
                bg-surface-100 border border-surface-300/50
                text-surface-800 placeholder:text-surface-500
                focus:outline-none focus:border-brand-400 focus:ring-1 focus:ring-brand-400/30
                transition-all
              "
            />
            <button
              onClick={handleSave}
              disabled={!saveName.trim() || saving}
              className="
                px-4 py-2 rounded-lg text-sm font-medium
                bg-brand-500 text-white
                hover:bg-brand-400
                disabled:opacity-40 disabled:cursor-not-allowed
                transition-all cursor-pointer
              "
            >
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      )}

      {/* Plan list */}
      <div className="glass rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-surface-300/30 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-surface-800">Saved Plans</h2>
          <span className="text-xs text-surface-500">{plans.length} plan{plans.length !== 1 ? "s" : ""}</span>
        </div>

        {error && (
          <div className="px-5 py-3 text-sm text-danger bg-danger/5 border-b border-danger/10">{error}</div>
        )}

        {loading && (
          <div className="px-5 py-12 text-center text-surface-500 text-sm">Loading...</div>
        )}

        {!loading && plans.length === 0 && (
          <div className="px-5 py-12 text-center">
            <div className="text-3xl mb-2 opacity-40">📋</div>
            <p className="text-surface-500 text-sm">No saved plans yet.</p>
            <p className="text-surface-400 text-xs mt-1">
              Generate a plan in the AI Builder and save it here, or upload one in the Calculator.
            </p>
          </div>
        )}

        {!loading && plans.length > 0 && (
          <div className="divide-y divide-surface-300/20">
            {plans.map((plan) => (
              <div key={plan.id} className="px-5 py-3.5 flex items-center justify-between group hover:bg-surface-100/50 transition-colors">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-surface-800 truncate">{plan.name}</div>
                  <div className="text-xs text-surface-500 mt-0.5">
                    {plan.description && <span className="mr-3">{plan.description}</span>}
                    Updated {plan.updated_at.slice(0, 16).replace("T", " ")}
                  </div>
                </div>
                <div className="flex items-center gap-1 ml-3 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button
                    onClick={() => onLoadPlan(plan.yaml_content, plan.name)}
                    className="
                      px-2.5 py-1 rounded text-xs font-medium
                      bg-brand-500/15 text-brand-400 hover:bg-brand-500/25
                      transition-colors cursor-pointer
                    "
                  >
                    Load
                  </button>
                  <button
                    onClick={() => handleDelete(plan.id)}
                    className="
                      px-2 py-1 rounded text-xs
                      text-surface-500 hover:text-danger hover:bg-danger/10
                      transition-colors cursor-pointer
                    "
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

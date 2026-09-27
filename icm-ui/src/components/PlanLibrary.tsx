import { useCallback, useEffect, useState } from "react";
import { deletePlan, listPlans, savePlan } from "../api";
import type { SavedPlan } from "../types";
import { Button, Callout, Card, CardHeader, EmptyState, Icon, PageHeader, Spinner } from "./ui";

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
    if (!window.confirm("Delete this plan? This cannot be undone.")) return;
    try {
      await deletePlan(id);
      setPlans((prev) => prev.filter((p) => p.id !== id));
    } catch {
      setError("Failed to delete plan");
    }
  }, []);

  return (
    <div className="animate-in">
      <PageHeader
        title="Plans"
        description="The commission plans in your library. Load one into Quick Calc, or assign them to payees in the Pipeline."
        actions={<Button icon="refresh" onClick={refresh}>Refresh</Button>}
      />

      <div className="space-y-5">
        {/* Save section — only shown when a plan is ready to save */}
        {planToSave && (
          <Card className="border-accent/40">
            <CardHeader icon="sparkles" title="Save generated plan to library"
              description="Give it a name your team will recognise." />
            <div className="flex flex-wrap gap-2 px-5 py-4">
              <input
                type="text"
                aria-label="Plan name"
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                placeholder="Plan name"
                className="min-w-64 flex-1"
              />
              <Button variant="primary" icon="check" onClick={handleSave} disabled={!saveName.trim() || saving} loading={saving}>
                {saving ? "Saving..." : "Save"}
              </Button>
            </div>
          </Card>
        )}

        {error && <Callout tone="danger">{error}</Callout>}

        <Card className="overflow-hidden">
          <CardHeader
            title="Saved plans"
            actions={<span className="text-[12.5px] text-ink-3 num">{plans.length} plan{plans.length !== 1 ? "s" : ""}</span>}
          />
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-14 text-[13px] text-ink-2"><Spinner /> Loading...</div>
          ) : plans.length === 0 ? (
            <EmptyState
              icon="fileText"
              title="No saved plans yet"
              description="Generate a plan in the AI Builder and save it here, or upload one in Quick Calc or the Pipeline."
            />
          ) : (
            <ul className="divide-y divide-line">
              {plans.map((plan) => (
                <li key={plan.id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3.5">
                  <div className="flex min-w-0 flex-1 items-center gap-3">
                    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent-ink">
                      <Icon name="fileText" />
                    </span>
                    <div className="min-w-0">
                      <div className="truncate text-[13.5px] font-medium text-ink">{plan.name}</div>
                      <div className="mt-0.5 flex flex-wrap gap-x-3 text-[12px] text-ink-3">
                        <span className="font-mono">{plan.id}</span>
                        {plan.description && <span>{plan.description}</span>}
                        <span>Updated {plan.updated_at.slice(0, 16).replace("T", " ")}</span>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Button size="sm" icon="play" onClick={() => onLoadPlan(plan.yaml_content, plan.name)}>
                      Load in Quick Calc
                    </Button>
                    <Button size="sm" variant="ghost" icon="trash" aria-label={`Delete ${plan.name}`}
                      onClick={() => handleDelete(plan.id)} />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

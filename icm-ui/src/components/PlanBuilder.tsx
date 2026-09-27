import { useCallback, useState } from "react";
import { savePlan } from "../api";
import { Button, Callout, Card, CardHeader, Icon, PageHeader } from "./ui";

export interface PlanBuilderRule {
  type: string;
  id: string;
  filter?: string;
  rate?: string;
  tiers?: { threshold: string; rate: string }[];
  threshold_pct?: string;
  multiplier?: string;
}

export interface PlanBuilderPlanData {
  plan_id: string;
  name: string;
  period_type: string;
  currency: string;
  rules: PlanBuilderRule[];
  [key: string]: unknown;
}

interface Props {
  plan: PlanBuilderPlanData;
  onClose: () => void;
  onUse?: (yaml: string) => void;
  /** Inside another screen (Quick Calc): no page header of its own. */
  embedded?: boolean;
}

export default function PlanBuilder({ plan: initialPlan, onClose, onUse, embedded }: Props) {
  const [plan, setPlan] = useState<PlanBuilderPlanData>(structuredClone(initialPlan));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  const updatePlan = useCallback((fn: (p: PlanBuilderPlanData) => PlanBuilderPlanData) => {
    setPlan(prev => fn(structuredClone(prev)));
    setSaved(false);
  }, []);

  const updateRule = useCallback((idx: number, fn: (r: PlanBuilderRule) => PlanBuilderRule) => {
    updatePlan(p => {
      p.rules[idx] = fn(structuredClone(p.rules[idx]));
      return p;
    });
  }, [updatePlan]);

  const addRule = useCallback(() => {
    updatePlan(p => {
      p.rules.push({
        type: "flat_rate",
        id: `R-${String(p.rules.length + 1).padStart(3, "0")}`,
        rate: "0.05",
      });
      return p;
    });
  }, [updatePlan]);

  const removeRule = useCallback((idx: number) => {
    updatePlan(p => {
      p.rules.splice(idx, 1);
      return p;
    });
  }, [updatePlan]);

  const addTier = useCallback((ruleIdx: number) => {
    updateRule(ruleIdx, r => {
      if (!r.tiers) r.tiers = [];
      const lastThreshold = r.tiers.length > 0
        ? parseFloat(r.tiers[r.tiers.length - 1].threshold)
        : 0;
      r.tiers.push({
        threshold: String(lastThreshold + 100000),
        rate: String(parseFloat(r.rate || "0.05") + r.tiers.length * 0.02),
      });
      return r;
    });
  }, [updateRule]);

  const removeTier = useCallback((ruleIdx: number, tierIdx: number) => {
    updateRule(ruleIdx, r => {
      r.tiers?.splice(tierIdx, 1);
      return r;
    });
  }, [updateRule]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setError("");
    try {
      const yaml = generateYaml(plan);
      await savePlan(plan.name, yaml, plan.plan_id);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to save plan");
    } finally {
      setSaving(false);
    }
  }, [plan]);

  return (
    <div className="animate-in">
      {!embedded && <PageHeader
        title="Plan builder"
        description="Tune rates, tiers and accelerators, then save the plan or use it straight away. The YAML updates as you go."
        actions={<Button variant="ghost" icon="arrowLeft" onClick={onClose}>Back</Button>}
      />}

      <div className="space-y-4">
        {/* Plan settings */}
        <Card>
          <CardHeader icon="fileText" title="Plan settings" />
          <div className="grid grid-cols-1 gap-4 px-5 py-4 sm:grid-cols-2">
            <label className="block">
              <span className="field-label">Plan name</span>
              <input
                type="text"
                value={plan.name}
                onChange={e => updatePlan(p => ({ ...p, name: e.target.value }))}
                className="w-full"
              />
            </label>
            <label className="block">
              <span className="field-label">Period</span>
              <select
                value={plan.period_type}
                onChange={e => updatePlan(p => ({ ...p, period_type: e.target.value }))}
                className="w-full"
              >
                <option value="monthly">Monthly</option>
                <option value="quarterly">Quarterly</option>
                <option value="annual">Annual</option>
              </select>
            </label>
          </div>
        </Card>

        {/* Rules */}
        {plan.rules.map((rule, ri) => (
          <Card key={ri}>
            <CardHeader
              icon="layers"
              title={<span className="flex items-center gap-2">Rule <span className="badge font-mono">{rule.id}</span></span>}
              actions={
                <>
                  <select
                    aria-label={`Rule type for ${rule.id}`}
                    value={rule.type}
                    onChange={e => updateRule(ri, r => {
                      r.type = e.target.value;
                      if (e.target.value === "tiered" && !r.tiers) {
                        r.tiers = [{ threshold: "50000", rate: "0.03" }, { threshold: "100000", rate: "0.05" }];
                      }
                      if (e.target.value === "accelerator") {
                        r.threshold_pct = "1.0";
                        r.multiplier = "2.0";
                      }
                      return r;
                    })}
                  >
                    <option value="flat_rate">Flat rate</option>
                    <option value="tiered">Tiered</option>
                    <option value="accelerator">Accelerator</option>
                  </select>
                  {plan.rules.length > 1 && (
                    <Button size="sm" variant="ghost" icon="trash" aria-label={`Remove rule ${rule.id}`} onClick={() => removeRule(ri)} />
                  )}
                </>
              }
            />
            <div className="space-y-5 px-5 py-4">
              {/* Flat rate / base rate */}
              {(rule.type === "flat_rate" || rule.type === "tiered") && (
                <SliderField
                  label="Base rate"
                  value={parseFloat(rule.rate || "0.05") * 100}
                  min={0}
                  max={50}
                  step={0.5}
                  unit="%"
                  onChange={v => updateRule(ri, r => { r.rate = String(v / 100); return r; })}
                />
              )}

              {/* Tiers */}
              {rule.type === "tiered" && rule.tiers && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="field-label mb-0">Tiers</span>
                    <Button size="sm" variant="ghost" icon="plus" onClick={() => addTier(ri)}>Add tier</Button>
                  </div>
                  <div className="overflow-hidden rounded-xl border border-line">
                    {rule.tiers.map((tier, ti) => (
                      <div key={ti} className="flex items-end gap-3 border-b border-line px-3 py-2.5 last:border-b-0">
                        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-surface-2 text-[12px] font-semibold text-ink-2 num">{ti + 1}</span>
                        <label className="block flex-1">
                          <span className="field-label mb-1 text-[11.5px]">Threshold ($)</span>
                          <input
                            type="number"
                            value={parseFloat(tier.threshold)}
                            onChange={e => updateRule(ri, r => {
                              if (r.tiers) r.tiers[ti].threshold = e.target.value;
                              return r;
                            })}
                            className="w-full num"
                          />
                        </label>
                        <label className="block flex-1">
                          <span className="field-label mb-1 text-[11.5px]">Rate (%)</span>
                          <input
                            type="number"
                            step="0.1"
                            value={parseFloat(tier.rate) * 100}
                            onChange={e => updateRule(ri, r => {
                              if (r.tiers) r.tiers[ti].rate = String(parseFloat(e.target.value || "0") / 100);
                              return r;
                            })}
                            className="w-full num"
                          />
                        </label>
                        {rule.tiers!.length > 1 && (
                          <Button size="sm" variant="ghost" icon="x" aria-label={`Remove tier ${ti + 1}`} onClick={() => removeTier(ri, ti)} />
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Accelerator */}
              {rule.type === "accelerator" && (
                <div className="space-y-5">
                  <SliderField
                    label="Threshold (% of quota)"
                    value={parseFloat(rule.threshold_pct || "1.0") * 100}
                    min={0}
                    max={200}
                    step={5}
                    unit="%"
                    onChange={v => updateRule(ri, r => { r.threshold_pct = String(v / 100); return r; })}
                  />
                  <SliderField
                    label="Multiplier"
                    value={parseFloat(rule.multiplier || "2.0")}
                    min={1}
                    max={5}
                    step={0.1}
                    unit="x"
                    onChange={v => updateRule(ri, r => { r.multiplier = String(v); return r; })}
                  />
                </div>
              )}

              {/* Filter */}
              <label className="block">
                <span className="field-label">Filter <span className="font-normal text-ink-3">(optional)</span></span>
                <input
                  type="text"
                  value={rule.filter || ""}
                  onChange={e => updateRule(ri, r => { r.filter = e.target.value || undefined; return r; })}
                  placeholder='e.g. product == "Enterprise"'
                  className="w-full font-mono"
                />
              </label>
            </div>
          </Card>
        ))}

        {/* Add rule button */}
        <button
          onClick={addRule}
          className="flex w-full items-center justify-center gap-2 rounded-xl border-2 border-dashed border-line-strong py-3 text-[13.5px] font-medium text-ink-2 transition-colors hover:border-accent/60 hover:bg-accent-soft hover:text-accent-ink"
        >
          <Icon name="plus" /> Add rule
        </button>

        {/* YAML preview */}
        <details className="card group overflow-hidden">
          <summary className="flex items-center justify-between px-5 py-3.5 text-[13.5px] font-medium text-ink-2 hover:text-ink">
            <span className="flex items-center gap-2"><Icon name="fileText" /> Preview YAML</span>
            <Icon name="chevronDown" className="transition-transform group-open:rotate-180" />
          </summary>
          <div className="px-4 pb-4">
            <pre className="audit-panel">{generateYaml(plan)}</pre>
          </div>
        </details>

        {/* Save */}
        {error && <Callout tone="danger">{error}</Callout>}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          {onUse && (
            <Button icon="play" onClick={() => onUse(generateYaml(plan))}>Use this plan</Button>
          )}
          <Button variant="primary" icon={saved ? "check" : "download"} onClick={handleSave} disabled={saving} loading={saving}>
            {saving ? "Saving..." : saved ? "Saved" : "Save to library"}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------
// Slider sub-component
// ------------------------------------------------------------------

function SliderField({ label, value, min, max, step, unit, onChange }: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block">
      <span className="mb-2 flex items-center justify-between">
        <span className="field-label mb-0">{label}</span>
        <span className="badge badge-accent num">{value.toFixed(step < 1 ? 1 : 0)}{unit}</span>
      </span>
      <input
        type="range"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="w-full cursor-pointer"
      />
    </label>
  );
}

// ------------------------------------------------------------------
// YAML generator (client-side mirror of server serialization)
// ------------------------------------------------------------------

function generateYaml(plan: PlanBuilderPlanData): string {
  const rules = plan.rules.map(r => {
    const base: Record<string, unknown> = { type: r.type, id: r.id };
    if (r.filter) base.filter = r.filter;
    if (r.type === "flat_rate") {
      base.rate = r.rate;
    } else if (r.type === "tiered") {
      base.rate = r.rate;
      base.tiers = r.tiers;
    } else if (r.type === "accelerator") {
      base.rate = r.rate;
      base.threshold_pct = r.threshold_pct;
      base.multiplier = r.multiplier;
    }
    return base;
  });

  const obj = {
    plan_id: plan.plan_id,
    name: plan.name,
    period_type: plan.period_type,
    currency: plan.currency || "USD",
    rules,
  };

  return toYaml(obj);
}

function toYaml(obj: unknown, indent = 0): string {
  const pad = "  ".repeat(indent);
  if (obj === null || obj === undefined) return "null";
  if (typeof obj === "string") return yamlString(obj);
  if (typeof obj === "number") return String(obj);
  if (typeof obj === "boolean") return String(obj);
  if (Array.isArray(obj)) {
    if (obj.length === 0) return "[]";
    return obj.map(item => `${pad}- ${toYaml(item, indent + 1).trimStart()}`).join("\n");
  }
  if (typeof obj === "object") {
    const entries = Object.entries(obj as Record<string, unknown>);
    if (entries.length === 0) return "{}";
    return entries.map(([k, v]) => {
      const val = toYaml(v, indent + 1);
      if (typeof v === "object" && v !== null && !Array.isArray(v)) {
        return `${pad}${k}:\n${val}`;
      }
      if (Array.isArray(v) && v.length > 0 && typeof v[0] === "object") {
        return `${pad}${k}:\n${val}`;
      }
      return `${pad}${k}: ${val}`;
    }).join("\n");
  }
  return String(obj);
}

function yamlString(s: string): string {
  // If the string is simple (no special chars), return unquoted
  if (/^[a-zA-Z0-9_\-./ ]+$/.test(s) && s.length > 0 && !s.startsWith(" ")) {
    return s;
  }
  // Escape backslashes and double quotes, wrap in double quotes
  const escaped = s.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  return `"${escaped}"`;
}

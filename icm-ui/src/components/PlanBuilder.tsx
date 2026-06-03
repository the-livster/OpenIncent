import { useCallback, useState } from "react";
import { savePlan } from "../api";

interface Rule {
  type: string;
  id: string;
  filter?: string;
  rate?: string;
  tiers?: { threshold: string; rate: string }[];
  threshold_pct?: string;
  multiplier?: string;
}

interface PlanData {
  plan_id: string;
  name: string;
  period_type: string;
  currency: string;
  rules: Rule[];
}

interface Props {
  plan: PlanData;
  onClose: () => void;
  onUse?: (yaml: string) => void;
}

export default function PlanBuilder({ plan: initialPlan, onClose, onUse }: Props) {
  const [plan, setPlan] = useState<PlanData>(structuredClone(initialPlan));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  const updatePlan = useCallback((fn: (p: PlanData) => PlanData) => {
    setPlan(prev => fn(structuredClone(prev)));
    setSaved(false);
  }, []);

  const updateRule = useCallback((idx: number, fn: (r: Rule) => Rule) => {
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
    <div className="max-w-3xl mx-auto space-y-6 animate-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-ink">Plan Builder</h2>
        <div className="flex items-center gap-2">
          <button onClick={onClose} className="text-xs text-ink2 hover:text-ink cursor-pointer transition-colors">
            ← Back to AI Builder
          </button>
        </div>
      </div>

      {/* Plan settings */}
      <div className="card p-5 space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-ink2 mb-1">Plan Name</label>
            <input
              type="text"
              value={plan.name}
              onChange={e => updatePlan(p => ({ ...p, name: e.target.value }))}
              className="w-full px-3 py-2 rounded-lg text-sm bg-soft border border-line text-ink focus:outline-none focus:border-accent transition-all"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-ink2 mb-1">Period</label>
            <select
              value={plan.period_type}
              onChange={e => updatePlan(p => ({ ...p, period_type: e.target.value }))}
              className="w-full px-3 py-2 rounded-lg text-sm bg-soft border border-line text-ink focus:outline-none focus:border-accent transition-all"
            >
              <option value="monthly">Monthly</option>
              <option value="quarterly">Quarterly</option>
              <option value="annual">Annual</option>
            </select>
          </div>
        </div>
      </div>

      {/* Rules */}
      {plan.rules.map((rule, ri) => (
        <div key={ri} className="card p-5 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <select
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
                className="px-3 py-1.5 rounded-lg text-sm font-medium bg-soft border border-line text-ink focus:outline-none focus:border-accent transition-all"
              >
                <option value="flat_rate">Flat Rate</option>
                <option value="tiered">Tiered</option>
                <option value="accelerator">Accelerator</option>
              </select>
              <span className="text-xs text-ink2 font-mono">{rule.id}</span>
            </div>
            {plan.rules.length > 1 && (
              <button onClick={() => removeRule(ri)} className="text-xs text-ink2 hover:text-danger cursor-pointer transition-colors">
                Remove
              </button>
            )}
          </div>

          {/* Flat rate / base rate */}
          {(rule.type === "flat_rate" || rule.type === "tiered") && (
            <SliderField
              label="Base Rate"
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
                <label className="text-xs font-medium text-ink2">Tiers</label>
                <button onClick={() => addTier(ri)} className="text-xs text-accent hover:text-brand-300 cursor-pointer transition-colors">
                  + Add Tier
                </button>
              </div>
              {rule.tiers.map((tier, ti) => (
                <div key={ti} className="flex items-center gap-2">
                  <span className="text-xs text-ink2 w-4">{ti + 1}</span>
                  <div className="flex-1 grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[10px] text-ink2">Threshold ($)</label>
                      <input
                        type="number"
                        value={parseFloat(tier.threshold)}
                        onChange={e => updateRule(ri, r => {
                          if (r.tiers) r.tiers[ti].threshold = e.target.value;
                          return r;
                        })}
                        className="w-full px-2 py-1 rounded text-xs bg-soft border border-line text-ink focus:outline-none focus:border-accent transition-all"
                      />
                    </div>
                    <div>
                      <label className="text-[10px] text-ink2">Rate (%)</label>
                      <input
                        type="number"
                        step="0.1"
                        value={parseFloat(tier.rate) * 100}
                        onChange={e => updateRule(ri, r => {
                          if (r.tiers) r.tiers[ti].rate = String(parseFloat(e.target.value || "0") / 100);
                          return r;
                        })}
                        className="w-full px-2 py-1 rounded text-xs bg-soft border border-line text-ink focus:outline-none focus:border-accent transition-all"
                      />
                    </div>
                  </div>
                  {rule.tiers!.length > 1 && (
                    <button onClick={() => removeTier(ri, ti)} className="text-xs text-ink2 hover:text-danger cursor-pointer transition-colors">
                      ✕
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Accelerator */}
          {rule.type === "accelerator" && (
            <div className="space-y-3">
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
          <div>
            <label className="block text-xs font-medium text-ink2 mb-1">
              Filter <span className="text-ink2">(optional)</span>
            </label>
            <input
              type="text"
              value={rule.filter || ""}
              onChange={e => updateRule(ri, r => { r.filter = e.target.value || undefined; return r; })}
              placeholder='e.g. product == "Enterprise"'
              className="w-full px-3 py-2 rounded-lg text-sm font-mono bg-soft border border-line text-ink placeholder:text-ink2 focus:outline-none focus:border-accent transition-all"
            />
          </div>
        </div>
      ))}

      {/* Add rule button */}
      <button
        onClick={addRule}
        className="w-full py-3 rounded-xl border-2 border-dashed border-surface-300 text-ink2 hover:border-accent hover:text-accent text-sm font-medium transition-all cursor-pointer"
      >
        + Add Rule
      </button>

      {/* YAML preview */}
      <details className="card overflow-hidden">
        <summary className="px-5 py-3 text-sm font-medium text-ink2 cursor-pointer hover:text-ink transition-colors">
          Preview YAML
        </summary>
        <div className="px-5 pb-4 overflow-x-auto">
          <pre className="text-xs text-ink2 font-mono whitespace-pre">{generateYaml(plan)}</pre>
        </div>
      </details>

      {/* Save */}
      <div className="flex justify-end gap-2">
        {error && (
          <div className="flex-1 px-3 py-2 rounded-lg bg-danger/10 border border-danger/30 text-danger text-xs select-text">
            {error}
          </div>
        )}
        <button onClick={onClose} className="px-4 py-2 rounded-lg text-sm text-ink2 hover:text-ink cursor-pointer transition-colors">
          Cancel
        </button>
        {onUse && (
          <button
            onClick={() => onUse(generateYaml(plan))}
            className="px-6 py-2 rounded-lg text-sm font-medium bg-accent/15 text-accent hover:bg-accent/25 transition-all cursor-pointer"
          >
            Use This Plan
          </button>
        )}
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-6 py-2 rounded-lg text-sm font-medium bg-accent text-white hover:bg-brand-400 disabled:opacity-40 transition-all cursor-pointer"
        >
          {saving ? "Saving..." : saved ? "✓ Saved!" : "Save to Library"}
        </button>
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
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className="text-xs font-medium text-ink2">{label}</label>
        <span className="text-xs font-mono text-ink font-semibold">
          {value.toFixed(step < 1 ? 1 : 0)}{unit}
        </span>
      </div>
      <input
        type="range"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="w-full h-2 rounded-full appearance-none bg-surface-200 accent-brand-500 cursor-pointer"
      />
    </div>
  );
}

// ------------------------------------------------------------------
// YAML generator (client-side mirror of server serialization)
// ------------------------------------------------------------------

function generateYaml(plan: PlanData): string {
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
  if (/^[a-zA-Z0-9_\-.\/ ]+$/.test(s) && s.length > 0 && !s.startsWith(" ")) {
    return s;
  }
  // Escape backslashes and double quotes, wrap in double quotes
  const escaped = s.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  return `"${escaped}"`;
}

import { useCallback, useState } from "react";
import { generatePlan } from "../api";
import PlanBuilder, { type PlanBuilderPlanData } from "./PlanBuilder";

interface Props {
  onPlanGenerated: (plan: { yaml: string; name: string }) => void;
}

export default function PlanGenerator({ onPlanGenerated }: Props) {
  const [description, setDescription] = useState("");
  const [planId, setPlanId] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [error, setError] = useState("");
  const [generatedPlan, setGeneratedPlan] = useState<Record<string, unknown> | null>(null);
  const [generatedYaml, setGeneratedYaml] = useState("");
  const [showBuilder, setShowBuilder] = useState(false);

  const generate = useCallback(async () => {
    if (!description.trim()) return;
    setStatus("loading");
    setError("");
    setGeneratedYaml("");
    setGeneratedPlan(null);
    try {
      const result = await generatePlan({
        description,
        plan_id: planId || undefined,
      });
      setGeneratedYaml(result.yaml);
      setGeneratedPlan(result.plan);
      setStatus("success");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Generation failed");
      setStatus("error");
    }
  }, [description, planId]);

  const downloadYaml = () => {
    const blob = new Blob([generatedYaml], { type: "text/yaml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${planId || "plan"}.yaml`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  // If builder is open, show it instead
  if (showBuilder && generatedPlan) {
    return (
      <PlanBuilder
        plan={generatedPlan as unknown as PlanBuilderPlanData}
        onClose={() => setShowBuilder(false)}
      />
    );
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6 animate-in">
      <div className="card p-6 space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-ink">AI Plan Builder</h2>
          <p className="text-sm text-ink2 mt-1">
            Describe your commission plan in plain English. We'll generate it and let you tune it with sliders.
          </p>
        </div>

        <div>
          <label className="block text-xs font-medium text-ink2 mb-1.5">
            Plan Description
          </label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="e.g. 5% flat commission on all closed deals. Above 100% quota, pay 2x the rate. Enterprise products get an extra 2% bonus."
            rows={4}
            className="w-full px-3 py-2.5 rounded-lg text-sm bg-soft border border-line text-ink placeholder:text-ink2 focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all resize-y"
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-ink2 mb-1.5">
            Plan ID <span className="text-ink2">(optional)</span>
          </label>
          <input
            type="text"
            value={planId}
            onChange={(e) => setPlanId(e.target.value)}
            placeholder="my_sales_plan"
            className="w-full px-3 py-2 rounded-lg text-sm font-mono bg-soft border border-line text-ink placeholder:text-ink2 focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all"
          />
        </div>

        <button
          onClick={generate}
          disabled={!description.trim() || status === "loading"}
          className="w-full py-2.5 rounded-lg font-medium text-sm bg-gradient-to-r from-accent to-accent-ink text-white hover:from-accent hover:to-accent disabled:opacity-40 disabled:cursor-not-allowed transition-all cursor-pointer shadow-sm"
        >
          {status === "loading" ? (
            <span className="flex items-center justify-center gap-2">
              <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              Generating...
            </span>
          ) : (
            "Generate Plan ✨"
          )}
        </button>
      </div>

      {status === "error" && (
        <div className="px-4 py-3 rounded-xl bg-danger/10 border border-danger/30 text-danger text-sm animate-in select-text">
          {error}
        </div>
      )}

      {status === "success" && generatedYaml && (
        <div className="card overflow-hidden animate-in">
          <div className="flex items-center justify-between px-4 py-3 border-b border-line">
            <h3 className="text-sm font-semibold text-ink">Plan Generated</h3>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowBuilder(true)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-accent text-white hover:bg-brand-400 transition-colors cursor-pointer"
              >
                Tune with Sliders
              </button>
              <button
                onClick={() => onPlanGenerated({ yaml: generatedYaml, name: planId || "generated_plan" })}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-accent/15 text-accent hover:bg-accent/25 transition-colors cursor-pointer"
              >
                + Save to Library
              </button>
              <button
                onClick={downloadYaml}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-accent/15 text-accent hover:bg-accent/25 transition-colors cursor-pointer"
              >
                ↓ Download YAML
              </button>
            </div>
          </div>
          <div className="p-4 overflow-x-auto">
            <pre className="audit-panel">{generatedYaml}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

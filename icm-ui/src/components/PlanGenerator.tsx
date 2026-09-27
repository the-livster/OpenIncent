import { useCallback, useState } from "react";
import { generatePlan } from "../api";
import PlanBuilder, { type PlanBuilderPlanData } from "./PlanBuilder";
import { Button, Callout, Card, CardHeader, PageHeader } from "./ui";

interface Props {
  onPlanGenerated: (plan: { yaml: string; name: string }) => void;
}

const EXAMPLES = [
  "5% flat commission on all closed deals. Above 100% of quota, pay 2x the rate.",
  "Recruiters earn 10% of placement fees up to target, 15% above it.",
  "SDRs earn 3% of sourced pipeline, but only once they reach 50% of quota.",
];

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
    <div className="animate-in">
      <PageHeader
        title="AI Builder"
        description="Describe your commission plan in plain English. It is drafted as a validated plan you can tune, save, and check before it pays anyone."
      />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <Card>
          <div className="space-y-4 p-5">
            <label className="block">
              <span className="field-label">Plan description</span>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="e.g. 5% flat commission on all closed deals. Above 100% quota, pay 2x the rate. Enterprise products get an extra 2% bonus."
                rows={6}
                className="w-full resize-y"
              />
            </label>
            <label className="block">
              <span className="field-label">Plan ID <span className="font-normal text-ink-3">(optional)</span></span>
              <input
                type="text"
                value={planId}
                onChange={(e) => setPlanId(e.target.value)}
                placeholder="my_sales_plan"
                className="w-full font-mono"
              />
            </label>
            <Button
              variant="primary"
              size="lg"
              icon="sparkles"
              className="w-full"
              onClick={generate}
              disabled={!description.trim() || status === "loading"}
              loading={status === "loading"}
            >
              {status === "loading" ? "Generating..." : "Generate plan"}
            </Button>
          </div>
        </Card>

        <Card className="h-fit">
          <CardHeader title="Try an example" description="Click one to start from it." />
          <ul className="space-y-2 p-4">
            {EXAMPLES.map(text => (
              <li key={text}>
                <button
                  onClick={() => setDescription(text)}
                  className="w-full rounded-lg border border-line px-3 py-2.5 text-left text-[12.5px] text-ink-2 transition-colors hover:border-accent/40 hover:bg-accent-soft hover:text-ink"
                >
                  {text}
                </button>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      {status === "error" && <Callout tone="danger" className="mt-5">{error}</Callout>}

      {status === "success" && generatedYaml && (
        <Card className="mt-5 overflow-hidden animate-in">
          <CardHeader
            icon="checkCircle"
            title="Plan generated"
            description="Review the YAML, tune it with sliders, then save it to your library."
            actions={
              <>
                <Button size="sm" variant="primary" icon="sliders" onClick={() => setShowBuilder(true)}>Tune with sliders</Button>
                <Button size="sm" icon="plus" onClick={() => onPlanGenerated({ yaml: generatedYaml, name: planId || "generated_plan" })}>
                  Save to library
                </Button>
                <Button size="sm" icon="download" onClick={downloadYaml}>Download YAML</Button>
              </>
            }
          />
          <div className="p-4">
            <pre className="audit-panel">{generatedYaml}</pre>
          </div>
        </Card>
      )}
    </div>
  );
}

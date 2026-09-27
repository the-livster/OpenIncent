import { useCallback, useEffect, useState } from "react";
import { calculate, listPlans, exportSavedStatements, previewFile } from "../api";
import PlanBuilder, { type PlanBuilderPlanData } from "./PlanBuilder";
import DropZone from "./DropZone";
import CalcResultsView from "./CalcResultsView";
import { Button, Callout, Card, CardHeader, EmptyState, Icon, PageHeader, Segmented } from "./ui";
import { cx } from "./ui/cx";
import { parseCsvPreview } from "./csvParser";
import type { CalculateResponse, SavedPlan } from "../types";

// ------------------------------------------------------------------
// Types
// ------------------------------------------------------------------

interface ColumnMapping {
  payeeColumn: string;
  amountColumn: string;
  dateColumn: string;
  productColumn: string;
}

type Step = "data" | "map" | "payees" | "plan" | "results";

const FIELD_LABELS: Record<keyof ColumnMapping, string> = {
  payeeColumn: "Rep / Payee",
  amountColumn: "Deal Amount",
  dateColumn: "Close Date",
  productColumn: "Product (optional)",
};

// ------------------------------------------------------------------
// Component
// ------------------------------------------------------------------

interface Props {
  loadedPlan: { yaml: string; name: string } | null;
  onPlanConsumed: () => void;
}

export default function CalculatorWizard({ loadedPlan, onPlanConsumed }: Props) {
  // Step 1: Data
  const [txnFile, setTxnFile] = useState<File | null>(null);
  const [csvPreview, setCsvPreview] = useState<{ headers: string[]; rows: string[][] } | null>(null);

  // Step 2: Column mapping
  const [mapping, setMapping] = useState<ColumnMapping>({
    payeeColumn: "",
    amountColumn: "",
    dateColumn: "",
    productColumn: "",
  });

  // Step 3: Payees
  const [payeeFile, setPayeeFile] = useState<File | null>(null);
  const [payeePreview, setPayeePreview] = useState<{ headers: string[]; rows: string[][] } | null>(null);

  // Step 4: Plan
  const [plans, setPlans] = useState<SavedPlan[]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [planFile, setPlanFile] = useState<File | null>(null);
  const [planSource, setPlanSource] = useState<"library" | "file" | "build">("library");

  // Step 5: Results
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [error, setError] = useState("");
  // Set when a run was blocked only because some ids are off the roster;
  // without a way to proceed, the desktop app would simply be stuck.
  const [unknownPayeesBlocked, setUnknownPayeesBlocked] = useState(false);
  const [allowUnknownPayees, setAllowUnknownPayees] = useState(false);
  const [data, setData] = useState<CalculateResponse | null>(null);

  const [step, setStep] = useState<Step>("data");

  // Load saved plans for step 4
  useEffect(() => {
    listPlans().then(setPlans).catch(() => {});
  }, []);

  // Handle plan loaded from library
  useEffect(() => {
    if (loadedPlan) {
      const file = new File([loadedPlan.yaml], `${loadedPlan.name}.yaml`, { type: "text/yaml" });
      setPlanFile(file);
      setPlanSource("file");
      onPlanConsumed();
    }
  }, [loadedPlan, onPlanConsumed]);

  // Parse CSV or call preview API when file changes
  useEffect(() => {
    if (!txnFile) { setCsvPreview(null); return; }
    if (txnFile.name.endsWith(".csv")) {
      const reader = new FileReader();
      reader.onload = () => {
        const preview = parseCsvPreview(reader.result as string);
        setCsvPreview(preview);
        const headers = preview.headers;
        setMapping({
          payeeColumn: headers.find(h => /rep|payee|agent|name|sales/i.test(h)) || "",
          amountColumn: headers.find(h => /amount|acv|value|revenue|total|price/i.test(h)) || "",
          dateColumn: headers.find(h => /date|close|period/i.test(h)) || "",
          productColumn: headers.find(h => /product|type|tier|plan/i.test(h)) || "",
        });
      };
      reader.readAsText(txnFile);
    } else {
      // XLSX — ask server for preview
      previewFile(txnFile).then(preview => {
        setCsvPreview({ headers: preview.headers, rows: preview.preview_rows });
        const rev: Record<string, string> = {};
        for (const [src, tgt] of Object.entries(preview.mapping)) {
          rev[tgt] = src;
        }
        setMapping({
          payeeColumn: rev["payee_id"] || "",
          amountColumn: rev["amount"] || "",
          dateColumn: rev["close_date"] || "",
          productColumn: rev["product"] || "",
        });
      }).catch(() => {
        setCsvPreview(null);
      });
    }
  }, [txnFile]);

  // Parse payee file for preview
  useEffect(() => {
    if (!payeeFile) { setPayeePreview(null); return; }
    previewFile(payeeFile, "payees").then(preview => {
      setPayeePreview({ headers: preview.headers, rows: preview.preview_rows });
    }).catch(() => {
      setPayeePreview(null);
    });
  }, [payeeFile]);

  // Build the file to send: for XLSX pass through, for CSV pass original
  // (the server-side fuzzy mapper handles column mapping)
  const buildMappedFile = useCallback((): File | null => {
    return txnFile;
  }, [txnFile]);

  // Build payees CSV from rep names in the data
  const buildAutoPayees = useCallback((): File => {
    if (!csvPreview || !mapping.payeeColumn) return new File([], "empty.csv");
    const payeeIdx = csvPreview.headers.indexOf(mapping.payeeColumn);
    if (payeeIdx < 0) return new File([], "empty.csv");
    const names = new Set(csvPreview.rows.map(r => r[payeeIdx]).filter(Boolean));
    let csv = "id,name,quota,plan_id,effective_from\n";
    let i = 1;
    for (const name of names) {
      csv += `P${String(i).padStart(3, "0")},${name},100000,auto,2026-01-01\n`;
      i++;
    }
    return new File([csv], "payees.csv", { type: "text/csv" });
  }, [csvPreview, mapping.payeeColumn]);

  // Run calculation
  const run = useCallback(async () => {
    let plan: File | null | undefined = planFile;
    if (planSource === "library" && selectedPlanId) {
      const found = plans.find(p => p.id === selectedPlanId);
      if (!found) {
        setError("Selected plan no longer exists. Please select another.");
        setStatus("error");
        return;
      }
      plan = new File([found.yaml_content], "plan.yaml", { type: "text/yaml" });
    }
    const txns = buildMappedFile();
    const pees = payeeFile || buildAutoPayees();

    if (!plan || !txns) return;

    setStatus("loading");
    setError("");
    try {
      const result = await calculate({
        plan,
        transactions: txns,
        payees: pees,
        allowUnknownPayees,
      });
      setData(result);
      setStatus("success");
      setStep("results");
    } catch (e: unknown) {
      const codes = (e as { codes?: string[] })?.codes ?? [];
      setUnknownPayeesBlocked(codes.includes("unknown_payee"));
      setError(e instanceof Error ? e.message : "Calculation failed");
      setStatus("error");
    }
  }, [planSource, selectedPlanId, plans, planFile, payeeFile, buildMappedFile, buildAutoPayees, allowUnknownPayees]);

  const [exportMessage, setExportMessage] = useState("");
  const handleExport = useCallback(async () => {
    if (!data) return;
    try {
      const savedTo = await exportSavedStatements(data);
      setExportMessage(savedTo ? "Statements saved to " + savedTo : "Statements downloaded.");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Export failed");
    }
  }, [data]);

  const stepIndex = ["data", "map", "payees", "plan", "results"].indexOf(step);

  return (
    <div className="space-y-5">
      <PageHeader
        title="Quick Calc"
        description="A one-off calculation: drop a sales file, map its columns, pick a plan. Nothing here changes your saved roster."
      />

      {exportMessage && <Callout tone="success" role="status">{exportMessage}</Callout>}

      {/* Step indicator */}
      {step !== "results" && (
        <ol aria-label="Quick Calc steps" className="flex flex-wrap items-center gap-1.5">
          {STEP_LABELS.map((label, i) => (
            <li key={label} className="flex items-center gap-1.5">
              {i > 0 && <Icon name="chevronRight" size={14} className="text-line-strong" />}
              <span
                aria-current={i === stepIndex ? "step" : undefined}
                className={cx(
                  "inline-flex items-center gap-2 rounded-full py-1 pl-1 pr-3 text-[12.5px] font-medium",
                  i === stepIndex && "bg-accent-soft text-accent-ink",
                  i < stepIndex && "text-ink",
                  i > stepIndex && "text-ink-3",
                )}
              >
                <span aria-hidden="true" className={cx(
                  "grid h-5 w-5 place-items-center rounded-full text-[10.5px] font-semibold num",
                  i === stepIndex ? "bg-accent text-on-accent" : i < stepIndex ? "bg-success-soft text-success-ink" : "bg-surface-3 text-ink-2",
                )}>
                  {i < stepIndex ? <Icon name="check" size={11} strokeWidth={2.8} /> : i + 1}
                </span>
                {label}
              </span>
            </li>
          ))}
        </ol>
      )}

      {/* Step 1: Upload Data */}
      {step === "data" && (
        <WizardCard
          title="Upload sales data"
          description="Drop your sales spreadsheet, CSV or Excel. Columns are detected automatically."
          footer={<Button variant="primary" iconRight="arrowRight" onClick={() => setStep("map")} disabled={!txnFile}>Map columns</Button>}
        >
          <DropZone file={txnFile} onChange={setTxnFile} accept=".csv,.xlsx" label="Sales transactions" icon="table" />

          {csvPreview && (
            <div>
              <p className="mb-2 text-[12.5px] text-ink-2">
                Detected {csvPreview.headers.length} columns, {csvPreview.rows.length} rows (showing the first 50)
              </p>
              <div className="table-wrap max-h-80 rounded-xl border border-line">
                <table>
                  <thead>
                    <tr>{csvPreview.headers.map(h => <th key={h}>{h}</th>)}</tr>
                  </thead>
                  <tbody>
                    {csvPreview.rows.slice(0, 50).map((row, ri) => (
                      <tr key={ri}>
                        {row.map((cell, ci) => (
                          <td key={ci} className="max-w-[200px] truncate whitespace-nowrap text-ink-2">{cell}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {txnFile && !csvPreview && (
            <p className="text-[12.5px] text-ink-2">Excel file detected — column mapping will happen automatically on the server.</p>
          )}
        </WizardCard>
      )}

      {/* Step 2: Map Columns */}
      {step === "map" && (
        <WizardCard
          title="Map columns"
          description="Tell us what each column represents. We guessed from your headers — adjust if needed."
          onBack={() => setStep("data")}
          footer={
            <Button variant="primary" iconRight="arrowRight" onClick={() => setStep("payees")}
              disabled={!mapping.payeeColumn || !mapping.amountColumn}>
              Payees
            </Button>
          }
        >
          <div className="grid gap-4 sm:grid-cols-2">
            {(Object.keys(FIELD_LABELS) as (keyof ColumnMapping)[]).map(field => (
              <label key={field} className="block">
                <span className="field-label">{FIELD_LABELS[field]}</span>
                {csvPreview ? (
                  <select
                    value={mapping[field]}
                    onChange={e => setMapping(prev => ({ ...prev, [field]: e.target.value }))}
                    className="w-full"
                  >
                    <option value="">Select a column</option>
                    {csvPreview.headers.map(h => (
                      <option key={h} value={h}>{h}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    type="text"
                    value={mapping[field]}
                    onChange={e => setMapping(prev => ({ ...prev, [field]: e.target.value }))}
                    placeholder="Type column name"
                    className="w-full"
                  />
                )}
              </label>
            ))}
          </div>
          {!csvPreview && (
            <p className="text-[12.5px] text-ink-2">Excel file detected — the server will auto-map columns. Type names above to override.</p>
          )}
        </WizardCard>
      )}

      {/* Step 3: Payees */}
      {step === "payees" && (
        <WizardCard
          title="Payees"
          description="Upload a payee roster, or skip it to generate one from the rep names in your data."
          onBack={() => setStep("map")}
          footer={<Button variant="primary" iconRight="arrowRight" onClick={() => setStep("plan")}>Select plan</Button>}
        >
          <DropZone file={payeeFile} onChange={setPayeeFile} accept=".csv,.xlsx" label="Payee roster (optional)" icon="users" />

          {!payeeFile && csvPreview && mapping.payeeColumn && (
            <Callout tone="info">
              {(() => {
                const idx = csvPreview.headers.indexOf(mapping.payeeColumn);
                const count = idx >= 0 ? new Set(csvPreview.rows.map(r => r[idx]).filter(Boolean)).size : 0;
                return `Will auto-generate ${count} payee${count !== 1 ? "s" : ""} from the "${mapping.payeeColumn}" column.`;
              })()}
            </Callout>
          )}

          {payeePreview && (
            <div>
              <p className="mb-2 text-[12.5px] text-ink-2">Detected {payeePreview.headers.length} columns</p>
              <div className="table-wrap max-h-72 rounded-xl border border-line">
                <table>
                  <thead>
                    <tr>{payeePreview.headers.map(h => <th key={h}>{h}</th>)}</tr>
                  </thead>
                  <tbody>
                    {payeePreview.rows.map((row, ri) => (
                      <tr key={ri}>
                        {row.map((cell, ci) => (
                          <td key={ci} className="max-w-[200px] truncate whitespace-nowrap text-ink-2">{cell}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </WizardCard>
      )}

      {/* Step 4: Select Plan */}
      {step === "plan" && (
        <WizardCard
          title="Select plan"
          description="Pick a saved plan from your library, upload a YAML file, or build one now."
          onBack={() => setStep("payees")}
          footer={
            <Button variant="primary" icon="play" onClick={run} disabled={!(selectedPlanId || planFile) || status === "loading"}
              loading={status === "loading"}>
              Calculate commissions
            </Button>
          }
        >
          <Segmented
            label="Plan source"
            value={planSource}
            onChange={setPlanSource}
            options={[
              { value: "library", label: "Library", icon: "layers" },
              { value: "file", label: "Upload file", icon: "upload" },
              { value: "build", label: "Build new", icon: "wand" },
            ]}
          />

          {planSource === "library" && (
            plans.length === 0 ? (
              <EmptyState icon="fileText" compact title="No saved plans"
                description="Switch to Upload file, or build one in the AI Builder." />
            ) : (
              <div className="grid max-h-72 gap-2 overflow-y-auto sm:grid-cols-2">
                {plans.map(p => (
                  <button
                    key={p.id}
                    onClick={() => setSelectedPlanId(p.id)}
                    aria-pressed={selectedPlanId === p.id}
                    className={cx(
                      "flex items-start gap-3 rounded-xl border px-3.5 py-3 text-left transition-colors",
                      selectedPlanId === p.id ? "border-accent bg-accent-soft" : "border-line hover:bg-surface-2",
                    )}
                  >
                    <Icon name={selectedPlanId === p.id ? "checkCircle" : "fileText"}
                      className={selectedPlanId === p.id ? "mt-0.5 text-accent-ink" : "mt-0.5 text-ink-3"} />
                    <span className="min-w-0">
                      <span className="block truncate text-[13.5px] font-medium text-ink">{p.name}</span>
                      {p.description && <span className="mt-0.5 block text-[12.5px] text-ink-2">{p.description}</span>}
                    </span>
                  </button>
                ))}
              </div>
            )
          )}

          {planSource === "file" && (
            <DropZone file={planFile} onChange={setPlanFile} accept=".yaml,.yml" label="Plan YAML" icon="fileText" />
          )}

          {planSource === "build" && (
            <PlanBuilderWizard
              onUse={(yaml: string) => {
                setPlanFile(new File([yaml], "plan.yaml", { type: "text/yaml" }));
                setPlanSource("file");
              }}
              onCancel={() => setPlanSource("library")}
            />
          )}
        </WizardCard>
      )}

      {/* Error */}
      {status === "error" && (
        <Callout tone="danger">
          <div className="whitespace-pre-wrap">{error}</div>
          {unknownPayeesBlocked && (
            <label className="mt-3 flex items-start gap-2 border-t border-danger/20 pt-3 text-ink">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={allowUnknownPayees}
                onChange={(e) => setAllowUnknownPayees(e.target.checked)}
              />
              <span>
                Pay these ids anyway, as separate people. Only do this if they are
                genuinely not on the roster — a mistyped id will split one person's
                bookings in two and neither half will reach quota.
              </span>
            </label>
          )}
        </Callout>
      )}

      {/* Results */}
      {step === "results" && status === "success" && data && (
        <CalcResultsView
          data={data}
          onExport={handleExport}
          onStartNew={() => { setStep("data"); setStatus("idle"); setData(null); }}
        />
      )}
    </div>
  );
}

// ------------------------------------------------------------------
// Sub-components
// ------------------------------------------------------------------

const STEP_LABELS = ["Upload data", "Map columns", "Payees", "Select plan"];

function WizardCard({ title, description, children, footer, onBack }: {
  title: string; description: string; children: React.ReactNode; footer?: React.ReactNode; onBack?: () => void;
}) {
  return (
    <Card className="animate-in">
      <CardHeader title={title} description={description} />
      <div className="space-y-4 px-5 py-5">{children}</div>
      <div className="flex items-center justify-between gap-3 rounded-b-[var(--radius-card)] border-t border-line bg-surface-2/60 px-5 py-3.5">
        <div>{onBack && <Button variant="ghost" icon="arrowLeft" onClick={onBack}>Back</Button>}</div>
        <div>{footer}</div>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------
// Inline plan builder for wizard
// ------------------------------------------------------------------

const DEFAULT_PLAN: PlanBuilderPlanData = {
  plan_id: "my_plan",
  name: "My Commission Plan",
  period_type: "monthly",
  currency: "USD",
  rules: [{ type: "flat_rate", id: "R-001", rate: "0.05" }],
};

function PlanBuilderWizard({ onUse, onCancel }: { onUse: (yaml: string) => void; onCancel: () => void }) {
  return (
    <PlanBuilder
      plan={DEFAULT_PLAN}
      onClose={onCancel}
      onUse={onUse}
      embedded
    />
  );
}

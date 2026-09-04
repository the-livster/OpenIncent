import { useCallback, useEffect, useState } from "react";
import { calculate, listPlans, exportStatements, previewFile } from "../api";
import PlanBuilder, { type PlanBuilderPlanData } from "./PlanBuilder";
import DropZone from "./DropZone";
import CalcResultsView from "./CalcResultsView";
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

  // Export XLSX statements
  const handleExport = useCallback(async () => {
    let plan: File | null | undefined = planFile;
    if (planSource === "library" && selectedPlanId) {
      const found = plans.find(p => p.id === selectedPlanId);
      if (!found) {
        setError("Selected plan no longer exists. Please select another.");
        return;
      }
      plan = new File([found.yaml_content], "plan.yaml", { type: "text/yaml" });
    }
    const txns = buildMappedFile();
    const pees = payeeFile || buildAutoPayees();
    if (!plan || !txns) return;
    try {
      await exportStatements({ plan, transactions: txns, payees: pees });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Export failed");
    }
  }, [planSource, selectedPlanId, plans, planFile, payeeFile, buildMappedFile, buildAutoPayees]);

  const stepIndex = ["data", "map", "payees", "plan", "results"].indexOf(step);

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Step indicator */}
      {step !== "results" && (
        <div className="flex items-center gap-2">
          {["Upload Data", "Map Columns", "Payees", "Select Plan"].map((label, i) => (
            <div key={label} className="flex items-center gap-2">
              <div className={`
                flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium transition-all
                ${i < stepIndex ? "bg-accent/15 text-accent" : ""}
                ${i === stepIndex ? "bg-accent text-white" : ""}
                ${i > stepIndex ? "bg-soft text-ink2" : ""}
              `}>
                <span className={`
                  w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold
                  ${i < stepIndex ? "bg-accent text-white" : ""}
                  ${i === stepIndex ? "bg-white text-accent" : ""}
                  ${i > stepIndex ? "bg-soft text-ink2" : ""}
                `}>
                  {i < stepIndex ? "✓" : i + 1}
                </span>
                {label}
              </div>
              {i < 3 && <div className="w-4 h-px bg-line" />}
            </div>
          ))}
        </div>
      )}

      {/* Step 1: Upload Data */}
      {step === "data" && (
        <div className="card p-6 space-y-4 animate-in">
          <h2 className="text-lg font-semibold text-ink">Upload Sales Data</h2>
          <p className="text-sm text-ink2">
            Drop your sales spreadsheet — CSV or Excel. We will detect the columns automatically.
          </p>
          <DropZone
            file={txnFile}
            onChange={setTxnFile}
            accept=".csv,.xlsx"
            label="Sales transactions"
            icon="📊"
          />

          {csvPreview && (
            <div className="mt-4">
              <div className="text-xs font-medium text-ink2 mb-2">
                Detected {csvPreview.headers.length} columns, {csvPreview.rows.length} rows (showing first 50)
              </div>
              <div className="overflow-x-auto rounded-lg border border-line">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-soft">
                      {csvPreview.headers.map(h => (
                        <th key={h} className="px-2.5 py-1.5 text-left font-medium text-ink whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {csvPreview.rows.slice(0, 50).map((row, ri) => (
                      <tr key={ri}>
                        {row.map((cell, ci) => (
                          <td key={ci} className="px-2.5 py-1.5 text-ink2 whitespace-nowrap max-w-[200px] truncate">{cell}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {txnFile && !csvPreview && (
            <div className="text-xs text-ink2 mt-2">
              Excel file detected — column mapping will happen automatically on the server.
            </div>
          )}

          <div className="flex justify-end pt-2">
            <StepButton onClick={() => setStep("map")} disabled={!txnFile}>
              Next: Map Columns →
            </StepButton>
          </div>
        </div>
      )}

      {/* Step 2: Map Columns */}
      {step === "map" && (
        <div className="card p-6 space-y-4 animate-in">
          <h2 className="text-lg font-semibold text-ink">Map Columns</h2>
          <p className="text-sm text-ink2">
            Tell us what each column represents. We guessed based on your headers — adjust if needed.
          </p>

          <div className="space-y-3">
            {(Object.keys(FIELD_LABELS) as (keyof ColumnMapping)[]).map(field => (
              <div key={field}>
                <label className="block text-xs font-medium text-ink2 mb-1">
                  {FIELD_LABELS[field]}
                </label>
                {csvPreview ? (
                  <select
                    value={mapping[field]}
                    onChange={e => setMapping(prev => ({ ...prev, [field]: e.target.value }))}
                    className="w-full px-3 py-2 rounded-lg text-sm bg-soft border border-line text-ink focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all"
                  >
                    <option value="">-- Select column --</option>
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
                    className="w-full px-3 py-2 rounded-lg text-sm bg-soft border border-line text-ink placeholder:text-ink2 focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/10 transition-all"
                  />
                )}
              </div>
            ))}
            {!csvPreview && (
              <div className="text-xs text-ink2 italic pt-1">
                Excel file detected — the server will auto-map columns. Type names above to override.
              </div>
            )}
          </div>

          <div className="flex justify-between pt-2">
            <TextButton onClick={() => setStep("data")}>← Back</TextButton>
            <StepButton
              onClick={() => setStep("payees")}
              disabled={!mapping.payeeColumn || !mapping.amountColumn}
            >
              Next: Payees →
            </StepButton>
          </div>
        </div>
      )}

      {/* Step 3: Payees */}
      {step === "payees" && (
        <div className="card p-6 space-y-4 animate-in">
          <h2 className="text-lg font-semibold text-ink">Payees</h2>
          <p className="text-sm text-ink2">
            Upload a payee roster, or skip to auto-generate one from the rep names in your data.
          </p>

          <DropZone
            file={payeeFile}
            onChange={setPayeeFile}
            accept=".csv,.xlsx"
            label="Payee roster (optional)"
            icon="👥"
          />

          {!payeeFile && csvPreview && mapping.payeeColumn && (
            <div className="px-3 py-2 rounded-lg bg-soft text-xs text-ink2">
              {(() => {
                const idx = csvPreview.headers.indexOf(mapping.payeeColumn);
                const count = idx >= 0 ? new Set(csvPreview.rows.map(r => r[idx]).filter(Boolean)).size : 0;
                return `Will auto-generate ${count} payee${count !== 1 ? "s" : ""} from the "${mapping.payeeColumn}" column.`;
              })()}
            </div>
          )}

          {payeePreview && (
            <div className="mt-2">
              <div className="text-xs font-medium text-ink2 mb-2">
                Detected {payeePreview.headers.length} columns
              </div>
              <div className="overflow-x-auto rounded-lg border border-line">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-soft">
                      {payeePreview.headers.map(h => (
                        <th key={h} className="px-2.5 py-1.5 text-left font-medium text-ink whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {payeePreview.rows.map((row, ri) => (
                      <tr key={ri}>
                        {row.map((cell, ci) => (
                          <td key={ci} className="px-2.5 py-1.5 text-ink2 whitespace-nowrap max-w-[200px] truncate">{cell}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="flex justify-between pt-2">
            <TextButton onClick={() => setStep("map")}>← Back</TextButton>
            <StepButton onClick={() => setStep("plan")}>
              Next: Select Plan →
            </StepButton>
          </div>
        </div>
      )}

      {/* Step 4: Select Plan */}
      {step === "plan" && (
        <div className="card p-6 space-y-4 animate-in">
          <h2 className="text-lg font-semibold text-ink">Select Plan</h2>
          <p className="text-sm text-ink2">
            Pick a saved plan from your library, or upload a YAML file.
          </p>

          {/* Toggle source */}
          <div className="flex gap-1 bg-soft rounded-lg p-1 w-fit">
            <SourceToggle active={planSource === "library"} onClick={() => setPlanSource("library")} label="Library" />
            <SourceToggle active={planSource === "file"} onClick={() => setPlanSource("file")} label="Upload File" />
            <SourceToggle active={planSource === "build"} onClick={() => setPlanSource("build")} label="Build New" />
          </div>

          {planSource === "library" && (
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {plans.length === 0 && (
                <div className="text-sm text-ink2 py-4 text-center">
                  No saved plans. Switch to Upload File, or build one in the AI Builder tab.
                </div>
              )}
              {plans.map(p => (
                <button
                  key={p.id}
                  onClick={() => setSelectedPlanId(p.id)}
                  className={`
                    w-full text-left px-4 py-3 rounded-lg border transition-all cursor-pointer
                    ${selectedPlanId === p.id
                      ? "border-accent bg-accent/10"
                      : "border-line bg-soft hover:border-ink2"
                    }
                  `}
                >
                  <div className="text-sm font-medium text-ink">{p.name}</div>
                  {p.description && <div className="text-xs text-ink2 mt-0.5">{p.description}</div>}
                </button>
              ))}
            </div>
          )}

          {planSource === "file" && (
            <DropZone
              file={planFile}
              onChange={setPlanFile}
              accept=".yaml,.yml"
              label="Plan YAML"
              icon="📋"
            />
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

          <div className="flex justify-between pt-2">
            <TextButton onClick={() => setStep("payees")}>← Back</TextButton>
            <StepButton
              onClick={run}
              disabled={!(selectedPlanId || planFile)}
              highlight
            >
              Calculate Commissions ✨
            </StepButton>
          </div>
        </div>
      )}

      {/* Error */}
      {status === "error" && (
        <div className="px-4 py-3 rounded-xl bg-danger/10 border border-danger/20 text-danger text-sm animate-in select-text space-y-3">
          <div className="whitespace-pre-wrap">{error}</div>
          {unknownPayeesBlocked && (
            <label className="flex items-start gap-2 pt-2 border-t border-danger/20 cursor-pointer">
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
        </div>
      )}

      {/* Loading */}
      {status === "loading" && (
        <div className="text-center py-12 animate-in">
          <div className="inline-block w-8 h-8 border-3 border-accent/30 border-t-accent rounded-full animate-spin mb-3" />
          <p className="text-ink2 text-sm">Calculating commissions...</p>
        </div>
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

function StepButton({ onClick, disabled, children, highlight }: {
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
  highlight?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`
        px-5 py-2 rounded-lg text-sm font-medium transition-all cursor-pointer
        ${highlight
          ? "bg-gradient-to-r from-accent to-accent-ink text-white shadow-sm hover:from-accent hover:to-accent"
          : "bg-accent text-white hover:bg-accent"
        }
        disabled:opacity-40 disabled:cursor-not-allowed
      `}
    >
      {children}
    </button>
  );
}

function TextButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className="text-sm text-ink2 hover:text-ink transition-colors cursor-pointer">
      {children}
    </button>
  );
}

function SourceToggle({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className={`
        px-3 py-1.5 rounded-md text-xs font-medium transition-all cursor-pointer
        ${active ? "bg-white text-ink shadow-sm" : "text-ink2 hover:text-ink"}
      `}
    >
      {label}
    </button>
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
    />
  );
}


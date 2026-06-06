import { useCallback, useEffect, useState } from "react";
import { calculate, listPlans, exportStatements, previewFile } from "../api";
import PlanBuilder from "./PlanBuilder";
import PayeeTrace from "./PayeeTrace";
import type { CalculateResponse, SavedPlan } from "../types";

// ------------------------------------------------------------------
// Simple client-side CSV parser
// ------------------------------------------------------------------

function parseCsvPreview(text: string): { headers: string[]; rows: string[][] } {
  const lines = text.trim().split(/\r?\n/);
  if (lines.length === 0) return { headers: [], rows: [] };
  const headers = parseCsvLine(lines[0]);
  const rows = lines.slice(1, 6).map(parseCsvLine);
  return { headers, rows };
}

function parseCsvLine(line: string): string[] {
  const cells: string[] = [];
  let current = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQuotes) {
      if (c === '"') {
        if (i + 1 < line.length && line[i + 1] === '"') {
          current += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        current += c;
      }
    } else {
      if (c === '"') {
        inQuotes = true;
      } else if (c === ",") {
        cells.push(current.trim());
        current = "";
      } else {
        current += c;
      }
    }
  }
  cells.push(current.trim());
  return cells;
}

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
  const [planSource, setPlanSource] = useState<"library" | "file" | "build" | "auto">("auto");

  // Step 5: Results
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [error, setError] = useState("");
  const [data, setData] = useState<CalculateResponse | null>(null);

  // Export format selection
  const [exportFormats, setExportFormats] = useState<{ pdf: boolean; xlsx: boolean; html: boolean }>({
    pdf: true, xlsx: false, html: false,
  });
  const [exportPeriod, setExportPeriod] = useState("");
  const [exportEmitZero, setExportEmitZero] = useState(false);
  const [exportStatus, setExportStatus] = useState<"idle" | "loading" | "error" | "done">("idle");
  const [exportError, setExportError] = useState("");
  const [savedPath, setSavedPath] = useState("");

  // Payee trace state
  const [showTrace, setShowTrace] = useState(false);
  const [tracePayee, setTracePayee] = useState("");
  const [tracePeriod, setTracePeriod] = useState("");

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
    // Resolve plan: from library, file, or auto-detect from DB
    let plan: File | undefined;
    if (planSource === "library" && selectedPlanId) {
      const p = plans.find(p => p.id === selectedPlanId);
      if (p) plan = new File([p.yaml_content], "plan.yaml", { type: "text/yaml" });
    } else if (planSource === "file" && planFile) {
      plan = planFile;
    }
    // planSource === "auto" → undefined (API resolves from DB)

    const txns = buildMappedFile();
    const pees = payeeFile || buildAutoPayees();

    if (!txns) return;

    setStatus("loading");
    setError("");
    try {
      const result = await calculate({ plan, transactions: txns, payees: pees });
      setData(result);
      setStatus("success");
      setStep("results");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Calculation failed");
      setStatus("error");
    }
  }, [planSource, selectedPlanId, plans, planFile, payeeFile, buildMappedFile, buildAutoPayees]);

  // Export statements as ZIP
  const handleExport = useCallback(async () => {
    setExportError("");
    setExportStatus("loading");

    // Resolve plan YAML text
    let planText: string | undefined;
    let planFileObj: File | undefined;
    if (planSource === "library" && selectedPlanId) {
      const p = plans.find(pl => pl.id === selectedPlanId);
      planText = p?.yaml_content;
    } else if (planSource === "file" && planFile) {
      planText = await planFile.text();
    }
    // planSource === "auto" → no plan, API resolves from DB

    // Resolve transaction data — send as text for CSV, as file for XLSX
    let txnText: string | undefined;
    let txnFileObj: File | undefined;
    if (txnFile) {
      if (txnFile.name.endsWith(".xlsx")) {
        txnFileObj = txnFile;
      } else {
        txnText = await txnFile.text();
      }
    }

    if (!txnText && !txnFileObj) {
      setExportError("No transaction data available for export.");
      setExportStatus("error");
      return;
    }

    // Resolve payee data — send as text for CSV, as file for XLSX
    let payeeText: string | undefined;
    let payeeFileObj: File | undefined;
    const pees = payeeFile || buildAutoPayees();
    if (pees.name.endsWith(".xlsx")) {
      payeeFileObj = pees;
    } else {
      payeeText = await pees.text();
    }

    const selectedFormats = Object.entries(exportFormats)
      .filter(([, v]) => v)
      .map(([k]) => k)
      .join(",") || "pdf";

    try {
      const result = await exportStatements({
        plan: planFileObj || undefined,
        transactions: txnFileObj || new File([], "empty"),
        payees: payeeFileObj || new File([], "empty"),
        plan_text: planText,
        txn_text: txnText,
        payee_text: payeeText,
        formats: selectedFormats,
        period: exportPeriod || undefined,
        emit_zero: exportEmitZero || undefined,
      });
      if (result) {
        setSavedPath(result);
      }
      setExportStatus("done");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Export failed";
      setExportError(msg);
      setExportStatus("error");
    }
  }, [planSource, selectedPlanId, plans, planFile, txnFile, payeeFile, buildAutoPayees, exportFormats]);

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
          />

          {csvPreview && (
            <div className="mt-4">
              <div className="text-xs font-medium text-ink2 mb-2">
                Detected {csvPreview.headers.length} columns, {csvPreview.rows.length} rows previewed
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
                    {csvPreview.rows.map((row, ri) => (
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
            <SourceToggle active={planSource === "auto"} onClick={() => setPlanSource("auto")} label="Auto" />
            <SourceToggle active={planSource === "library"} onClick={() => setPlanSource("library")} label="Library" />
            <SourceToggle active={planSource === "file"} onClick={() => setPlanSource("file")} label="Upload File" />
            <SourceToggle active={planSource === "build"} onClick={() => setPlanSource("build")} label="Build New" />
          </div>

          {planSource === "auto" && (
            <p className="text-sm text-ink2">
              Plans will be resolved from the database using each payee's <code>plan_id</code>.
              Save plans via the <strong>Plans</strong> tab and assign payees to plans via the <strong>Payees</strong> tab first.
            </p>
          )}

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
              disabled={planSource !== "auto" && !(selectedPlanId || planFile)}
              highlight
            >
              Calculate Commissions ✨
            </StepButton>
          </div>
        </div>
      )}

      {/* Error */}
      {status === "error" && (
        <div className="px-4 py-3 rounded-xl bg-danger/10 border border-danger/20 text-danger text-sm animate-in select-text">
          {error}
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
        <div className="space-y-6 animate-in">
          {/* Summary cards */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <StatCard label="Total Commission" value={`$${Object.values(data.summary).reduce((a, b) => a + parseFloat(b), 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} />
            <StatCard label="Payees" value={String(Object.keys(data.summary).length)} />
            <StatCard label="Commission Lines" value={String(data.commissions.length)} />
          </div>

          {/* Period lock + next period */}
          {data.calculation_ids && Object.keys(data.calculation_ids).length > 0 && (
            <PeriodLockPanel calcIds={data.calculation_ids} planId={data.commissions[0]?.payee_id ? "" : ""} />
          )}

          {/* Export section */}
          <div className="space-y-3">
            {/* Period filter + Emit zero */}
            <div className="flex items-center gap-4 flex-wrap">
              <div className="flex items-center gap-2">
                <span className="text-xs text-ink2">Period:</span>
                <input
                  type="text"
                  value={exportPeriod}
                  onChange={e => setExportPeriod(e.target.value)}
                  placeholder="YYYY-MM (optional)"
                  className="w-36 px-2 py-1 rounded text-xs bg-soft border border-line text-ink focus:outline-none focus:border-accent"
                />
              </div>
              <label className="flex items-center gap-1.5 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={exportEmitZero}
                  onChange={() => setExportEmitZero(!exportEmitZero)}
                  className="accent-accent"
                />
                <span className="text-xs text-ink2">Include $0 statements</span>
              </label>
            </div>

            {/* Format checkboxes */}
            <div className="flex items-center gap-4 flex-wrap">
              <span className="text-xs text-ink2 font-medium">Formats:</span>
              {(["pdf", "xlsx", "html"] as const).map(fmt => (
                <label key={fmt} className="flex items-center gap-1.5 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={exportFormats[fmt]}
                    onChange={() => setExportFormats(prev => ({ ...prev, [fmt]: !prev[fmt] }))}
                    className="accent-accent"
                  />
                  <span className="text-sm text-ink">{fmt.toUpperCase()}</span>
                </label>
              ))}
            </div>

            {/* Error banner */}
            {exportStatus === "error" && exportError && (
              <div className="px-4 py-2.5 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm select-text">
                {exportError}
              </div>
            )}

            {/* Download button */}
            <div className="flex justify-end items-center gap-3">
              {exportError && (
                <span className="text-danger text-xs">{exportError}</span>
              )}
              {exportStatus === "done" && savedPath && (
                <button
                  onClick={async () => {
                    try {
                      await fetch(`${localStorage.getItem("icm_api_base") || ""}/v1/open-folder`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ path: savedPath }),
                      });
                    } catch { /* ignore */ }
                  }}
                  className="text-xs text-ink2 hover:text-accent underline cursor-pointer"
                >
                  Show in folder
                </button>
              )}
              <button
                onClick={handleExport}
                disabled={exportStatus === "loading"}
                className={`
                  inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium
                  transition-all cursor-pointer
                  ${exportStatus === "loading"
                    ? "bg-soft border border-line text-ink2 cursor-wait"
                    : exportStatus === "done"
                    ? "bg-green-50 border border-green-200 text-green-700"
                    : "bg-accent/10 border border-accent/20 text-accent hover:bg-accent/15 hover:border-accent/30"
                  }
                `}
              >
                {exportStatus === "loading" ? (
                  <>
                    <span className="inline-block w-3.5 h-3.5 border-2 border-ink2/30 border-t-ink2 rounded-full animate-spin" />
                    Generating...
                  </>
                ) : exportStatus === "done" ? (
                  "✓ Saved"
                ) : (
                  "↓ Download Statements (.zip)"
                )}
              </button>
            </div>
          </div>

          {/* Per-payee breakdown */}
          <div className="card overflow-hidden">
            <div className="px-5 py-3 border-b border-line">
              <h3 className="text-sm font-semibold text-ink">Per Payee</h3>
            </div>
            <div className="divide-y divide-line">
              {Object.entries(data.summary).map(([payee, total]) => (
                <div key={payee} className="px-5 py-2.5 flex justify-between items-center text-sm">
                  <span className="text-ink font-medium">{payee}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-ink font-mono">${parseFloat(total).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                    <button
                      onClick={() => { setShowTrace(true); setTracePayee(payee); setTracePeriod(data!.commissions.find(c => c.payee_id === payee)?.period || "all"); }}
                      className="text-xs text-ink2 hover:text-accent cursor-pointer"
                      title="View payout trace"
                    >🔍</button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Commission details */}
          <div className="card overflow-hidden">
            <div className="px-5 py-3 border-b border-line">
              <h3 className="text-sm font-semibold text-ink">All Commissions ({data.commissions.length})</h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="bg-soft">
                    <th className="px-3 py-2 text-left font-medium text-ink2">Deal</th>
                    <th className="px-3 py-2 text-left font-medium text-ink2">Payee</th>
                    <th className="px-3 py-2 text-left font-medium text-ink2">Rule</th>
                    <th className="px-3 py-2 text-right font-medium text-ink2">Amount</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {data.commissions.map((c, i) => (
                    <tr key={i} className="hover:bg-soft">
                      <td className="px-3 py-1.5 text-ink font-mono">{c.transaction_id}</td>
                      <td className="px-3 py-1.5 text-ink">{c.payee_id}</td>
                      <td className="px-3 py-1.5 text-ink2">{c.rule_id}</td>
                      <td className="px-3 py-1.5 text-ink font-mono text-right">${parseFloat(c.commission_amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="flex justify-center">
            <TextButton onClick={() => { setStep("data"); setStatus("idle"); setData(null); }}>
              ← Start New Calculation
            </TextButton>
          </div>

          {/* Payee trace panel */}
          {showTrace ? (tracePayee ? (
            <>
              <div className="fixed inset-0 bg-black/20 z-40" onClick={() => { setShowTrace(false); setTracePayee(""); }} />
              <PayeeTrace payeeId={tracePayee} period={tracePeriod || "all"}
                commissions={data.commissions} ledger={data.ledger}
                onClose={() => { setShowTrace(false); setTracePayee(""); }} />
            </>
          ) : (
            <>
              <div className="fixed inset-0 bg-black/20 z-40" onClick={() => setShowTrace(false)} />
              <div className="fixed inset-y-0 right-0 w-[460px] max-w-[92vw] bg-white border-l border-line shadow-xl z-50 flex flex-col">
                <div className="p-5">
                  <h3 className="text-sm font-semibold mb-2">Select a payee</h3>
                  {Object.entries(data.summary).map(([pid]) => (
                    <button key={pid}
                      onClick={() => { setTracePayee(pid); setTracePeriod(data.commissions.find(c => c.payee_id === pid)?.period || "all"); }}
                      className="block w-full text-left px-3 py-2 rounded hover:bg-soft text-sm cursor-pointer">{pid}</button>
                  ))}
                  <button onClick={() => setShowTrace(false)} className="mt-4 text-xs text-ink2 hover:text-ink cursor-pointer">Close</button>
                </div>
              </div>
            </>
          )) : null}
        </div>
      )}

    </div>
  );

}

// ------------------------------------------------------------------
// Sub-components
// ------------------------------------------------------------------

function DropZone({ file, onChange, accept, label }: {
  file: File | null;
  onChange: (f: File | null) => void;
  accept: string;
  label: string;
}) {
  const [dragOver, setDragOver] = useState(false);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f) onChange(f);
  }, [onChange]);

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      onClick={() => {
        const input = document.createElement("input");
        input.type = "file";
        input.accept = accept;
        input.onchange = () => {
          const f = input.files?.[0];
          if (f) onChange(f);
        };
        input.click();
      }}
      className={`
        rounded-xl border-2 border-dashed p-6 text-center cursor-pointer transition-all
        ${dragOver
          ? "border-accent bg-accent/10 scale-[1.01]"
          : file
            ? "border-accent bg-accent/5"
            : "border-line bg-soft hover:border-ink2"
        }
      `}
    >
      {file ? (
        <div>
          <div className="text-lg mb-1">{accept.includes("csv") ? "📊" : accept.includes("yaml") ? "📋" : "📄"}</div>
          <div className="text-sm font-medium text-ink">{file.name}</div>
          <div className="text-xs text-ink2 mt-0.5">
            {(file.size / 1024).toFixed(1)} KB · Click to change
          </div>
        </div>
      ) : (
        <div>
          <div className="text-2xl mb-1 opacity-40">📂</div>
          <div className="text-sm text-ink2">{label}</div>
          <div className="text-xs text-ink2 mt-0.5">Drag & drop or click to browse</div>
        </div>
      )}
    </div>
  );
}

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

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="card p-4">
      <div className="text-xs text-ink2">{label}</div>
      <div className="text-lg font-bold text-ink mt-0.5">{value}</div>
    </div>
  );
}


// ------------------------------------------------------------------
// Period lock panel
// ------------------------------------------------------------------

function PeriodLockPanel({ calcIds, planId }: { calcIds: Record<string, string>; planId: string }) {
  const [locked, setLocked] = useState<Record<string, boolean>>({});
  const base = localStorage.getItem("icm_api_base") || "";

  // Derive plan_id from the first payee's data if not provided
  const [resolvedPlanId, setResolvedPlanId] = useState(planId);

  useEffect(() => {
    // Check lock status for each period
    const periods = Object.keys(calcIds);
    if (periods.length === 0) return;
    // Try to determine plan_id from the first calculation
    const calcId = calcIds[periods[0]];
    fetch(`${base}/v1/calculations/${encodeURIComponent(calcId)}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.plan_id) setResolvedPlanId(d.plan_id);
        // Then check lock statuses
        periods.forEach(period => {
          fetch(`${base}/v1/periods/${encodeURIComponent(d?.plan_id || resolvedPlanId)}/${period}/status`)
            .then(r => r.json())
            .then(s => setLocked(prev => ({ ...prev, [period]: s.locked === true })))
            .catch(() => {});
        });
      })
      .catch(() => {});
  }, [calcIds]);

  const toggleLock = async (period: string) => {
    const calcId = calcIds[period];
    const pid = resolvedPlanId;
    if (!pid) return;
    const currentlyLocked = locked[period];
    if (currentlyLocked) {
      await fetch(`${base}/v1/periods/${encodeURIComponent(pid)}/${period}/lock`, { method: "DELETE" });
    } else {
      await fetch(`${base}/v1/periods/${encodeURIComponent(pid)}/${period}/lock?calculation_id=${encodeURIComponent(calcId)}`, { method: "POST" });
    }
    setLocked(prev => ({ ...prev, [period]: !currentlyLocked }));
  };

  const periods = Object.keys(calcIds);
  if (periods.length === 0) return null;

  const nextPeriod = () => {
    const lastPeriod = periods[periods.length - 1];
    const [year, month] = lastPeriod.split("-").map(Number);
    const d = new Date(year, month, 1); // month is 0-indexed, so this gives us the next month
    const next = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    // Pre-fill could go here via a callback prop, but for now just show it
    alert(`Next period: ${next}\n\nStart a new calculation with this period.`);
  };

  return (
    <div className="card p-4 space-y-3">
      <h3 className="text-sm font-semibold text-ink">Periods</h3>
      {periods.map(period => (
        <div key={period} className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${locked[period] ? "bg-green-500" : "bg-ink2/30"}`} />
            <span className="text-xs text-ink2">{period}</span>
            <span className={`text-xs font-medium ${locked[period] ? "text-green-700" : "text-ink2"}`}>
              {locked[period] ? "Locked" : "Open"}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => toggleLock(period)}
              className={`px-2 py-0.5 rounded text-xs cursor-pointer transition-colors ${
                locked[period]
                  ? "bg-ink2/10 text-ink2 hover:bg-ink2/20"
                  : "bg-accent/10 text-accent hover:bg-accent/20"
              }`}>
              {locked[period] ? "Unlock" : "Lock"}
            </button>
            {locked[period] && resolvedPlanId && (
              <a
                href={`${base}/v1/periods/${encodeURIComponent(resolvedPlanId)}/${period}/register`}
                className="px-2 py-0.5 rounded text-xs bg-green-50 text-green-700 hover:bg-green-100 cursor-pointer transition-colors no-underline"
              >
                Register ↓
              </a>
            )}
          </div>
        </div>
      ))}
      <div className="pt-2 border-t border-line">
        <button onClick={nextPeriod}
          className="w-full px-3 py-1.5 rounded text-xs font-medium bg-accent/10 text-accent hover:bg-accent/20 cursor-pointer transition-colors">
          Start Next Period →
        </button>
      </div>
    </div>
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

interface PlanBuilderRule {
  type: string;
  id: string;
  rate?: string;
  filter?: string;
  tiers?: { threshold: string; rate: string }[];
  threshold_pct?: string;
  multiplier?: string;
}

interface PlanBuilderPlanData {
  plan_id: string;
  name: string;
  period_type: string;
  currency: string;
  rules: PlanBuilderRule[];
}

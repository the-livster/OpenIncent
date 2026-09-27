import { useState } from "react";
import { calculate, previewFile } from "../api";
import type { CalculateResponse } from "../types";
import type { PayeeRow, TransactionRow } from "./Pipeline";
import { payeeCsv } from "../payeeCsv";
import { Td } from "./Table";
import { useTableSort } from "./useTableSort";
import { FilterBar, FilterTh, SortTh } from "./SortableTable";
import StageShell from "./StageShell";
import UploadZone from "./UploadZone";
import { Button, Callout, Icon, Pagination } from "./ui";

interface Props {
  payees: PayeeRow[];
  transactions: TransactionRow[];
  setTransactions: (t: TransactionRow[]) => void;
  txnFile: File | null;
  setTxnFile: (file: File | null) => void;
  samplePlan: File | null;
  onCalculated: (r: CalculateResponse) => void;
  onBack: () => void;
}

const COLUMNS = [
  { col: "id", label: "ID" },
  { col: "payee_id", label: "Payee" },
  { col: "period", label: "Period" },
  { col: "amount", label: "Amount" },
  { col: "product", label: "Product" },
] as const;

export default function StageCrediting({ payees, transactions: preview, setTransactions, txnFile, setTxnFile, samplePlan, onCalculated, onBack }: Props) {
  const [status, setStatus] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [error, setError] = useState("");
  // Mirrors CalculatorWizard: the pre-flight blocks unrostered ids, so this
  // flow needs the same way past it or the Pipeline is simply stuck.
  const [unknownPayeesBlocked, setUnknownPayeesBlocked] = useState(false);
  const [allowUnknownPayees, setAllowUnknownPayees] = useState(false);
  const [uploading, setUploading] = useState(false);

  const { paginated, totalItems, page, totalPages, setPage, sortCol, sortDir, filters, toggleSort, setFilter, clearFilters } = useTableSort(preview, "id");

  async function handleFile(f: File) {
    if (uploading) return;
    setUploading(true); setError("");
    try {
      const p = await previewFile(f, "transactions");
      const rows = p.preview_rows.map((row, _i) => {
        const get = (target: string, fallback: string) => {
          const src = p.mapping[target];
          if (src !== undefined) {
            const idx = p.headers.indexOf(src);
            if (idx >= 0 && idx < row.length) return String(row[idx]);
          }
          return fallback;
        };
        return {
          id: get("id", `T-${_i}`), payee_id: get("payee_id", ""),
          deal_id: get("deal_id", get("id", "")), period: get("period", ""),
          amount: get("amount", "0"), product: get("product", ""),
          close_date: get("close_date", ""),
        };
      });
      if (!rows.length) throw new Error("The file contains no transactions.");
      setTxnFile(f); setTransactions(rows);
      setAllowUnknownPayees(false); setUnknownPayeesBlocked(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not parse transactions file.");
    } finally { setUploading(false); }
  }

  async function runCalc() {
    if (preview.length === 0) { setError("No transactions loaded."); return; }
    setStatus("loading"); setError("");
    try {
      if (!txnFile) throw new Error("Upload the original transaction file before calculating.");
      const pees = new File([payeeCsv(payees)], "payees.csv", { type: "text/csv" });
      const result = await calculate({ transactions: txnFile, payees: pees, allowUnknownPayees,
        plan: samplePlan ?? undefined, sample: !!samplePlan });
      onCalculated(result);
      setStatus("done");
    } catch (e: unknown) {
      const codes = (e as { codes?: string[] })?.codes ?? [];
      setUnknownPayeesBlocked(codes.includes("unknown_payee"));
      setError(e instanceof Error ? e.message : "Calculation failed");
      setStatus("error");
    }
  }

  const loaded = preview.length > 0;

  return (
    <StageShell
      n={5}
      group="Run"
      title="Crediting"
      description="Upload the period's deals. Each sale is credited to its payee, then every plan runs against it."
      actions={loaded && (
        <Button size="sm" variant="ghost" icon="x" disabled={status === "loading"}
          onClick={() => { setTransactions([]); setTxnFile(null); }}>
          Clear
        </Button>
      )}
      onBack={onBack}
      footer={loaded && (
        <Button variant="primary" icon={status === "loading" ? undefined : "play"} loading={status === "loading"}
          onClick={runCalc} disabled={status === "loading" || uploading || !txnFile}>
          {status === "loading" ? "Calculating..." : "Calculate commissions"}
        </Button>
      )}
    >
      {error && !loaded && <Callout tone="danger" role="alert">{error}</Callout>}
      {!loaded ? (
        <UploadZone
          label="Upload Transactions"
          title="Drop this period's deals here"
          hint="CSV or XLSX with columns id, payee_id, amount and period. Messy headers are matched automatically."
          accept=".csv,.xlsx"
          icon="upload"
          busy={uploading}
          busyLabel="Reading transactions..."
          onFile={handleFile}
        />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-surface-2/60 px-4 py-3 text-[13px] text-ink-2">
            <Icon name="fileText" className="text-ink-3" />
            <span className="font-medium text-ink">{txnFile?.name}</span>
            <span>showing {preview.length} preview rows. The full original file will be calculated.</span>
          </div>
          <FilterBar total={preview.length} shown={totalItems} filters={filters} onClear={clearFilters} />
          <div className="table-wrap overflow-hidden rounded-xl border border-line">
            <table>
              <thead>
                <tr>
                  {COLUMNS.map(c => (
                    <SortTh key={c.col} col={c.col} label={c.label} current={sortCol} dir={sortDir} onClick={toggleSort}
                      align={c.col === "amount" ? "right" : undefined} />
                  ))}
                </tr>
                <tr>
                  {COLUMNS.map(c => (
                    <FilterTh key={c.col} label={c.label} value={filters[c.col] || ""} onChange={v => setFilter(c.col, v)} />
                  ))}
                </tr>
              </thead>
              <tbody>
                {paginated.map(t => (
                  <tr key={t.id}>
                    <Td mono>{t.id}</Td><Td mono>{t.payee_id}</Td><Td mono>{t.period}</Td>
                    <Td num>{t.amount}</Td><Td className="text-ink-2">{t.product}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination page={page} totalPages={totalPages} onPage={setPage} className="border-t border-line" />
          </div>
          {error && (
            <Callout tone="danger">
              <div role="alert" className="whitespace-pre-wrap">{error}</div>
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
                    genuinely not on the roster - a mistyped id will split one person's
                    bookings in two and neither half will reach quota.
                  </span>
                </label>
              )}
            </Callout>
          )}
        </>
      )}
    </StageShell>
  );
}

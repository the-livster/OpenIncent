import { useMemo, useState } from "react";
import type { CalculateResponse } from "../types";
import { exportSavedStatements } from "../api";
import { withCurrency } from "../format";
import { Th, Td } from "./Table";
import StageShell from "./StageShell";
import StatementPreview, { type StatementTarget } from "./StatementPreview";
import { Button, Callout, Card, CardHeader, EmptyState, Icon, Pagination, Segmented, type IconName } from "./ui";
import { cx } from "./ui/cx";

interface Props { result: CalculateResponse; onBack: () => void }

const FORMATS: { id: string; label: string; hint: string; icon: IconName }[] = [
  { id: "html", label: "HTML", hint: "Interactive: every deal explained, filters, dark mode", icon: "sparkles" },
  { id: "pdf", label: "PDF", hint: "Print-ready, one page per few deals", icon: "printer" },
  { id: "xlsx", label: "XLSX", hint: "Excel workbook of every line", icon: "table" },
];

export default function StageReporting({ result, onBack }: Props) {
  const [view, setView] = useState<"summary" | "ledger">("summary");
  const [formats, setFormats] = useState(["xlsx"]);
  const [exporting, setExporting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const [preview, setPreview] = useState<StatementTarget | null>(null);
  const ledger = result.ledger ?? [];
  const pages = Math.max(1, Math.ceil(ledger.length / 100));
  const payouts = result.payouts ?? [];
  const calculationIds = useMemo(() => Object.values(result.calculation_ids ?? {}), [result.calculation_ids]);

  async function download() {
    setExporting(true); setError(""); setMessage("");
    try {
      const savedTo = await exportSavedStatements(result, formats);
      setMessage(savedTo ? "Statements saved to " + savedTo : "Statements downloaded. Open the ZIP to view the individual statements and internal summary.");
    } catch (e) { setError(e instanceof Error ? e.message : "Export failed. Please try again."); }
    finally { setExporting(false); }
  }

  return (
    <StageShell
      n={9}
      group="Results"
      title="Reporting"
      description="Check the statement totals and preview what each person will see, then download one statement per person and period. Exports use this saved calculation, with its original plan settings."
      onBack={onBack}
    >
      <Segmented
        label="Report view"
        value={view}
        onChange={setView}
        options={[
          { value: "summary", label: "Payout summary", icon: "receipt" },
          { value: "ledger", label: `Audit ledger (${ledger.length})`, icon: "listChecks" },
        ]}
      />

      {view === "summary" && (
        payouts.length ? (
          <div className="table-wrap overflow-hidden rounded-xl border border-line">
            <table>
              <thead><tr><Th>Payee</Th><Th>Period</Th><Th className="text-right">Statement total</Th><Th className="w-0" /></tr></thead>
              <tbody>
                {payouts.map(row => (
                  <tr key={row.payee_id + ":" + row.period}>
                    <Td>
                      <span className="block font-medium">{row.name}</span>
                      <span className="block font-mono text-[11.5px] text-ink-3">{row.payee_id}</span>
                    </Td>
                    <Td mono className="text-ink-2">{row.period}</Td>
                    <Td num className="font-semibold">{withCurrency(row.currency, row.total)}</Td>
                    <Td className="text-right">
                      <Button size="sm" icon="eye" disabled={!calculationIds.length}
                        aria-label={`Preview statement for ${row.name}`}
                        onClick={() => setPreview({ payeeId: row.payee_id, name: row.name, period: row.period })}>
                        Preview
                      </Button>
                    </Td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                {Object.entries(result.payout_totals ?? {}).map(([currency, total]) => (
                  <tr key={currency}>
                    <td colSpan={2} className="border-t border-line bg-surface-2 font-semibold">Total ({currency})</td>
                    <td className="num border-t border-line bg-surface-2 text-right font-semibold">{withCurrency(currency, total)}</td>
                    <td className="border-t border-line bg-surface-2" />
                  </tr>
                ))}
              </tfoot>
            </table>
          </div>
        ) : (
          <EmptyState icon="receipt" title="No statement payouts in this run" compact />
        )
      )}

      {view === "ledger" && (
        <div className="overflow-hidden rounded-xl border border-line">
          <div className="table-wrap max-h-[60vh]">
            <table>
              <thead><tr><Th>Payee</Th><Th>Event</Th><Th>Rule</Th><Th>Details</Th></tr></thead>
              <tbody>
                {ledger.slice((page - 1) * 100, page * 100).map((entry, i) => (
                  <tr key={i}>
                    <Td mono>{String(entry.payee_id ?? "")}</Td>
                    <Td><span className="badge">{String(entry.event_type ?? "")}</span></Td>
                    <Td mono className="text-ink-2">{String(entry.rule_id ?? "")}</Td>
                    <Td className="min-w-64 whitespace-normal text-ink-2">{String(entry.human_readable ?? "")}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={page} totalPages={pages} onPage={setPage} className="border-t border-line" />
        </div>
      )}

      <Card className="overflow-hidden" aria-label="Export statements" role="region">
        <CardHeader
          icon="download"
          title="Download statements"
          description="A ZIP of private per-person statements plus an internal payout summary. Share each person only their own statement."
        />
        <div className="space-y-4 px-5 py-4">
          <fieldset disabled={exporting} className="grid gap-2 sm:grid-cols-3">
            <legend className="sr-only">Statement formats</legend>
            {FORMATS.map(format => {
              const checked = formats.includes(format.id);
              return (
                <label key={format.id} className={cx(
                  "flex items-start gap-3 rounded-xl border px-3.5 py-3 transition-colors",
                  checked ? "border-accent/50 bg-accent-soft" : "border-line hover:bg-surface-2",
                )}>
                  <input
                    type="checkbox"
                    aria-label={format.label}
                    className="mt-0.5"
                    checked={checked}
                    onChange={e => setFormats(previous => e.target.checked ? [...previous, format.id] : previous.filter(f => f !== format.id))}
                  />
                  <span className="min-w-0">
                    <span className="flex items-center gap-1.5 text-[13.5px] font-semibold text-ink">
                      <Icon name={format.icon} size={14} className="text-ink-2" />{format.label}
                    </span>
                    <span className="mt-0.5 block text-[12px] text-ink-2">{format.hint}</span>
                  </span>
                </label>
              );
            })}
          </fieldset>
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="primary" icon="download" loading={exporting} onClick={download}
              disabled={exporting || !formats.length || !calculationIds.length}>
              {exporting ? "Preparing statements..." : "Download statements"}
            </Button>
            {message && <p role="status" className="text-[13px] break-all text-success-ink">{message}</p>}
          </div>
          {error && <Callout tone="danger" role="alert">{error}</Callout>}
        </div>
      </Card>

      <StatementPreview
        target={preview}
        calculationIds={calculationIds}
        sample={result.sample}
        onClose={() => setPreview(null)}
      />
    </StageShell>
  );
}

import { useState } from "react";
import type { CalculateResponse } from "../types";
import { exportSavedStatements } from "../api";
import { Th, Td } from "./Table";

interface Props { result: CalculateResponse; onBack: () => void }

export default function StageReporting({ result, onBack }: Props) {
  const [view, setView] = useState<"summary" | "ledger">("summary");
  const [formats, setFormats] = useState(["xlsx"]);
  const [exporting, setExporting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const ledger = result.ledger ?? [];
  const pages = Math.max(1, Math.ceil(ledger.length / 100));
  const payouts = result.payouts ?? [];

  async function download() {
    setExporting(true); setError(""); setMessage("");
    try {
      const savedTo = await exportSavedStatements(result, formats);
      setMessage(savedTo ? "Statements saved to " + savedTo : "Statements downloaded. Open the ZIP to view the individual statements and internal summary.");
    } catch (e) { setError(e instanceof Error ? e.message : "Export failed. Please try again."); }
    finally { setExporting(false); }
  }

  return (
    <div className="max-w-4xl space-y-5">
      <h1 className="text-lg font-bold text-zinc-800">9. Reporting / Output</h1>
      <p className="text-sm text-zinc-500">Review the statement totals, then download one statement per person and period. Exports use this saved calculation, including its original plan settings.</p>
      <div className="flex gap-2">
        <button onClick={() => setView("summary")} aria-pressed={view === "summary"} className="px-3 py-2 rounded-lg text-sm bg-blue-50 text-blue-700">Payout Summary</button>
        <button onClick={() => setView("ledger")} aria-pressed={view === "ledger"} className="px-3 py-2 rounded-lg text-sm bg-zinc-100 text-zinc-700">Audit Ledger ({ledger.length})</button>
      </div>
      {view === "summary" && <div className="space-y-3">
        {payouts.length ? <div className="overflow-x-auto"><table className="w-full text-sm border">
          <thead className="bg-zinc-100"><tr><Th>Payee</Th><Th>Period</Th><Th>Currency</Th><Th className="text-right">Statement total</Th></tr></thead>
          <tbody>{payouts.map(row => <tr key={row.payee_id + ":" + row.period} className="border-b border-zinc-100">
            <Td><span className="font-medium">{row.name}</span><span className="block text-xs text-zinc-500">{row.payee_id}</span></Td>
            <Td>{row.period}</Td><Td>{row.currency}</Td><Td className="text-right font-semibold">{row.total}</Td>
          </tr>)}</tbody>
        </table></div> : <p className="text-sm text-zinc-500">No statement payouts in this run.</p>}
        {Object.entries(result.payout_totals ?? {}).map(([currency, total]) => <p key={currency} className="text-right text-sm font-semibold">Total: {currency} {total}</p>)}
      </div>}
      {view === "ledger" && <div className="space-y-3">
        <div className="overflow-auto max-h-[60vh] border rounded-lg"><table className="w-full text-xs">
          <thead className="bg-zinc-100 sticky top-0"><tr><Th>Payee</Th><Th>Event</Th><Th>Rule</Th><Th>Details</Th></tr></thead>
          <tbody>{ledger.slice((page - 1) * 100, page * 100).map((entry, i) => <tr key={i} className="border-b border-zinc-100">
            <Td mono>{String(entry.payee_id ?? "")}</Td><Td>{String(entry.event_type ?? "")}</Td>
            <Td mono>{String(entry.rule_id ?? "")}</Td><Td className="whitespace-normal min-w-64">{String(entry.human_readable ?? "")}</Td>
          </tr>)}</tbody>
        </table></div>
        {pages > 1 && <div className="flex justify-center gap-4 text-sm">
          <button disabled={page === 1} onClick={() => setPage(p => p - 1)} className="disabled:opacity-30">Previous entries</button>
          <span>Page {page} of {pages}</span>
          <button disabled={page === pages} onClick={() => setPage(p => p + 1)} className="disabled:opacity-30">Next entries</button>
        </div>}
      </div>}
      <section className="border border-blue-200 bg-blue-50 rounded-xl p-5 space-y-3" aria-label="Export statements">
        <h2 className="text-sm font-semibold text-blue-900">Download statements</h2>
        <p className="text-sm text-blue-800">The ZIP includes private per-person statements and an internal payout summary. Share only the intended person's statement.</p>
        <fieldset disabled={exporting} className="flex gap-5 text-sm">
          <legend className="sr-only">Statement formats</legend>
          {["xlsx", "pdf", "html"].map(format => <label key={format} className="flex gap-2 items-center">
            <input type="checkbox" checked={formats.includes(format)} onChange={e => setFormats(previous => e.target.checked ? [...previous, format] : previous.filter(f => f !== format))} />
            {format.toUpperCase()}
          </label>)}
        </fieldset>
        <button onClick={download} disabled={exporting || !formats.length || !Object.keys(result.calculation_ids ?? {}).length}
          className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40">
          {exporting ? "Preparing statements..." : "Download statements"}
        </button>
        {message && <p role="status" className="text-sm text-green-800 break-all">{message}</p>}
        {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      </section>
      <button onClick={onBack} className="text-sm text-zinc-500 hover:text-zinc-700">Back to Adjustments</button>
    </div>
  );
}

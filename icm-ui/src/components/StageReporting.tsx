import { useState } from "react";
import type { CalculateResponse } from "../types";
import { Th, Td } from "./Table";

interface Props { result: CalculateResponse; onBack: () => void; }

export default function StageReporting({ result, onBack }: Props) {
  const [view, setView] = useState<"summary" | "ledger">("summary");

  const commissions = result.commissions || [];
  const ledger = result.ledger;

  // Summary by payee
  const byPayee: Record<string, number> = {};
  for (const c of commissions) {
    byPayee[c.payee_id] = (byPayee[c.payee_id] || 0) + parseFloat(c.commission_amount || "0");
  }

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-bold text-zinc-800">9. Reporting / Output</h1>

      <div className="flex gap-2">
        <button onClick={() => setView("summary")} className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer ${view === "summary" ? "bg-blue-100 text-blue-700" : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200"}`}>
          Payout Summary
        </button>
        <button onClick={() => setView("ledger")} className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer ${view === "ledger" ? "bg-blue-100 text-blue-700" : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200"}`}>
          Audit Ledger ({ledger?.length || 0})
        </button>
      </div>

      {view === "summary" && (
        <div className="space-y-3">
          <table className="w-full text-xs border rounded-lg overflow-hidden">
            <thead className="bg-zinc-100">
              <tr><Th>Payee</Th><Th className="text-right">Total Payout</Th><Th className="text-right">Lines</Th></tr>
            </thead>
            <tbody>
              {Object.entries(byPayee).sort(([a], [b]) => a.localeCompare(b)).map(([pid, total]) => (
                <tr key={pid} className="border-b border-zinc-100 hover:bg-zinc-50">
                  <Td mono>{pid}</Td>
                  <Td className="text-right font-semibold">${total.toFixed(2)}</Td>
                  <Td className="text-right text-zinc-400">{commissions.filter(c => c.payee_id === pid).length}</Td>
                </tr>
              ))}
              <tr className="bg-zinc-50 font-bold">
                <Td>Total</Td>
                <Td className="text-right">${commissions.reduce((s, c) => s + parseFloat(c.commission_amount || "0"), 0).toFixed(2)}</Td>
                <Td className="text-right">{commissions.length}</Td>
              </tr>
            </tbody>
          </table>

          {/* Navigate back to calculator for full export */}
          <div className="border border-blue-200 bg-blue-50 rounded-lg p-4">
            <h3 className="text-sm font-semibold text-blue-800 mb-2">Export Statements</h3>
            <p className="text-xs text-blue-700 mb-2">Return to the Calculator tab to export per-rep statements (PDF, XLSX, HTML).</p>
            <button
              onClick={onBack}
              className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 cursor-pointer"
            >
              ← Back to Calculator
            </button>
          </div>
        </div>
      )}

      {view === "ledger" && ledger && (
        <div className="overflow-auto max-h-[70vh] border rounded-lg">
          <table className="w-full text-xs">
            <thead className="bg-zinc-100 sticky top-0">
              <tr>
                <Th>Payee</Th><Th>Event</Th><Th>Rule</Th><Th>Details</Th>
              </tr>
            </thead>
            <tbody>
              {ledger.slice(0, 200).map((e, i) => (
                <tr key={i} className="border-b border-zinc-100 hover:bg-zinc-50">
                  <Td mono>{String(e.payee_id ?? "")}</Td>
                  <Td>{String(e.event_type ?? "")}</Td>
                  <Td mono>{String(e.rule_id ?? "")}</Td>
                  <Td className="text-zinc-500 max-w-xs truncate">{String(e.human_readable ?? "")}</Td>
                </tr>
              ))}
            </tbody>
          </table>
          {ledger.length > 200 && <p className="p-3 text-xs text-zinc-400 text-center">+ {ledger.length - 200} more entries</p>}
        </div>
      )}

      <div className="flex justify-between">
        <button onClick={onBack} className="text-sm text-zinc-500 hover:text-zinc-700">← Back</button>
      </div>
    </div>
  );
}
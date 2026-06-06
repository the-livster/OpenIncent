import { useState } from "react";
import { calculate, previewFile } from "../api";
import type { CalculateResponse, SavedPlan } from "../types";
import type { PayeeRow, TransactionRow } from "./Pipeline";

interface Props {
  payees: PayeeRow[];
  transactions: TransactionRow[];
  setTransactions: (t: TransactionRow[]) => void;
  plans: SavedPlan[];
  onCalculated: (r: CalculateResponse) => void;
  onBack: () => void;
}

export default function StageCrediting({ payees, transactions: _transactions, setTransactions, plans: _plans, onCalculated, onBack }: Props) {
  const [status, setStatus] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<TransactionRow[]>(_transactions);

  async function handleFile(f: File) {
    try {
      const p = await previewFile(f, "transactions");
      const rows = p.preview_rows.slice(0, 100).map((row, _i) => {
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
      setPreview(rows);
      setTransactions(rows);
    } catch {
      setError("Could not parse transactions file. Ensure it's CSV or XLSX with id, payee_id, amount, period columns.");
    }
  }

  async function runCalc() {
    if (preview.length === 0) { setError("No transactions loaded."); return; }
    setStatus("loading"); setError("");

    // Build payee CSV and transaction CSV for the API call
    const peeHeaders = ["id","name","quota","plan_id","effective_from","effective_to","email","manager_id","manager_override","team_id"];
    const peeCSV = [peeHeaders.join(","), ...payees.map(p =>
      [p.id, p.name, p.quota, p.plan_id, p.effective_from, p.effective_to, p.email, p.manager_id, p.manager_override, p.team_id].map(v => `"${v}"`).join(",")
    )].join("\n");

    const txnHeaders = ["id","payee_id","deal_id","period","amount","product","close_date"];
    const txnCSV = [txnHeaders.join(","), ...preview.map(t =>
      [t.id, t.payee_id, t.deal_id, t.period, t.amount, t.product, t.close_date].map(v => `"${v}"`).join(",")
    )].join("\n");

    try {
      const pees = new File([peeCSV], "payees.csv", { type: "text/csv" });
      const txns = new File([txnCSV], "transactions.csv", { type: "text/csv" });

      // If all payees have a plan_id and all plans exist in DB, send without plan (multi-plan auto-resolve)
      // Otherwise, try with the first plan
      const result = await calculate({ transactions: txns, payees: pees });
      onCalculated(result);
      setStatus("done");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Calculation failed");
      setStatus("error");
    }
  }

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-bold text-zinc-800">4. Crediting</h1>
      <p className="text-sm text-zinc-500">Upload the period's deals (transactions). The engine resolves credits — splits, overlays, and manager overrides — then runs the full pipeline once.</p>

      {preview.length === 0 ? (
        <div className="space-y-3">
          <div className="border-2 border-dashed border-zinc-300 rounded-xl p-8 text-center">
            <p className="text-sm text-zinc-500 mb-3">Drop a CSV or XLSX file with columns: id, payee_id, amount, period</p>
            <label className="inline-block px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 cursor-pointer">
              Upload Transactions
              <input type="file" accept=".csv,.xlsx" className="hidden" onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f); }} />
            </label>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <span className="text-sm text-zinc-600">{preview.length} deals loaded</span>
            <button onClick={() => { setPreview([]); setTransactions([]); }} className="text-xs text-zinc-400 hover:text-zinc-600">Clear</button>
          </div>
          <table className="w-full text-xs border rounded-lg overflow-hidden">
            <thead className="bg-zinc-100">
              <tr>
                <Th>ID</Th><Th>Payee</Th><Th>Period</Th><Th>Amount</Th><Th>Product</Th>
              </tr>
            </thead>
            <tbody>
              {preview.slice(0, 15).map(t => (
                <tr key={t.id} className="border-b border-zinc-100 hover:bg-zinc-50">
                  <Td mono>{t.id}</Td><Td mono>{t.payee_id}</Td><Td mono>{t.period}</Td><Td>{t.amount}</Td><Td>{t.product}</Td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="flex justify-between items-center">
            <button onClick={onBack} className="text-sm text-zinc-500 hover:text-zinc-700">← Back</button>
            <button onClick={runCalc} disabled={status === "loading"}
              className="px-5 py-2.5 rounded-lg text-sm font-semibold bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 cursor-pointer">
              {status === "loading" ? "Calculating..." : "Calculate Commissions →"}
            </button>
          </div>
          {error && <div className="px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-red-700 text-xs">{error}</div>}
        </div>
      )}
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="px-3 py-2 text-left font-medium text-zinc-500 whitespace-nowrap">{children}</th>;
}
function Td({ children, mono }: { children: React.ReactNode; mono?: boolean }) {
  return <td className={`px-3 py-1.5 whitespace-nowrap ${mono ? "font-mono text-zinc-600" : "text-zinc-700"}`}>{children}</td>;
}

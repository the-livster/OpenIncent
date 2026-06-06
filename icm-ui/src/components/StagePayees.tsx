import { useState } from "react";
import type { PayeeRow } from "./Pipeline";
import { previewFile } from "../api";

interface Props {
  payees: PayeeRow[];
  setPayees: (p: PayeeRow[]) => void;
  onNext: () => void;
}

export default function StagePayees({ payees, setPayees, onNext }: Props) {
  const [loading, setLoading] = useState(false);

  async function handleFile(f: File) {
    setLoading(true);
    try {
      const preview = await previewFile(f, "payees");
      const rows: PayeeRow[] = [];
      for (const row of preview.preview_rows.slice(0, 50)) {
        // Map using inferred mapping or raw columns
        const get = (target: string, fallback: string) => {
          const src = preview.mapping[target];
          if (src !== undefined) {
            const idx = preview.headers.indexOf(src);
            if (idx >= 0 && idx < row.length) return String(row[idx]);
          }
          return fallback;
        };
        rows.push({
          id: get("id", ""), name: get("name", ""), quota: get("quota", "0"),
          plan_id: get("plan_id", ""), effective_from: get("effective_from", ""),
          effective_to: get("effective_to", ""), email: get("email", ""),
          ramp_months: "", ramp_schedule: "", category_quotas: "{}",
          manager_id: get("manager_id", ""), manager_override: get("manager_override", ""),
          team_id: get("team_id", ""),
        });
      }
      setPayees(rows);
    } catch {
      // fallback: parse as CSV
      const text = await f.text();
      const lines = text.trim().split(/\r?\n/);
      if (lines.length < 2) return;
      const headers = lines[0].split(",").map(h => h.trim().toLowerCase());
      const rows: PayeeRow[] = [];
      for (let i = 1; i < Math.min(lines.length, 51); i++) {
        const cells = lines[i].split(",").map(c => c.trim().replace(/^"|"$/g, ""));
        const get = (names: string[]) => {
          for (const n of names) { const idx = headers.indexOf(n); if (idx >= 0) return cells[idx] || ""; }
          return "";
        };
        rows.push({
          id: cells[0] || "", name: cells[1] || "", quota: cells[2] || "0",
          plan_id: cells[3] || "", effective_from: cells[4] || "",
          effective_to: cells[5] || "", email: cells[6] || "",
          ramp_months: "", ramp_schedule: "", category_quotas: "{}",
          manager_id: get(["manager_id", "manager"]),
          manager_override: get(["manager_override", "manager rate"]),
          team_id: get(["team_id", "team"]),
        });
      }
      setPayees(rows);
    }
    setLoading(false);
  }

  return (
    <div className="max-w-3xl space-y-4">
      <h1 className="text-lg font-bold text-zinc-800">1. Payees</h1>
      <p className="text-sm text-zinc-500">Upload your payee roster (CSV or XLSX). Columns: id, name, quota, plan_id, effective_from.</p>
      {payees.length === 0 ? (
        <div className="border-2 border-dashed border-zinc-300 rounded-xl p-8 text-center space-y-3">
          <p className="text-sm text-zinc-500">Drop a CSV or XLSX file with columns: id, name, quota, plan_id, effective_from</p>
          <label className="inline-block px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 cursor-pointer">
            {loading ? "Loading..." : "Upload Payees"}
            <input type="file" accept=".csv,.xlsx" className="hidden" onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f); }} />
          </label>
        </div>
      ) : (
        <div className="space-y-3">
          <table className="w-full text-xs border rounded-lg overflow-hidden">
            <thead className="bg-zinc-100">
              <tr>
                <Th>ID</Th><Th>Name</Th><Th>Quota</Th><Th>Plan</Th><Th>From</Th><Th>To</Th>
              </tr>
            </thead>
            <tbody>
              {payees.slice(0, 20).map(p => (
                <tr key={p.id} className="border-b border-zinc-100 hover:bg-zinc-50">
                  <Td mono>{p.id}</Td><Td>{p.name}</Td><Td>{p.quota}</Td>
                  <Td mono>{p.plan_id}</Td><Td>{p.effective_from}</Td><Td>{p.effective_to}</Td>
                </tr>
              ))}
            </tbody>
          </table>
          {payees.length > 20 && <p className="text-xs text-zinc-400">+ {payees.length - 20} more</p>}
          <div className="flex gap-2">
            <button onClick={() => setPayees([])} className="text-xs text-zinc-500 hover:text-zinc-700">Clear</button>
            <button onClick={() => document.getElementById("payee-reupload")?.click()} className="text-xs text-blue-600 hover:underline">Re-upload</button>
            <input id="payee-reupload" type="file" accept=".csv,.xlsx" className="hidden" onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f); }} />
          </div>
          <div className="flex justify-end">
            <StepButton onClick={onNext} highlight>{payees.length} payees loaded →</StepButton>
          </div>
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
function StepButton({ onClick, highlight, children }: { onClick: () => void; highlight?: boolean; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors cursor-pointer
      ${highlight ? "bg-blue-600 text-white hover:bg-blue-700" : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200"}`}>
      {children}
    </button>
  );
}

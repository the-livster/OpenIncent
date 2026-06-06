import { useState } from "react";
import type { CalculateResponse, Commission } from "../types";

interface Props { result: CalculateResponse; onNext: () => void; onBack: () => void; }

export default function StageEarnings({ result, onNext, onBack }: Props) {
  const [expandedPayee, setExpandedPayee] = useState<string | null>(null);
  const commissions = result.commissions || [];

  // Group by payee, then by rule
  const byPayee: Record<string, Commission[]> = {};
  for (const c of commissions) {
    if (c.rule_id === "payout_cap" || c.rule_id === "draw" || c.rule_id === "mbo" || c.rule_id === "manual_adjustment") continue;
    (byPayee[c.payee_id] ??= []).push(c);
  }

  const payees = Object.keys(byPayee).sort();

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-bold text-zinc-800">6. Earnings</h1>
      <p className="text-sm text-zinc-500">Commission earned per payee, grouped by rule. Click a payee to drill into rule-level detail.</p>

      <div className="space-y-2">
        {payees.map(pid => {
          const lines = byPayee[pid];
          const total = lines.reduce((s, c) => s + parseFloat(c.commission_amount || "0"), 0);
          const rules = [...new Set(lines.map(c => c.rule_id))];
          return (
            <div key={pid} className="border border-zinc-200 rounded-lg overflow-hidden">
              <button
                onClick={() => setExpandedPayee(expandedPayee === pid ? null : pid)}
                className="w-full flex items-center justify-between px-4 py-2.5 text-left hover:bg-zinc-50"
              >
                <span className="text-sm font-medium text-zinc-700">{pid}</span>
                <span className="text-sm font-semibold text-zinc-800">${total.toFixed(2)}</span>
              </button>
              {expandedPayee === pid && (
                <div className="border-t border-zinc-100 px-4 py-2 bg-zinc-50/50">
                  <table className="w-full text-xs">
                    <thead><tr><Th>Rule</Th><Th>Lines</Th><Th>Amount</Th></tr></thead>
                    <tbody>
                      {rules.map(rid => {
                        const ruleLines = lines.filter(c => c.rule_id === rid);
                        const ruleTotal = ruleLines.reduce((s, c) => s + parseFloat(c.commission_amount || "0"), 0);
                        return (
                          <tr key={rid} className="border-b border-zinc-100">
                            <Td mono>{rid}</Td><Td>{ruleLines.length}</Td><Td>${ruleTotal.toFixed(2)}</Td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="flex justify-between">
        <button onClick={onBack} className="text-sm text-zinc-500 hover:text-zinc-700">← Back</button>
        <button onClick={onNext} className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 cursor-pointer">Continue →</button>
      </div>
    </div>
  );
}
function Th({ children }: { children: React.ReactNode }) { return <th className="px-3 py-2 text-left font-medium text-zinc-500 whitespace-nowrap">{children}</th>; }
function Td({ children, mono }: { children: React.ReactNode; mono?: boolean }) { return <td className={`px-3 py-1.5 whitespace-nowrap ${mono ? "font-mono text-zinc-600" : "text-zinc-700"}`}>{children}</td>; }

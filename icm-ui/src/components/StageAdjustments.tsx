import type { CalculateResponse, Commission } from "../types";
import { Th, Td } from "./Table";

interface Props { result: CalculateResponse; onNext: () => void; onBack: () => void; }

const ADJ_RULES = ["payout_cap", "draw", "mbo", "manual_adjustment"];

export default function StageAdjustments({ result, onNext, onBack }: Props) {
  const commissions = result.commissions || [];
  const drawBalances = result.draw_balances;

  // Find true_up entries in ledger
  const ledger = result.ledger;
  const trueUps = (ledger || []).filter(e => e.event_type === "true_up");

  // Group adjustment commissions by type
  const byType: Record<string, Commission[]> = {};
  for (const c of commissions) {
    if (ADJ_RULES.includes(c.rule_id)) {
      (byType[c.rule_id] ??= []).push(c);
}
  const labels: Record<string, string> = {
    payout_cap: "Plan Payout Cap",
    draw: "Draw / Guarantee",
    mbo: "MBO / Bonus",
    manual_adjustment: "Manual Adjustments",
  };

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-bold text-zinc-800">8. Payout Adjustments</h1>
      <p className="text-sm text-zinc-500">Non-commission lines that affect the final payout: caps, draws, MBOs, true-ups, and manual adjustments.</p>

      {Object.entries(byType).map(([ruleId, lines]) => {
        const total = lines.reduce((s, c) => s + parseFloat(c.commission_amount || "0"), 0);
        return (
          <div key={ruleId} className="border border-zinc-200 rounded-lg p-4">
            <h3 className="text-sm font-semibold text-zinc-700">{labels[ruleId] || ruleId}</h3>
            <p className="text-xs text-zinc-500 mb-2">{lines.length} line(s)</p>
            <table className="w-full text-xs">
              <thead><tr><Th>Payee</Th><Th>Amount</Th><Th>Notes</Th></tr></thead>
              <tbody>
                {lines.map((c, i) => (
                  <tr key={i} className="border-b border-zinc-100">
                    <Td mono>{c.payee_id}</Td>
                    <Td className={parseFloat(c.commission_amount) < 0 ? "text-red-600" : "text-green-700"}>
                      {parseFloat(c.commission_amount) >= 0 ? "+" : ""}${Math.abs(parseFloat(c.commission_amount)).toFixed(2)}
                    </Td>
                    <Td className="text-zinc-500">{c.notes}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-xs font-semibold text-zinc-600 mt-1">Total: ${total.toFixed(2)}</p>
          </div>
        );
      })}

      {/* True-ups */}
      {trueUps.length > 0 && (
        <div className="border border-zinc-200 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-zinc-700">True-Ups (Locked Periods)</h3>
          <div className="space-y-1 mt-2">
            {trueUps.map((e, i) => (
              <div key={i} className="text-xs text-zinc-600">{e.human_readable}</div>
            ))}
          </div>
        </div>
      )}

      {/* Draw Balances */}
      {drawBalances && Object.keys(drawBalances).length > 0 && (
        <div className="border border-zinc-200 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-zinc-700">Draw Balances</h3>
          <div className="space-y-1 mt-2">
            {Object.entries(drawBalances).map(([pid, bal]) => (
              <div key={pid} className="text-xs text-zinc-600">{pid}: ${parseFloat(bal).toFixed(2)}</div>
            ))}
          </div>
        </div>
      )}

      <div className="flex justify-between">
        <button onClick={onBack} className="text-sm text-zinc-500 hover:text-zinc-700">← Back</button>
        <button onClick={onNext} className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 cursor-pointer">Continue →</button>
      </div>
    </div>
  );
}
}

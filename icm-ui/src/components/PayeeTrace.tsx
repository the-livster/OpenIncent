import { useMemo, useState } from "react";
import type { Commission, LedgerEntry } from "../types";

interface Props {
  payeeId: string;
  period: string;
  commissions: Commission[];
  ledger: LedgerEntry[];
  onClose: () => void;
}

interface StageLine {
  txn_id: string;
  base: string;
  rate: string;
  amount: string;
  notes: string;
}

interface Stage {
  id: string;
  label: string;
  kind: "computation" | "adjustment" | "cross_period" | "manual" | "final";
  inputs: Record<string, string>;
  output: Record<string, unknown>;
  items?: { type: string; amount: string; reason?: string; origin?: string; note: string }[];
  byRule?: Record<string, { lines: StageLine[]; total: string }>;
}

export default function PayeeTrace({ payeeId, period, commissions, ledger, onClose }: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const stages = useMemo(() => buildStages(payeeId, period, commissions, ledger), [payeeId, period, commissions, ledger]);

  const toggle = (id: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const kindColor: Record<string, string> = {
    computation: "border-blue-200 bg-blue-50/50",
    adjustment: "border-amber-200 bg-amber-50/50",
    cross_period: "border-purple-200 bg-purple-50/50",
    manual: "border-teal-200 bg-teal-50/50",
    final: "border-green-300 bg-green-50",
  };

  return (
    <div className="fixed inset-y-0 right-0 w-[460px] max-w-[92vw] bg-white border-l border-line shadow-xl z-50 flex flex-col animate-in">
      <div className="flex items-center justify-between px-5 py-4 border-b border-line shrink-0">
        <div>
          <h3 className="text-sm font-semibold text-ink">Payout Trace</h3>
          <p className="text-xs text-ink2 mt-0.5">{payeeId} &middot; {period}</p>
        </div>
        <button onClick={onClose} className="text-ink2 hover:text-ink cursor-pointer text-lg px-1">×</button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {stages.map((stage, i) => (
          <div key={stage.id} className={`rounded-lg border ${kindColor[stage.kind] ?? "border-line bg-soft"} overflow-hidden`}>
            <button
              onClick={() => toggle(stage.id)}
              className="w-full flex items-center justify-between px-3 py-2.5 text-left cursor-pointer hover:bg-black/5 transition-colors"
            >
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-ink2 w-5 text-right">{i + 1}</span>
                <span className="text-xs font-semibold text-ink">{stage.label}</span>
                {stage.output.total != null && (
                  <span className="text-xs font-mono font-semibold text-ink">${String(stage.output.total)}</span>
                )}
              </div>
              <span className="text-xs text-ink2">{expanded.has(stage.id) ? "▾" : "▸"}</span>
            </button>

            {expanded.has(stage.id) && (
              <div className="px-3 pb-3 space-y-2 text-xs border-t border-black/5 pt-2">
                {/* Computation: per-rule breakdown */}
                {stage.kind === "computation" && stage.byRule && (
                  <div className="space-y-2">
                    {Object.entries(stage.byRule).map(([rid, rule]) => (
                      <div key={rid}>
                        <div className="flex justify-between text-ink font-medium mb-1">
                          <span>{rid}</span>
                          <span className="font-mono">${rule.total}</span>
                        </div>
                        {rule.lines.map((l, j) => (
                          <div key={j} className="flex justify-between text-ink2 pl-3 text-[11px]">
                            <span>{l.txn_id} — {l.notes}</span>
                            <span className="font-mono">${l.amount}</span>
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                )}

                {/* Items: adjustments, cross-period, manual */}
                {stage.items && (
                  <div className="space-y-1">
                    {stage.items.map((item, j) => (
                      <div key={j} className="flex justify-between items-start">
                        <div>
                          <span className="text-ink font-medium">{item.type}</span>
                          {item.reason && <span className="text-ink2 ml-1">— {item.reason}</span>}
                          {item.origin && <span className="text-ink2 ml-1">from {item.origin}</span>}
                          {item.note && <div className="text-ink2 text-[11px]">{item.note}</div>}
                        </div>
                        <span className={`font-mono font-medium ${item.amount.startsWith("-") ? "text-red-600" : "text-green-700"}`}>
                          ${item.amount}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Raw inputs */}
                {Object.keys(stage.inputs).length > 0 && (
                  <div className="text-ink2 text-[11px] space-y-0.5">
                    {Object.entries(stage.inputs).map(([k, v]) => (
                      <div key={k} className="flex justify-between"><span>{k}</span><span className="font-mono">{v}</span></div>
                    ))}
                  </div>
                )}

                {stage.output.note != null && (
                  <div className="text-ink2 text-[11px] italic">{String(stage.output.note)}</div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function buildStages(payeeId: string, period: string, commissions: Commission[], _ledger: LedgerEntry[]): Stage[] {
  const myComms = commissions.filter(
    c => c.payee_id === payeeId && (c.period === period || c.origin_period === period || !c.period || c.period === period)
  );

  const stages: Stage[] = [];

  // -- Rules --
  const ruleLines: Record<string, { lines: StageLine[]; total: string }> = {};
  const nonRuleIds = new Set(["payout_cap", "draw", "manual_adjustment"]);
  for (const c of myComms) {
    if (nonRuleIds.has(c.rule_id)) continue;
    if (!ruleLines[c.rule_id]) ruleLines[c.rule_id] = { lines: [], total: "0" };
    ruleLines[c.rule_id].lines.push({
      txn_id: c.transaction_id, base: c.base_amount, rate: c.rate,
      amount: c.commission_amount, notes: c.notes,
    });
  }
  for (const rid of Object.keys(ruleLines)) {
    ruleLines[rid].total = ruleLines[rid].lines.reduce((s, l) => s + parseFloat(l.amount), 0).toFixed(2);
  }
  const ruleTotal = Object.values(ruleLines).reduce((s, r) => s + parseFloat(r.total), 0).toFixed(2);
  stages.push({
    id: "rules", label: "Rules", kind: "computation",
    inputs: {}, output: { total: ruleTotal },
    byRule: ruleLines,
  });

  // -- Plan cap --
  const capLine = myComms.find(c => c.rule_id === "payout_cap");
  let running = parseFloat(ruleTotal);
  if (capLine) {
    const capAdj = parseFloat(capLine.commission_amount);
    running += capAdj;
    stages.push({
      id: "plan_cap", label: "Plan Cap", kind: "adjustment",
      inputs: { earned: ruleTotal, cap_adj: capLine.commission_amount },
      output: { total: running.toFixed(2), adjustment: capLine.commission_amount,
                note: `capped: ${ruleTotal} → ${running.toFixed(2)}` },
    });
  }

  // -- Draw --
  const drawLines = myComms.filter(c => c.rule_id === "draw");
  if (drawLines.length > 0) {
    const drawTotal = drawLines.reduce((s, c) => s + parseFloat(c.commission_amount), 0);
    running += drawTotal;
    stages.push({
      id: "draw", label: "Draw", kind: "adjustment",
      inputs: drawLines[0]?.notes ? { detail: drawLines[0].notes } : {},
      output: { total: running.toFixed(2), adjustment: drawTotal.toFixed(2),
                note: drawLines[0]?.notes ?? "" },
    });
  }

  // -- Cross-period --
  const crossItems = myComms
    .filter(c => c.origin_period && c.origin_period !== c.period && c.rule_id !== "draw")
    .map(c => ({ type: "true_up", origin: c.origin_period, amount: c.commission_amount, note: c.notes }));
  if (crossItems.length > 0) {
    const crossTotal = crossItems.reduce((s, it) => s + parseFloat(it.amount), 0);
    running += crossTotal;
    stages.push({ id: "cross_period", label: "Cross-Period", kind: "cross_period", inputs: {},
                  items: crossItems, output: { total: running.toFixed(2) } });
  }

  // -- Manual --
  const manualLines = myComms.filter(c => c.rule_id === "manual_adjustment");
  if (manualLines.length > 0) {
    const items = manualLines.map(c => ({ type: "manual_adjustment", amount: c.commission_amount, reason: c.notes, note: "" }));
    const manTotal = manualLines.reduce((s, c) => s + parseFloat(c.commission_amount), 0);
    running += manTotal;
    stages.push({ id: "manual", label: "Manual Adj", kind: "manual", inputs: {},
                  items, output: { total: running.toFixed(2) } });
  }

  // -- Final --
  stages.push({ id: "final", label: "Payout", kind: "final", inputs: {}, output: { total: running.toFixed(2) } });

  return stages;
}

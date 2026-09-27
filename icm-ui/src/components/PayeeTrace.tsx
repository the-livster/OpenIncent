import { useMemo, useState } from "react";
import type { Commission, LedgerEntry } from "../types";
import { Drawer, Icon } from "./ui";
import { cx } from "./ui/cx";

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

// The dot on the timeline, by kind of stage.
const KIND: Record<Stage["kind"], { dot: string }> = {
  computation: { dot: "bg-accent" },
  adjustment: { dot: "bg-warning" },
  cross_period: { dot: "bg-[#8b5cf6]" },
  manual: { dot: "bg-[#14b8a6]" },
  final: { dot: "bg-success" },
};

/** How one payee's payout was built, step by step: rules, then each
 *  adjustment in the order the engine applied it, to the final figure. */
export default function PayeeTrace({ payeeId, period, commissions, ledger, onClose }: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["rules"]));

  const stages = useMemo(() => buildStages(payeeId, period, commissions, ledger), [payeeId, period, commissions, ledger]);

  const toggle = (id: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <Drawer open onClose={onClose} title="Payout trace" subtitle={`${payeeId} · ${period}`} width={500}>
      <ol className="relative space-y-3 px-5 py-5 before:absolute before:bottom-8 before:left-[33px] before:top-8 before:w-px before:bg-line">
        {stages.map((stage) => {
          const open = expanded.has(stage.id);
          const kind = KIND[stage.kind];
          const final = stage.kind === "final";
          return (
            <li key={stage.id} className="relative">
              <div className={cx("overflow-hidden rounded-xl border bg-surface", final ? "border-success/40" : "border-line")}>
                <button
                  onClick={() => toggle(stage.id)}
                  aria-expanded={open}
                  className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left hover:bg-surface-2"
                >
                  <span className="flex items-center gap-3">
                    <span className="relative z-10 grid h-6 w-6 place-items-center rounded-full border border-line bg-surface">
                      <span className={cx("h-2 w-2 rounded-full", kind.dot)} />
                    </span>
                    <span className={cx("text-[13px]", final ? "font-semibold text-ink" : "font-medium text-ink")}>{stage.label}</span>
                  </span>
                  <span className="flex items-center gap-2">
                    {stage.output.total != null && (
                      <span className={cx("num text-[13px]", final ? "font-semibold text-success-ink" : "font-medium text-ink")}>
                        {String(stage.output.total)}
                      </span>
                    )}
                    <Icon name="chevronRight" size={14} className={cx("text-ink-3 transition-transform", open && "rotate-90")} />
                  </span>
                </button>

                {open && (
                  <div className="space-y-2 border-t border-line px-4 py-3 text-[12.5px]">
                    {/* Computation: per-rule breakdown */}
                    {stage.kind === "computation" && stage.byRule && (
                      <div className="space-y-3">
                        {Object.entries(stage.byRule).map(([rid, rule]) => (
                          <div key={rid}>
                            <div className="mb-1 flex justify-between font-medium text-ink">
                              <span className="font-mono">{rid}</span>
                              <span className="num">{rule.total}</span>
                            </div>
                            {rule.lines.map((l, j) => (
                              <div key={j} className="flex justify-between gap-3 pl-3 text-[12px] text-ink-2">
                                <span className="min-w-0">{l.txn_id} — {l.notes}</span>
                                <span className="num shrink-0">{l.amount}</span>
                              </div>
                            ))}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Items: adjustments, cross-period, manual */}
                    {stage.items && (
                      <div className="space-y-1.5">
                        {stage.items.map((item, j) => (
                          <div key={j} className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <span className="font-medium text-ink">{item.type}</span>
                              {item.reason && <span className="ml-1 text-ink-2">— {item.reason}</span>}
                              {item.origin && <span className="ml-1 text-ink-2">from {item.origin}</span>}
                              {item.note && <div className="text-[12px] text-ink-2">{item.note}</div>}
                            </div>
                            <span className={cx("num shrink-0 font-medium", item.amount.startsWith("-") ? "text-danger-ink" : "text-success-ink")}>
                              {item.amount}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Raw inputs */}
                    {Object.keys(stage.inputs).length > 0 && (
                      <div className="space-y-0.5 text-[12px] text-ink-2">
                        {Object.entries(stage.inputs).map(([k, v]) => (
                          <div key={k} className="flex justify-between"><span>{k}</span><span className="num font-mono">{v}</span></div>
                        ))}
                      </div>
                    )}

                    {stage.output.note != null && (
                      <div className="text-[12px] italic text-ink-2">{String(stage.output.note)}</div>
                    )}
                    {final && !stage.items && !stage.byRule && (
                      <div className="text-[12px] text-ink-2">The amount this payee is paid for the period.</div>
                    )}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </Drawer>
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

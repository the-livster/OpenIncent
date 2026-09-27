import { useState } from "react";
import type { CalculateResponse, Commission } from "../types";
import { money, sumCents } from "../format";
import { Th, Td } from "./Table";
import StageShell from "./StageShell";
import { EmptyState, Icon, Stat } from "./ui";
import { cx } from "./ui/cx";

interface Props { result: CalculateResponse; onNext: () => void; onBack: () => void; }

const ADJUSTMENT_RULES = new Set(["payout_cap", "draw", "mbo", "manual_adjustment"]);

export default function StageEarnings({ result, onNext, onBack }: Props) {
  const [expandedPayee, setExpandedPayee] = useState<string | null>(null);
  const commissions = result.commissions || [];

  // Group by payee, then by rule
  const byPayee: Record<string, Commission[]> = {};
  for (const c of commissions) {
    if (ADJUSTMENT_RULES.has(c.rule_id)) continue;
    (byPayee[c.payee_id] ??= []).push(c);
  }

  const totals = Object.entries(byPayee)
    .map(([pid, lines]) => ({ pid, lines, total: sumCents(lines.map(c => c.commission_amount)) }))
    .sort((a, b) => b.total - a.total);
  const grand = totals.reduce((s, t) => s + t.total, 0);
  const top = Math.max(...totals.map(t => Math.abs(t.total)), 1);
  const names = Object.fromEntries((result.payouts ?? []).map(p => [p.payee_id, p.name]));

  return (
    <StageShell
      n={7}
      group="Results"
      title="Earnings"
      description="Commission earned per payee, grouped by rule. Open a payee to see which rules paid them."
      onBack={onBack}
      onNext={onNext}
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Commission earned" value={money(grand)} hint="Before caps, draws and adjustments" icon="coins" />
        <Stat label="Payees earning" value={totals.length} icon="users" />
        <Stat label="Commission lines" value={totals.reduce((s, t) => s + t.lines.length, 0)} icon="listChecks" />
      </div>

      {totals.length === 0 ? (
        <EmptyState icon="coins" title="No commission earned in this run" compact />
      ) : (
        <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line">
          {totals.map(({ pid, lines, total }) => {
            const open = expandedPayee === pid;
            const rules = [...new Set(lines.map(c => c.rule_id))];
            return (
              <li key={pid}>
                <button
                  onClick={() => setExpandedPayee(open ? null : pid)}
                  aria-expanded={open}
                  className="grid w-full grid-cols-[16px_minmax(0,1fr)_110px] items-center gap-4 px-4 py-3 text-left transition-colors hover:bg-surface-2 sm:grid-cols-[16px_minmax(0,1fr)_minmax(0,220px)_110px]"
                >
                  <Icon name="chevronRight" size={14} className={cx("text-ink-3 transition-transform", open && "rotate-90")} />
                  <span className="min-w-0">
                    <span className="block truncate text-[13.5px] font-medium text-ink">{names[pid] || pid}</span>
                    <span className="block font-mono text-[11.5px] text-ink-3">{pid}</span>
                  </span>
                  <span className="hidden h-1.5 overflow-hidden rounded-full bg-surface-3 sm:block">
                    <span className={cx("block h-full rounded-full", total < 0 ? "bg-danger" : "bg-accent")}
                      style={{ width: `${(Math.abs(total) / top) * 100}%` }} />
                  </span>
                  <span className="num text-right text-[13.5px] font-semibold text-ink">{money(total)}</span>
                </button>
                {open && (
                  <div className="border-t border-line bg-surface-2/50 px-4 py-3">
                    <div className="overflow-hidden rounded-lg border border-line bg-surface">
                      <table>
                        <thead><tr><Th>Rule</Th><Th className="text-right">Lines</Th><Th className="text-right">Amount</Th></tr></thead>
                        <tbody>
                          {rules.map(rid => {
                            const ruleLines = lines.filter(c => c.rule_id === rid);
                            return (
                              <tr key={rid}>
                                <Td mono>{rid}</Td>
                                <Td num>{ruleLines.length}</Td>
                                <Td num className="font-medium">{money(sumCents(ruleLines.map(c => c.commission_amount)))}</Td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </StageShell>
  );
}

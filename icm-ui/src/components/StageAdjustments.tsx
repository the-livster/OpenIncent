import type { CalculateResponse, Commission } from "../types";
import { money, sumCents } from "../format";
import { Th, Td } from "./Table";
import StageShell from "./StageShell";
import { Card, CardHeader, EmptyState, Icon, type IconName } from "./ui";
import { cx } from "./ui/cx";

interface Props { result: CalculateResponse; onNext: () => void; onBack: () => void; }

const ADJ_RULES = ["payout_cap", "draw", "mbo", "manual_adjustment"];

const LABELS: Record<string, { title: string; icon: IconName; hint: string }> = {
  payout_cap: { title: "Plan payout cap", icon: "shield", hint: "Earnings above the plan's cap, held back" },
  draw: { title: "Draw / guarantee", icon: "scale", hint: "Top-ups paid and recoveries taken" },
  mbo: { title: "MBO / bonus", icon: "target", hint: "Non-commission payouts" },
  manual_adjustment: { title: "Manual adjustments", icon: "pencil", hint: "Entered by hand, each with a reason" },
};

export default function StageAdjustments({ result, onNext, onBack }: Props) {
  const commissions = result.commissions || [];
  const drawBalances = result.draw_balances;

  // Find true_up entries in ledger
  const trueUps = (result.ledger || []).filter(e => e.event_type === "true_up");

  // Group adjustment commissions by type
  const byType: Record<string, Commission[]> = {};
  for (const c of commissions) {
    if (ADJ_RULES.includes(c.rule_id)) {
      (byType[c.rule_id] ??= []).push(c);
    }
  }
  const balances = Object.entries(drawBalances ?? {});
  const nothing = Object.keys(byType).length === 0 && trueUps.length === 0 && balances.length === 0;

  return (
    <StageShell
      n={8}
      group="Results"
      title="Payout adjustments"
      description="Lines that change the final payout without being commission: caps, draws, bonuses, true-ups and manual adjustments."
      onBack={onBack}
      onNext={onNext}
    >
      {nothing && (
        <EmptyState icon="checkCircle" title="No adjustments in this run"
          description="Every payout is exactly the commission earned." compact />
      )}

      {Object.entries(byType).map(([ruleId, lines]) => {
        const label = LABELS[ruleId] ?? { title: ruleId, icon: "layers" as IconName, hint: "" };
        const total = sumCents(lines.map(c => c.commission_amount));
        return (
          <Card key={ruleId}>
            <CardHeader
              icon={label.icon}
              title={label.title}
              description={`${lines.length} line(s) · ${label.hint}`}
              actions={<Amount value={total} strong />}
            />
            <div className="table-wrap">
              <table>
                <thead><tr><Th>Payee</Th><Th className="text-right">Amount</Th><Th>Notes</Th></tr></thead>
                <tbody>
                  {lines.map((c, i) => (
                    <tr key={i}>
                      <Td mono>{c.payee_id}</Td>
                      <Td className="text-right"><Amount value={parseFloat(c.commission_amount)} /></Td>
                      <Td className="whitespace-normal text-ink-2">{c.notes}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        );
      })}

      {trueUps.length > 0 && (
        <Card>
          <CardHeader icon="history" title="True-ups on locked periods"
            description="Changes to periods already paid, settled in this one" />
          <ul className="divide-y divide-line">
            {trueUps.map((e, i) => (
              <li key={i} className="px-5 py-2.5 text-[13px] text-ink-2">{e.human_readable}</li>
            ))}
          </ul>
        </Card>
      )}

      {balances.length > 0 && (
        <Card>
          <CardHeader icon="scale" title="Draw balances" description="Still to recover after this run" />
          <ul className="divide-y divide-line">
            {balances.map(([pid, bal]) => (
              <li key={pid} className="flex items-center justify-between px-5 py-2.5 text-[13px]">
                <span className="font-mono text-ink">{pid}</span>
                <span className="num font-medium text-ink">{money(bal)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </StageShell>
  );
}

function Amount({ value, strong }: { value: number; strong?: boolean }) {
  const negative = value < 0;
  return (
    <span className={cx("num inline-flex items-center gap-1", strong ? "text-[15px] font-semibold" : "font-medium",
      negative ? "text-danger-ink" : "text-success-ink")}>
      <Icon name={negative ? "arrowDown" : "arrowUp"} size={13} />
      {negative ? "−" : "+"}{money(Math.abs(value))}
    </span>
  );
}

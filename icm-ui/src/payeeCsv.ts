import type { PayeeRow } from "./components/Pipeline";

const fields = ["id", "name", "quota", "plan_id", "effective_from", "effective_to", "email",
  "ramp_months", "ramp_schedule", "category_quotas", "draw_amount", "draw_recoverable",
  "manager_id", "manager_override", "team_id"] as const;

/** Preserve exact decimal strings, quoted names and per-period quota overrides. */
export function payeeCsv(payees: PayeeRow[]): string {
  const quote = (value: string) => `"${value.replaceAll('"', '""')}"`;
  const rows = payees.flatMap(p => [
    [...fields.map(key => p[key] ?? ""), ""],
    ...Object.entries(p.quotas ?? {}).map(([period, quota]) =>
      [...fields.map(key => key === "quota" ? quota : p[key] ?? ""), period]),
  ]);
  return [[...fields, "period"].join(","), ...rows.map(row => row.map(quote).join(","))].join("\r\n");
}

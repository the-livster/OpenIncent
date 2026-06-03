import { useMemo } from "react";
import type { CalculateResponse, Commission } from "../types";

interface Props {
  data: CalculateResponse;
}

function sumCents(values: string[]): string {
  let total = 0;
  for (const v of values) {
    const parts = v.split(".");
    const dollars = parseInt(parts[0], 10) || 0;
    const cents = parseInt((parts[1] ?? "0").padEnd(2, "0").slice(0, 2), 10);
    total += dollars * 100 + (dollars < 0 ? -cents : cents);
  }
  const sign = total < 0 ? "-" : "";
  const abs = Math.abs(total);
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}

export default function ResultsDashboard({ data }: Props) {
  const totalCommission = useMemo(
    () => sumCents(Object.values(data.summary)),
    [data.summary]
  );

  const uniqueRules = useMemo(
    () => new Set(data.commissions.map((c) => c.rule_id)).size,
    [data.commissions]
  );

  const payeeSummary = useMemo(() => {
    const map: Record<string, { total: number; count: number }> = {};
    for (const c of data.commissions) {
      if (!map[c.payee_id]) map[c.payee_id] = { total: 0, count: 0 };
      map[c.payee_id].total += parseFloat(c.commission_amount);
      map[c.payee_id].count++;
    }
    return Object.entries(map)
      .map(([id, v]) => ({ id, ...v }))
      .sort((a, b) => b.total - a.total);
  }, [data.commissions]);

  const stats = [
    { label: "Total Commission", value: `$${totalCommission}`, accent: "text-success" },
    { label: "Payees", value: String(Object.keys(data.summary).length), accent: "text-accent" },
    { label: "Commission Lines", value: String(data.commissions.length), accent: "text-brand-300" },
    { label: "Rules Triggered", value: String(uniqueRules), accent: "text-warn" },
  ];

  return (
    <div className="space-y-6 animate-in">
      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 stagger">
        {stats.map((s) => (
          <div
            key={s.label}
            className="card p-4 hover:scale-[1.02] transition-transform duration-200"
          >
            <div className="text-xs font-medium text-ink2 uppercase tracking-wider">
              {s.label}
            </div>
            <div className={`text-2xl font-bold mt-1 ${s.accent}`}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* Payee breakdown */}
      {payeeSummary.length > 1 && (
        <div className="card p-4">
          <h3 className="text-sm font-semibold text-ink mb-3">Per-Payee Breakdown</h3>
          <div className="space-y-2">
            {payeeSummary.map((p) => {
              const pct = (p.total / parseFloat(totalCommission)) * 100;
              return (
                <div key={p.id} className="flex items-center gap-3">
                  <span className="text-xs font-mono text-ink2 w-24 truncate">{p.id}</span>
                  <div className="flex-1 h-2 rounded-full bg-surface-200 overflow-hidden">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-accent to-brand-400 transition-all duration-500"
                      style={{ width: `${Math.min(pct, 100)}%` }}
                    />
                  </div>
                  <span className="text-xs font-mono text-ink w-20 text-right">
                    ${p.total.toFixed(2)}
                  </span>
                  <span className="text-xs text-ink2 w-10 text-right">
                    {p.count} txn
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Download buttons */}
      <div className="flex flex-wrap gap-2">
        <DownloadBtn
          label="Commissions CSV"
          onClick={() => downloadCSV(data.commissions)}
        />
        <DownloadBtn
          label="Summary CSV"
          onClick={() => downloadSummary(data.summary)}
        />
        <DownloadBtn
          label="Ledger JSONL"
          onClick={() => download("ledger.jsonl", data.ledger.map((e) => JSON.stringify(e)).join("\n"), "application/jsonl")}
        />
      </div>
    </div>
  );
}

function DownloadBtn({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="
        inline-flex items-center gap-1.5 px-3 py-1.5
        rounded-lg text-xs font-medium
        bg-surface-200/80 text-ink hover:bg-surface-300
        border border-line
        transition-colors cursor-pointer
      "
    >
      <span>↓</span> {label}
    </button>
  );
}

function escapeCSVField(value: string): string {
  if (value.includes(",") || value.includes('"') || value.includes("\n")) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

function downloadCSV(commissions: Commission[]) {
  const header = "transaction_id,payee_id,period,rule_id,base_amount,rate,commission_amount,notes";
  const rows = commissions.map((c) =>
    [c.transaction_id, c.payee_id, c.period, c.rule_id, c.base_amount, c.rate, c.commission_amount, c.notes]
      .map(escapeCSVField)
      .join(",")
  );
  download("commissions.csv", [header, ...rows].join("\n"), "text/csv");
}

function downloadSummary(summary: Record<string, string>) {
  const rows = ["payee_id,total_commission"];
  for (const [id, total] of Object.entries(summary)) rows.push(`${id},${total}`);
  download("summary.csv", rows.join("\n"), "text/csv");
}

function download(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

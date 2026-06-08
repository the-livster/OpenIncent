import type { CalculateResponse } from "../types";

interface Props {
  data: CalculateResponse;
  onExport: () => void;
  onStartNew: () => void;
}

export default function CalcResultsView({ data, onExport, onStartNew }: Props) {
  // Sum in integer cents to avoid floating-point drift vs the backend total.
  const total = Object.values(data.summary).reduce(
    (a, b) => a + Math.round(parseFloat(b) * 100), 0,
  ) / 100;
  const pctFmt = { minimumFractionDigits: 2, maximumFractionDigits: 2 } as const;

  return (
    <div className="space-y-6 animate-in">
      {/* Summary cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <StatCard
          label="Total Commission"
          value={`$${total.toLocaleString(undefined, pctFmt)}`}
        />
        <StatCard
          label="Payees"
          value={String(Object.keys(data.summary).length)}
        />
        <StatCard
          label="Commission Lines"
          value={String(data.commissions.length)}
        />
      </div>

      {/* Export button */}
      <div className="flex justify-end">
        <button
          onClick={onExport}
          className="
            inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium
            bg-soft border border-line text-ink
            hover:bg-soft hover:border-ink2
            transition-all cursor-pointer
          "
        >
          ↓ Download Statements (.xlsx)
        </button>
      </div>

      {/* Per-payee breakdown */}
      <div className="card overflow-hidden">
        <div className="px-5 py-3 border-b border-line">
          <h3 className="text-sm font-semibold text-ink">Per Payee</h3>
        </div>
        <div className="divide-y divide-line">
          {Object.entries(data.summary).map(([payee, total]) => (
            <div
              key={payee}
              className="px-5 py-2.5 flex justify-between items-center text-sm"
            >
              <span className="text-ink font-medium">{payee}</span>
              <span className="text-ink font-mono">
                ${parseFloat(total).toLocaleString(undefined, pctFmt)}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Commission details */}
      <div className="card overflow-hidden">
        <div className="px-5 py-3 border-b border-line">
          <h3 className="text-sm font-semibold text-ink">
            All Commissions ({data.commissions.length})
          </h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-soft">
                <th className="px-3 py-2 text-left font-medium text-ink2">Deal</th>
                <th className="px-3 py-2 text-left font-medium text-ink2">Payee</th>
                <th className="px-3 py-2 text-left font-medium text-ink2">Rule</th>
                <th className="px-3 py-2 text-right font-medium text-ink2">Amount</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {data.commissions.map((c, i) => (
                <tr key={i} className="hover:bg-soft">
                  <td className="px-3 py-1.5 text-ink font-mono">{c.transaction_id}</td>
                  <td className="px-3 py-1.5 text-ink">{c.payee_id}</td>
                  <td className="px-3 py-1.5 text-ink2">{c.rule_id}</td>
                  <td className="px-3 py-1.5 text-ink font-mono text-right">
                    ${parseFloat(c.commission_amount).toLocaleString(undefined, pctFmt)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex justify-center">
        <TextButton onClick={onStartNew}>
          ← Start New Calculation
        </TextButton>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------
// Shared sub-components (also used elsewhere in CalculatorWizard)
// ------------------------------------------------------------------

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="card p-4">
      <div className="text-xs text-ink2">{label}</div>
      <div className="text-lg font-bold text-ink mt-0.5">{value}</div>
    </div>
  );
}

function TextButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className="text-sm text-ink2 hover:text-ink transition-colors cursor-pointer">
      {children}
    </button>
  );
}

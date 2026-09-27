import type { CalculateResponse } from "../types";
import { money, sumCents } from "../format";
import { Button, Card, CardHeader, Stat } from "./ui";

interface Props {
  data: CalculateResponse;
  onExport: () => void;
  onStartNew: () => void;
}

export default function CalcResultsView({ data, onExport, onStartNew }: Props) {
  // Sum in integer cents to avoid floating-point drift vs the backend total.
  const total = sumCents(Object.values(data.summary));
  const top = Math.max(...Object.values(data.summary).map(v => Math.abs(parseFloat(v))), 1);

  return (
    <div className="space-y-5 animate-in">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button variant="ghost" size="sm" icon="arrowLeft" className="-ml-2" onClick={onStartNew}>
          Start a new calculation
        </Button>
        <Button variant="primary" icon="download" onClick={onExport}>Download statements (.xlsx)</Button>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Stat label="Total commission" value={money(total)} icon="coins" />
        <Stat label="Payees" value={String(Object.keys(data.summary).length)} icon="users" />
        <Stat label="Commission lines" value={String(data.commissions.length)} icon="listChecks" />
      </div>

      <Card className="overflow-hidden">
        <CardHeader title="Per payee" />
        <ul className="divide-y divide-line">
          {Object.entries(data.summary).map(([payee, amount]) => (
            <li key={payee} className="grid grid-cols-[minmax(0,1fr)_120px] items-center gap-4 px-5 py-2.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,200px)_120px]">
              <span className="truncate font-mono text-[13px] text-ink">{payee}</span>
              <span className="hidden h-1.5 overflow-hidden rounded-full bg-surface-3 sm:block">
                <span className="block h-full rounded-full bg-accent"
                  style={{ width: `${(Math.abs(parseFloat(amount)) / top) * 100}%` }} />
              </span>
              <span className="num text-right text-[13.5px] font-semibold text-ink">{money(amount)}</span>
            </li>
          ))}
        </ul>
      </Card>

      <Card className="overflow-hidden">
        <CardHeader title={`All commissions (${data.commissions.length})`} />
        <div className="table-wrap max-h-[480px]">
          <table>
            <thead>
              <tr>
                <th>Deal</th>
                <th>Payee</th>
                <th>Rule</th>
                <th className="text-right">Amount</th>
              </tr>
            </thead>
            <tbody>
              {data.commissions.map((c, i) => (
                <tr key={i}>
                  <td className="font-mono text-[12.5px]">{c.transaction_id}</td>
                  <td className="font-mono text-[12.5px]">{c.payee_id}</td>
                  <td><span className="badge badge-accent">{c.rule_id}</span></td>
                  <td className="num text-right font-medium">{money(c.commission_amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

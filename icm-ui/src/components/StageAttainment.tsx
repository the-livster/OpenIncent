import type { CalculateResponse } from "../types";

interface Props { result: CalculateResponse; onNext: () => void; onBack: () => void; }

export default function StageAttainment({ result, onNext, onBack }: Props) {
  const att = (result as Record<string, unknown>).attainment as Record<string, unknown>[] | undefined;
  const summaries = att ?? [];

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-bold text-zinc-800">5. Attainment</h1>
      <p className="text-sm text-zinc-500">Bookings vs quota per payee and window. This is what drives tier advancement and threshold gates.</p>
      <table className="w-full text-xs border rounded-lg overflow-hidden">
        <thead className="bg-zinc-100">
          <tr>
            <Th>Payee</Th><Th>Window</Th><Th>Bookings</Th><Th>Quota</Th><Th>Attainment %</Th>
          </tr>
        </thead>
        <tbody>
          {summaries.map((a: Record<string, unknown>, i: number) => (
            <tr key={i} className="border-b border-zinc-100 hover:bg-zinc-50">
              <Td mono>{String(a.payee_id ?? "")}</Td>
              <Td mono>{String(a.period ?? "")}</Td>
              <Td>{String(a.bookings ?? "")}</Td>
              <Td>{String(a.quota ?? "")}</Td>
              <Td>{a.attainment_pct != null ? `${(Number(a.attainment_pct) * 100).toFixed(1)}%` : "N/A"}</Td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex justify-between">
        <button onClick={onBack} className="text-sm text-zinc-500 hover:text-zinc-700">← Back</button>
        <button onClick={onNext} className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 cursor-pointer">Continue →</button>
      </div>
    </div>
  );
}
function Th({ children }: { children: React.ReactNode }) { return <th className="px-3 py-2 text-left font-medium text-zinc-500 whitespace-nowrap">{children}</th>; }
function Td({ children, mono }: { children: React.ReactNode; mono?: boolean }) { return <td className={`px-3 py-1.5 whitespace-nowrap ${mono ? "font-mono text-zinc-600" : "text-zinc-700"}`}>{children}</td>; }

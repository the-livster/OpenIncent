import type { PayeeRow } from "./Pipeline";

interface Props {
  payees: PayeeRow[];
  setPayees: (p: PayeeRow[]) => void;
  onNext: () => void;
  onBack: () => void;
}

export default function StageQuotas({ payees, setPayees, onNext, onBack }: Props) {
  function update(idx: number, field: keyof PayeeRow, value: string) {
    const next = [...payees];
    next[idx] = { ...next[idx], [field]: value };
    setPayees(next);
  }

  const missingQuota = payees.filter(p => !p.quota || p.quota === "0");

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-bold text-zinc-800">2. Quotas</h1>
      <p className="text-sm text-zinc-500">Review and adjust each payee's quota, ramp schedule, and per-window overrides.</p>

      {missingQuota.length > 0 && (
        <div className="px-3 py-2 rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs">
          ⚠ {missingQuota.length} payee(s) have no quota set. Attainment will be N/A until fixed.
        </div>
      )}

      <div className="overflow-auto max-h-[60vh] border rounded-lg">
        <table className="w-full text-xs">
          <thead className="bg-zinc-100 sticky top-0">
            <tr>
              <Th>ID</Th><Th>Name</Th><Th>Base Quota</Th><Th>Ramp Months</Th><Th>Ramp Schedule</Th>
            </tr>
          </thead>
          <tbody>
            {payees.map((p, i) => (
              <tr key={p.id || i} className="border-b border-zinc-100 hover:bg-zinc-50">
                <Td mono>{p.id}</Td><Td>{p.name}</Td>
                <Td><input value={p.quota} onChange={e => update(i, "quota", e.target.value)} className="w-24 px-1 py-0.5 rounded border border-zinc-200 text-xs bg-white" /></Td>
                <Td><input value={p.ramp_months} onChange={e => update(i, "ramp_months", e.target.value)} placeholder="optional" className="w-20 px-1 py-0.5 rounded border border-zinc-200 text-xs bg-white" /></Td>
                <Td><input value={p.ramp_schedule} onChange={e => update(i, "ramp_schedule", e.target.value)} placeholder='e.g. 0.25 0.5 0.75' className="w-40 px-1 py-0.5 rounded border border-zinc-200 text-xs bg-white" /></Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex justify-between">
        <button onClick={onBack} className="text-sm text-zinc-500 hover:text-zinc-700">← Back</button>
        <button onClick={onNext} className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 cursor-pointer">
          Continue →
        </button>
      </div>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="px-3 py-2 text-left font-medium text-zinc-500 whitespace-nowrap">{children}</th>;
}
function Td({ children, mono }: { children: React.ReactNode; mono?: boolean }) {
  return <td className={`px-3 py-1.5 whitespace-nowrap ${mono ? "font-mono text-zinc-600" : "text-zinc-700"}`}>{children}</td>;
}

import { useMemo, useState } from "react";
import type { PayeeRow } from "./Pipeline";
import { Th, Td } from "./Table";

interface Props {
  payees: PayeeRow[];
  setPayees: (p: PayeeRow[]) => void;
  onNext: () => void;
  onBack: () => void;
}

type SortCol = "id" | "name" | "quota" | "ramp_months";

export default function StageQuotas({ payees, setPayees, onNext, onBack }: Props) {
  const [sortCol, setSortCol] = useState<SortCol>("id");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  function update(payeeId: string, field: keyof PayeeRow, value: string) {
    setPayees(payees.map(p => p.id === payeeId ? { ...p, [field]: value } : p));
  }

  const toggleSort = (col: SortCol) => {
    if (sortCol === col) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortCol(col); setSortDir("asc"); }
  };

  const sorted = useMemo(() => {
    return [...payees].sort((a, b) => {
      const av = (a[sortCol] ?? "").toLowerCase();
      const bv = (b[sortCol] ?? "").toLowerCase();
      if (sortCol === "quota") {
        return sortDir === "asc" ? parseFloat(av) - parseFloat(bv) : parseFloat(bv) - parseFloat(av);
      }
      return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
    });
  }, [payees, sortCol, sortDir]);

  const missingQuota = payees.filter(p => !p.quota || p.quota === "0");

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-lg font-bold text-zinc-800">2. Quotas</h1>
      <p className="text-sm text-zinc-500">Review and adjust each payee's quota, ramp schedule, and per-window overrides.</p>

      {missingQuota.length > 0 && (
        <div className="px-3 py-2 rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs select-text">
          ⚠ {missingQuota.length} payee(s) have no quota set. Attainment will be N/A until fixed.
        </div>
      )}

      <div className="overflow-auto max-h-[60vh] border rounded-lg">
        <table className="w-full text-xs">
          <thead className="bg-zinc-100 sticky top-0">
            <tr>
              <SortTh col="id" label="ID" current={sortCol} dir={sortDir} onClick={toggleSort} />
              <SortTh col="name" label="Name" current={sortCol} dir={sortDir} onClick={toggleSort} />
              <SortTh col="quota" label="Base Quota" current={sortCol} dir={sortDir} onClick={toggleSort} />
              <Th>Ramp Months</Th><Th>Ramp Schedule</Th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(p => (
              <tr key={p.id} className="border-b border-zinc-100 hover:bg-zinc-50">
                <Td mono>{p.id}</Td><Td>{p.name}</Td>
                <Td><input value={p.quota} onChange={e => update(p.id, "quota", e.target.value)} className="w-24 px-1 py-0.5 rounded border border-zinc-200 text-xs bg-white" /></Td>
                <Td><input value={p.ramp_months} onChange={e => update(p.id, "ramp_months", e.target.value)} placeholder="optional" className="w-20 px-1 py-0.5 rounded border border-zinc-200 text-xs bg-white" /></Td>
                <Td><input value={p.ramp_schedule} onChange={e => update(p.id, "ramp_schedule", e.target.value)} placeholder='e.g. 0.25 0.5 0.75' className="w-40 px-1 py-0.5 rounded border border-zinc-200 text-xs bg-white" /></Td>
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

function SortTh({ col, label, current, dir, onClick }: {
  col: SortCol; label: string; current: SortCol; dir: string; onClick: (c: SortCol) => void;
}) {
  const active = current === col;
  return (
    <th onClick={() => onClick(col)} className="px-3 py-2 text-left font-medium text-zinc-500 whitespace-nowrap cursor-pointer hover:text-zinc-700 select-none">
      {label}{active ? (dir === "asc" ? " ↑" : " ↓") : ""}
    </th>
  );
}

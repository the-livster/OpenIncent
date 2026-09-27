import { useMemo, useState } from "react";
import type { PayeeRow } from "./Pipeline";
import { Th, Td } from "./Table";
import { SortTh } from "./SortableTable";
import StageShell from "./StageShell";
import { Badge, Callout } from "./ui";

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
    <StageShell
      n={2}
      group="Setup"
      title="Quotas"
      description="Review each payee's quota and ramp. Edits here apply to this run; the saved roster is unchanged."
      actions={payees.length > 0 && missingQuota.length === 0 && <Badge tone="success" icon="check">All quotas set</Badge>}
      onBack={onBack}
      onNext={onNext}
    >
      {missingQuota.length > 0 && (
        <Callout tone="warning">
          {missingQuota.length} payee(s) have no quota set. Attainment will be N/A until fixed.
        </Callout>
      )}

      {payees.length === 0 ? (
        <p className="text-[13.5px] text-ink-2">Upload a roster in step 1 to review quotas.</p>
      ) : (
        <div className="table-wrap max-h-[60vh] rounded-xl border border-line">
          <table>
            <thead>
              <tr>
                <SortTh col="id" label="ID" current={sortCol} dir={sortDir} onClick={toggleSort} />
                <SortTh col="name" label="Name" current={sortCol} dir={sortDir} onClick={toggleSort} />
                <SortTh col="quota" label="Base quota" current={sortCol} dir={sortDir} onClick={toggleSort} />
                <Th>Ramp months</Th>
                <Th>Ramp schedule</Th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(p => {
                const missing = !p.quota || p.quota === "0";
                return (
                  <tr key={p.id}>
                    <Td mono>{p.id}</Td>
                    <Td className="font-medium">{p.name}</Td>
                    <Td>
                      <input aria-label={`Quota for ${p.name}`} value={p.quota} onChange={e => update(p.id, "quota", e.target.value)}
                        className={`w-28 num ${missing ? "border-warning" : ""}`} />
                    </Td>
                    <Td>
                      <input aria-label={`Ramp months for ${p.name}`} value={p.ramp_months} onChange={e => update(p.id, "ramp_months", e.target.value)}
                        placeholder="Optional" className="w-24" />
                    </Td>
                    <Td>
                      <input aria-label={`Ramp schedule for ${p.name}`} value={p.ramp_schedule} onChange={e => update(p.id, "ramp_schedule", e.target.value)}
                        placeholder="e.g. 0.25 0.5 0.75" className="w-44" />
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </StageShell>
  );
}

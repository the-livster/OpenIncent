import { useRef, useState } from "react";
import type { PayeeRow } from "./Pipeline";
import { parsePayees } from "../api";
import { Td } from "./Table";
import { useTableSort } from "./useTableSort";
import { FilterBar, FilterTh, SortTh } from "./SortableTable";
import StageShell from "./StageShell";
import UploadZone from "./UploadZone";
import { Badge, Button, Callout, Pagination } from "./ui";

interface Props {
  payees: PayeeRow[];
  setPayees: (p: PayeeRow[]) => void;
  onNext: () => void;
}

const COLUMNS = [
  { col: "id", label: "ID" },
  { col: "name", label: "Name" },
  { col: "quota", label: "Quota" },
  { col: "plan_id", label: "Plan" },
  { col: "effective_from", label: "From" },
  { col: "effective_to", label: "To" },
] as const;

export default function StagePayees({ payees, setPayees, onNext }: Props) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const reupload = useRef<HTMLInputElement>(null);

  const { paginated, totalItems, page, totalPages, setPage, sortCol, sortDir, filters, toggleSort, setFilter, clearFilters } = useTableSort(payees, "id");

  async function handleFile(f: File) {
    if (loading) return;
    setLoading(true); setError("");
    try {
      const rows = await parsePayees(f);
      setPayees(rows); setPage(1); clearFilters();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not import the roster.");
    } finally { setLoading(false); }
  }

  const count = payees.length;

  return (
    <StageShell
      n={1}
      group="Setup"
      title="Payees"
      description="Upload your payee roster as CSV or Excel, with id, name, quota, plan_id and effective_from columns."
      actions={count > 0 && (
        <>
          <Badge tone="success" icon="check">{count} {count === 1 ? "payee" : "payees"} loaded</Badge>
          <Button size="sm" icon="upload" disabled={loading} loading={loading} onClick={() => reupload.current?.click()}>
            Replace file
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setPayees([])}>Clear</Button>
        </>
      )}
      onNext={count > 0 ? onNext : undefined}
      nextDisabled={loading}
    >
      {error && <Callout tone="danger" role="alert">{error}</Callout>}
      {count === 0 ? (
        <UploadZone
          label="Upload Payees"
          title="Drop your roster here"
          hint="CSV or XLSX with columns id, name, quota, plan_id, effective_from. Every row is checked before anything changes."
          accept=".csv,.xlsx"
          icon="users"
          busy={loading}
          busyLabel="Reading roster..."
          onFile={handleFile}
        />
      ) : (
        <>
          <FilterBar total={count} shown={totalItems} filters={filters} onClear={clearFilters} />
          <div className="table-wrap overflow-hidden rounded-xl border border-line">
            <table>
              <thead>
                <tr>
                  {COLUMNS.map(c => (
                    <SortTh key={c.col} col={c.col} label={c.label} current={sortCol} dir={sortDir} onClick={toggleSort}
                      align={c.col === "quota" ? "right" : undefined} />
                  ))}
                </tr>
                <tr>
                  {COLUMNS.map(c => (
                    <FilterTh key={c.col} label={c.label} value={filters[c.col] || ""} onChange={v => setFilter(c.col, v)} />
                  ))}
                </tr>
              </thead>
              <tbody>
                {paginated.map(p => (
                  <tr key={p.id}>
                    <Td mono>{p.id}</Td>
                    <Td className="font-medium">{p.name}</Td>
                    <Td num>{p.quota}</Td>
                    <Td mono>{p.plan_id}</Td>
                    <Td className="text-ink-2">{p.effective_from}</Td>
                    <Td className="text-ink-2">{p.effective_to}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination page={page} totalPages={totalPages} onPage={setPage} className="border-t border-line" />
          </div>
          <input
            ref={reupload}
            aria-label="Upload Payees"
            disabled={loading}
            type="file"
            accept=".csv,.xlsx"
            className="hidden"
            onChange={e => { const f = e.target.files?.[0]; e.target.value = ""; if (f) handleFile(f); }}
          />
        </>
      )}
    </StageShell>
  );
}

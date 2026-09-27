import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { deletePayee, importPayees, listPayees, savePayee, type PayeeSaveArgs } from "../api";
import type { Payee } from "../types";
import { money } from "../format";
import { SortTh } from "./SortableTable";
import { Badge, Button, Callout, Card, Drawer, EmptyState, Icon, PageHeader, Spinner } from "./ui";
import { cx } from "./ui/cx";

type SortCol = "id" | "name" | "quota" | "plan_id" | "effective_from" | "email";
type Filter = "all" | "no-plan" | "no-quota" | "inactive";

export default function Roster() {
  const [payees, setPayees] = useState<Payee[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");

  // Edit form state
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState<PayeeSaveArgs>(emptyForm());

  // Import state
  const [replaceRoster, setReplaceRoster] = useState(false);
  const importInput = useRef<HTMLInputElement>(null);

  // Sort
  const [sortCol, setSortCol] = useState<SortCol>("id");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const toggleSort = (col: SortCol) => {
    if (sortCol === col) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortCol(col); setSortDir("asc"); }
  };

  // Filter
  const [filter, setFilter] = useState<Filter>("all");

  const refresh = useCallback(async () => {
    try {
      const data = await listPayees();
      setPayees(data);
    } catch {
      setError("Failed to load payees");
    }
    setLoading(false);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const today = new Date().toISOString().slice(0, 10);
  const isInactive = useCallback((p: Payee) => !!(p.effective_to && p.effective_to < today), [today]);

  const sorted = useMemo(() => {
    let list = [...payees];
    // Filter
    if (filter === "no-plan") list = list.filter(p => !p.plan_id);
    else if (filter === "no-quota") list = list.filter(p => !p.quota || p.quota === "0");
    else if (filter === "inactive") list = list.filter(isInactive);
    const q = search.trim().toLowerCase();
    if (q) {
      list = list.filter(p => [p.id, p.name, p.email ?? "", p.plan_id].some(v => v.toLowerCase().includes(q)));
    }
    // Sort
    list.sort((a, b) => {
      const av = (a[sortCol] ?? "").toString().toLowerCase();
      const bv = (b[sortCol] ?? "").toString().toLowerCase();
      if (sortCol === "quota") {
        return sortDir === "asc" ? parseFloat(av) - parseFloat(bv) : parseFloat(bv) - parseFloat(av);
      }
      return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
    });
    return list;
  }, [payees, sortCol, sortDir, filter, search, isInactive]);

  // Quick counts
  const noPlan = payees.filter(p => !p.plan_id).length;
  const noQuota = payees.filter(p => !p.quota || p.quota === "0").length;
  const inactive = payees.filter(isInactive).length;

  const startEdit = (p?: Payee) => {
    setError("");
    if (p) {
      setEditing(p.id);
      setForm({
        payee_id: p.id, name: p.name, quota: p.quota || "0", plan_id: p.plan_id,
        effective_from: p.effective_from || "",
        effective_to: p.effective_to || undefined,
        email: p.email || undefined,
      });
    } else {
      const id = `P${String(Date.now()).slice(-6)}`;
      setEditing(id);
      setForm({ ...emptyForm(), payee_id: id });
    }
  };

  const handleSave = async () => {
    setError("");
    try {
      await savePayee(form);
      setEditing(null);
      setStatus("Saved.");
      await refresh();
      setTimeout(() => setStatus(""), 2000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    }
  };

  const handleDelete = async (id: string) => {
    if (!window.confirm(`Remove ${id} from the roster?`)) return;
    try {
      await deletePayee(id);
      await refresh();
    } catch {
      setError("Delete failed");
    }
  };

  const handleImport = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setError("");
    setStatus("Importing...");
    try {
      const result = await importPayees(files[0], replaceRoster);
      setStatus(`Imported ${result.imported} payees. Roster: ${result.total_in_roster} total.`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Import failed");
      setStatus("");
    }
  };

  const isNew = editing !== null && !payees.some(p => p.id === editing);

  return (
    <div className="animate-in">
      <PageHeader
        title="Payees"
        description="The saved roster the Pipeline starts from. Edit people one at a time, or import a whole file."
        actions={
          <>
            <label className="flex items-center gap-2 text-[12.5px] text-ink-2">
              <input type="checkbox" checked={replaceRoster} onChange={e => setReplaceRoster(e.target.checked)} />
              Replace entire roster
            </label>
            <Button icon="upload" onClick={() => importInput.current?.click()}>Import CSV/XLSX</Button>
            <input ref={importInput} aria-label="Import roster file" type="file" accept=".csv,.xlsx" className="hidden"
              onChange={e => { handleImport(e.target.files); e.target.value = ""; }} />
            <Button variant="primary" icon="userPlus" onClick={() => startEdit()}>Add payee</Button>
          </>
        }
      />

      {status && <Callout tone={status === "Importing..." ? "info" : "success"} className="mb-4">{status}</Callout>}
      {error && !editing && <Callout tone="danger" className="mb-4">{error}</Callout>}

      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-5 py-3">
          <div className="relative min-w-[200px] flex-1">
            <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-3" />
            <input type="search" aria-label="Search payees" placeholder="Search by name, id, email or plan"
              value={search} onChange={e => setSearch(e.target.value)} className="w-full pl-9" />
          </div>
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Show">
            <FilterChip active={filter === "all"} onClick={() => setFilter("all")} label="All" count={payees.length} />
            <FilterChip active={filter === "no-plan"} onClick={() => setFilter("no-plan")} label="No plan" count={noPlan} warn />
            <FilterChip active={filter === "no-quota"} onClick={() => setFilter("no-quota")} label="No quota" count={noQuota} warn />
            <FilterChip active={filter === "inactive"} onClick={() => setFilter("inactive")} label="Inactive" count={inactive} />
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-14 text-[13px] text-ink-2"><Spinner /> Loading roster...</div>
        ) : sorted.length === 0 ? (
          <EmptyState
            icon="users"
            title={filter !== "all" || search ? "No payees match" : "No payees saved"}
            description={filter !== "all" || search ? "Try another filter or search." : "Import a CSV or add people one at a time."}
            action={filter === "all" && !search && <Button variant="primary" icon="userPlus" onClick={() => startEdit()}>Add payee</Button>}
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <SortTh col="id" label="ID" current={sortCol} dir={sortDir} onClick={toggleSort} />
                  <SortTh col="name" label="Name" current={sortCol} dir={sortDir} onClick={toggleSort} />
                  <SortTh col="quota" label="Quota" current={sortCol} dir={sortDir} onClick={toggleSort} align="right" />
                  <SortTh col="plan_id" label="Plan" current={sortCol} dir={sortDir} onClick={toggleSort} />
                  <SortTh col="effective_from" label="Active" current={sortCol} dir={sortDir} onClick={toggleSort} />
                  <SortTh col="email" label="Email" current={sortCol} dir={sortDir} onClick={toggleSort} />
                  <th className="w-0" />
                </tr>
              </thead>
              <tbody>
                {sorted.map(p => {
                  const missingPlan = !p.plan_id;
                  const missingQuota = !p.quota || p.quota === "0";
                  const ended = isInactive(p);
                  return (
                    <tr key={p.id} className={cx(ended && "opacity-60")}>
                      <td className="font-mono text-[12.5px]">{p.id}</td>
                      <td className="font-medium">{p.name}</td>
                      <td className="num text-right">
                        {missingQuota ? <Badge tone="warning">No quota</Badge> : money(p.quota)}
                      </td>
                      <td>{missingPlan ? <Badge tone="warning" icon="alertTriangle">None</Badge> : <span className="font-mono text-[12.5px] text-ink-2">{p.plan_id}</span>}</td>
                      <td className="whitespace-nowrap text-ink-2">
                        {p.effective_from || "—"} <span className="text-ink-3">→</span> {p.effective_to || "ongoing"}
                        {ended && <Badge className="ml-2">Ended</Badge>}
                      </td>
                      <td className="text-ink-2">{p.email || "—"}</td>
                      <td>
                        <div className="flex justify-end gap-1">
                          <Button size="sm" variant="ghost" icon="pencil" onClick={() => startEdit(p)}>Edit</Button>
                          <Button size="sm" variant="ghost" icon="trash" aria-label={`Delete ${p.id}`} onClick={() => handleDelete(p.id)} />
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Drawer
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={isNew ? "New payee" : "Edit payee"}
        subtitle={<span className="font-mono">{form.payee_id}</span>}
        width={440}
      >
        <form
          className="space-y-4 px-5 py-5"
          onSubmit={e => { e.preventDefault(); void handleSave(); }}
        >
          {error && <Callout tone="danger">{error}</Callout>}
          <Field label="Name" value={form.name} onChange={v => setForm(f => ({ ...f, name: v }))} />
          <div className="grid grid-cols-2 gap-3">
            <Field label="Quota" value={form.quota} onChange={v => setForm(f => ({ ...f, quota: v }))} />
            <Field label="Plan ID" value={form.plan_id} onChange={v => setForm(f => ({ ...f, plan_id: v }))} />
            <Field label="Effective from" value={form.effective_from} onChange={v => setForm(f => ({ ...f, effective_from: v }))} placeholder="YYYY-MM-DD" />
            <Field label="Effective to" value={form.effective_to || ""} onChange={v => setForm(f => ({ ...f, effective_to: v || undefined }))} placeholder="Optional" />
          </div>
          <Field label="Email" value={form.email || ""} onChange={v => setForm(f => ({ ...f, email: v || undefined }))} placeholder="Optional — used to send statements" />
          <div className="flex justify-end gap-2 border-t border-line pt-4">
            <Button variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
            <Button variant="primary" type="submit" icon="check">Save payee</Button>
          </div>
        </form>
      </Drawer>
    </div>
  );
}

function FilterChip({ active, onClick, label, count, warn }: {
  active: boolean; onClick: () => void; label: string; count: number; warn?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={cx(
        "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[12.5px] font-medium transition-colors",
        active
          ? warn ? "border-warning/40 bg-warning-soft text-warning-ink" : "border-accent/30 bg-accent-soft text-accent-ink"
          : "border-line text-ink-2 hover:bg-surface-2 hover:text-ink",
      )}
    >
      {label}
      <span className={cx("num rounded-full px-1.5 text-[11px]", active ? "bg-surface/70" : "bg-surface-2")}>{count}</span>
    </button>
  );
}

function Field({ label, value, onChange, placeholder }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string;
}) {
  return (
    <label className="block">
      <span className="field-label">{label}</span>
      <input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} className="w-full" />
    </label>
  );
}

function emptyForm(): PayeeSaveArgs {
  return {
    payee_id: "", name: "", quota: "0", plan_id: "",
    effective_from: "", effective_to: undefined,
    email: undefined,
  };
}

import { useCallback, useEffect, useMemo, useState } from "react";
import { deletePayee, importPayees, listPayees, savePayee, type PayeeSaveArgs } from "../api";
import type { Payee } from "../types";

type SortCol = "id" | "name" | "quota" | "plan_id" | "effective_from" | "email";

export default function Roster() {
  const [payees, setPayees] = useState<Payee[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  // Edit form state
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState<PayeeSaveArgs>(emptyForm());

  // Import state
  const [replaceRoster, setReplaceRoster] = useState(false);

  // Sort
  const [sortCol, setSortCol] = useState<SortCol>("id");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const toggleSort = (col: SortCol) => {
    if (sortCol === col) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortCol(col); setSortDir("asc"); }
  };

  // Filter
  const [filter, setFilter] = useState<"all" | "no-plan" | "no-quota" | "inactive">("all");

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

  const sorted = useMemo(() => {
    let list = [...payees];
    // Filter
    if (filter === "no-plan") list = list.filter(p => !p.plan_id);
    else if (filter === "no-quota") list = list.filter(p => !p.quota || p.quota === "0");
    else if (filter === "inactive") list = list.filter(p => {
      const now = new Date().toISOString().slice(0, 10);
      return (p.effective_to && p.effective_to < now);
    });
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
  }, [payees, sortCol, sortDir, filter]);

  // Quick counts
  const noPlan = payees.filter(p => !p.plan_id).length;
  const noQuota = payees.filter(p => !p.quota || p.quota === "0").length;
  const inactive = payees.filter(p => {
    const now = new Date().toISOString().slice(0, 10);
    return !!(p.effective_to && p.effective_to < now);
  }).length;

  const startEdit = (p?: Payee) => {
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

  if (loading) return <div className="p-6 text-zinc-500">Loading roster...</div>;

  return (
    <div className="max-w-4xl mx-auto space-y-6 animate-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-ink">Roster</h2>
          <p className="text-sm text-ink2 mt-1">
            {payees.length} saved payees. Edit, add, or bulk import.
          </p>
        </div>
        <button
          onClick={() => startEdit()}
          className="px-4 py-2 rounded-lg text-sm font-medium bg-accent text-white hover:bg-accent transition-colors cursor-pointer"
        >
          + Add Payee
        </button>
      </div>

      {/* Import */}
      <div className="card p-4 space-y-3">
        <div className="flex items-center gap-3">
          <label className="px-3 py-1.5 rounded-lg text-xs font-medium bg-soft border border-line text-ink hover:border-ink2 cursor-pointer transition-colors">
            Import CSV/XLSX
            <input type="file" accept=".csv,.xlsx" className="hidden" onChange={e => handleImport(e.target.files)} />
          </label>
          <label className="flex items-center gap-1.5 text-xs text-ink2 cursor-pointer select-none">
            <input type="checkbox" checked={replaceRoster} onChange={e => setReplaceRoster(e.target.checked)} className="accent-accent" />
            Replace entire roster
          </label>
        </div>
        {status && <div className="text-xs text-ink2">{status}</div>}
      </div>

      {/* Error */}
      {error && (
        <div className="px-4 py-3 rounded-xl bg-danger/10 border border-danger/20 text-danger text-sm select-text">
          {error}
        </div>
      )}

      {/* Edit form */}
      {editing && (
        <div className="card p-4 space-y-3 border-accent/30">
          <h3 className="text-sm font-semibold text-ink">
            {payees.some(p => p.id === editing) ? "Edit" : "New"} Payee: {form.payee_id}
          </h3>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <Field label="Name" value={form.name} onChange={v => setForm(f => ({ ...f, name: v }))} />
            <Field label="Quota" value={form.quota} onChange={v => setForm(f => ({ ...f, quota: v }))} />
            <Field label="Plan ID" value={form.plan_id} onChange={v => setForm(f => ({ ...f, plan_id: v }))} />
            <Field label="Effective From" value={form.effective_from} onChange={v => setForm(f => ({ ...f, effective_from: v }))} placeholder="YYYY-MM-DD" />
            <Field label="Effective To" value={form.effective_to || ""} onChange={v => setForm(f => ({ ...f, effective_to: v || undefined }))} placeholder="optional" />
            <Field label="Email" value={form.email || ""} onChange={v => setForm(f => ({ ...f, email: v || undefined }))} placeholder="optional" />
          </div>
          <div className="flex gap-2 pt-1">
            <button onClick={handleSave} className="px-3 py-1.5 rounded-lg text-xs font-medium bg-accent text-white hover:bg-accent transition-colors cursor-pointer">
              Save
            </button>
            <button onClick={() => setEditing(null)} className="px-3 py-1.5 rounded-lg text-xs font-medium bg-soft border border-line text-ink2 hover:text-ink transition-colors cursor-pointer">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Quick-filter badges */}
      <div className="flex items-center gap-2 flex-wrap">
        <FilterBadge active={filter === "all"} onClick={() => setFilter("all")} label={`All (${payees.length})`} />
        <FilterBadge active={filter === "no-plan"} onClick={() => setFilter("no-plan")} label={`No plan (${noPlan})`} warn />
        <FilterBadge active={filter === "no-quota"} onClick={() => setFilter("no-quota")} label={`No quota (${noQuota})`} warn />
        <FilterBadge active={filter === "inactive"} onClick={() => setFilter("inactive")} label={`Inactive (${inactive})`} />
      </div>

      {/* Payee list */}
      {sorted.length === 0 ? (
        <div className="text-sm text-ink2 py-8 text-center">
          {filter !== "all" ? "No payees match this filter." : "No payees saved. Import a CSV or add one manually."}
        </div>
      ) : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-soft">
                  <SortTh col="id" label="ID" current={sortCol} dir={sortDir} onClick={toggleSort} />
                  <SortTh col="name" label="Name" current={sortCol} dir={sortDir} onClick={toggleSort} />
                  <SortTh col="quota" label="Quota" current={sortCol} dir={sortDir} onClick={toggleSort} right />
                  <SortTh col="plan_id" label="Plan" current={sortCol} dir={sortDir} onClick={toggleSort} />
                  <SortTh col="effective_from" label="Active" current={sortCol} dir={sortDir} onClick={toggleSort} />
                  <SortTh col="email" label="Email" current={sortCol} dir={sortDir} onClick={toggleSort} />
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {sorted.map(p => {
                  const noPlan = !p.plan_id;
                  const noQuota = !p.quota || p.quota === "0";
                  const now = new Date().toISOString().slice(0, 10);
                  const isInactive = !!(p.effective_to && p.effective_to < now);
                  return (
                    <tr key={p.id} className={`hover:bg-soft ${isInactive ? "opacity-50" : ""}`}>
                      <td className="px-3 py-1.5 text-ink font-mono">{p.id}</td>
                      <td className="px-3 py-1.5 text-ink">{p.name}</td>
                      <td className={`px-3 py-1.5 font-mono text-right ${noQuota ? "text-warn font-semibold" : "text-ink"}`}>
                        {noQuota ? "—" : `$${parseFloat(p.quota!).toLocaleString()}`}
                      </td>
                      <td className={`px-3 py-1.5 ${noPlan ? "text-warn font-semibold" : "text-ink2"}`}>
                        {p.plan_id || "⚠ None"}
                      </td>
                      <td className="px-3 py-1.5 text-xs">
                        {p.effective_from || "—"} → {p.effective_to || "ongoing"}
                        {isInactive && <span className="ml-1 text-warn">(ended)</span>}
                      </td>
                      <td className="px-3 py-1.5 text-ink2 text-xs">{p.email || "—"}</td>
                      <td className="px-3 py-1.5 flex gap-1 justify-end">
                        <button onClick={() => startEdit(p)} className="text-xs text-accent hover:underline cursor-pointer">Edit</button>
                        <button onClick={() => handleDelete(p.id)} className="text-xs text-danger hover:underline cursor-pointer">Delete</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function SortTh({ col, label, current, dir, onClick, right }: {
  col: SortCol; label: string; current: SortCol; dir: string; onClick: (c: SortCol) => void; right?: boolean;
}) {
  const active = current === col;
  return (
    <th
      onClick={() => onClick(col)}
      className={`px-3 py-2 font-medium text-ink2 whitespace-nowrap cursor-pointer hover:text-ink select-none ${right ? "text-right" : "text-left"}`}
    >
      {label}{active ? (dir === "asc" ? " ↑" : " ↓") : ""}
    </th>
  );
}

function FilterBadge({ active, onClick, label, warn }: {
  active: boolean; onClick: () => void; label: string; warn?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-2.5 py-1 rounded-full text-[11px] font-medium transition-colors cursor-pointer
        ${active
          ? warn ? "bg-warn/15 text-warn" : "bg-accent/15 text-accent"
          : warn ? "text-warn/60 hover:text-warn hover:bg-warn/5" : "text-ink2 hover:text-ink hover:bg-soft"
        }`}
    >
      {label}
    </button>
  );
}

function Field({ label, value, onChange, placeholder }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string;
}) {
  return (
    <div>
      <label className="block text-[10px] font-medium text-ink2 mb-0.5">{label}</label>
      <input
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full px-2 py-1 rounded text-xs bg-soft border border-line text-ink placeholder:text-ink2 focus:outline-none focus:border-accent"
      />
    </div>
  );
}

function emptyForm(): PayeeSaveArgs {
  return {
    payee_id: "", name: "", quota: "0", plan_id: "",
    effective_from: "", effective_to: undefined,
    email: undefined,
  };
}

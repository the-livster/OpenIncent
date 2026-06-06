import { useCallback, useEffect, useState } from "react";
import { deletePayee, listPayees, savePayee } from "../api";

interface PayeeRecord {
  id: string;
  name: string;
  quota: string;
  plan_id: string;
  effective_from: string;
  effective_to: string | null;
  email: string | null;
  ramp: string | null;
  category_quotas: string | null;
}

export default function PayeeManager() {
  const [payees, setPayees] = useState<PayeeRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<PayeeRecord | null>(null);
  const [showAdd, setShowAdd] = useState(false);

  const [form, setForm] = useState({
    id: "", name: "", quota: "0", plan_id: "", effective_from: "",
    email: "", ramp_months: "", ramp_schedule: "",
    category_quotas: "{}",
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listPayees();
      setPayees(data as unknown as PayeeRecord[]);
    } catch {
      // endpoint may not exist (non-desktop mode)
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const resetForm = () => {
    setForm({ id: "", name: "", quota: "0", plan_id: "", effective_from: "",
              email: "", ramp_months: "", ramp_schedule: "", category_quotas: "{}" });
    setEditing(null);
    setShowAdd(false);
    setError("");
  };

  const openEdit = (p: PayeeRecord) => {
    let ramp_months = "";
    let ramp_schedule = "";
    try {
      if (p.ramp) {
        const r = JSON.parse(p.ramp);
        ramp_months = String(r.months || "");
        ramp_schedule = (r.schedule || []).join(" ");
      }
    } catch { /* ignore */ }
    setForm({
      id: p.id, name: p.name, quota: p.quota, plan_id: p.plan_id,
      effective_from: p.effective_from,
      email: p.email || "",
      ramp_months, ramp_schedule,
      category_quotas: p.category_quotas || "{}",
    });
    setEditing(p);
    setShowAdd(false);
  };

  const handleSave = useCallback(async () => {
    if (!form.id || !form.name) {
      setError("ID and Name are required.");
      return;
    }
    setError("");
    try {
      await savePayee({
        payee_id: form.id,
        name: form.name,
        quota: form.quota,
        plan_id: form.plan_id,
        effective_from: form.effective_from,
        email: form.email || undefined,
        ramp_months: form.ramp_months ? parseInt(form.ramp_months) : undefined,
        ramp_schedule: form.ramp_schedule || undefined,
        category_quotas: form.category_quotas !== "{}" ? form.category_quotas : undefined,
      });
      resetForm();
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Save failed");
    }
  }, [form, load]);

  const handleDelete = useCallback(async (payeeId: string) => {
    if (!confirm(`Delete ${payeeId}?`)) return;
    try {
      await deletePayee(payeeId);
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  }, [load]);

  if (loading) return <div className="text-center py-8 text-ink2 text-sm">Loading payees...</div>;

  return (
    <div className="max-w-3xl mx-auto space-y-4 animate-in">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-ink">Payees</h2>
        <button
          onClick={() => { resetForm(); setShowAdd(true); setForm(f => ({ ...f, id: "" })); }}
          className="px-3 py-1.5 rounded-lg text-xs font-medium bg-accent/10 text-accent hover:bg-accent/20 cursor-pointer transition-colors"
        >
          + Add Payee
        </button>
      </div>

      {error && (
        <div className="px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-red-700 text-xs select-text">{error}</div>
      )}

      {(showAdd || editing) && (
        <PayeeForm form={form} setForm={setForm} onSave={handleSave} onCancel={resetForm} isEdit={!!editing} />
      )}

      {/* Table */}
      <div className="card overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-line bg-soft">
              <th className="px-3 py-2 text-left text-ink2 font-medium">ID</th>
              <th className="px-3 py-2 text-left text-ink2 font-medium">Name</th>
              <th className="px-3 py-2 text-right text-ink2 font-medium">Quota</th>
              <th className="px-3 py-2 text-left text-ink2 font-medium">Plan</th>
              <th className="px-3 py-2 text-left text-ink2 font-medium">Effective</th>
              <th className="px-3 py-2 w-16"></th>
            </tr>
          </thead>
          <tbody>
            {payees.length === 0 && (
              <tr><td colSpan={6} className="px-3 py-6 text-center text-ink2">No payees found</td></tr>
            )}
            {payees.map(p => (
              <tr key={p.id} className="border-b border-line hover:bg-soft cursor-pointer transition-colors"
                  onClick={() => openEdit(p)}>
                <td className="px-3 py-2 font-mono text-ink">{p.id}</td>
                <td className="px-3 py-2 text-ink">{p.name}</td>
                <td className="px-3 py-2 text-right font-mono text-ink">${parseFloat(p.quota || "0").toLocaleString()}</td>
                <td className="px-3 py-2 text-ink2">{p.plan_id}</td>
                <td className="px-3 py-2 text-ink2">{p.effective_from}</td>
                <td className="px-2 py-2">
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDelete(p.id); }}
                    className="text-xs text-ink2 hover:text-danger cursor-pointer px-1"
                  >🗑</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PayeeForm({ form, setForm, onSave, onCancel, isEdit }: {
  form: { id: string; name: string; quota: string; plan_id: string; effective_from: string;
          email: string; ramp_months: string; ramp_schedule: string; category_quotas: string; };
  setForm: (f: typeof form) => void;
  onSave: () => void;
  onCancel: () => void;
  isEdit: boolean;
}) {
  const update = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [k]: e.target.value });

  return (
    <div className="card p-4 space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label="ID" value={form.id} onChange={update("id")} disabled={isEdit} />
        <Field label="Name" value={form.name} onChange={update("name")} />
        <Field label="Quota" value={form.quota} onChange={update("quota")} />
        <Field label="Plan ID" value={form.plan_id} onChange={update("plan_id")} />
        <Field label="Effective From" value={form.effective_from} onChange={update("effective_from")} placeholder="YYYY-MM-DD" />
        <Field label="Email" value={form.email} onChange={update("email")} placeholder="optional" />
        <Field label="Ramp Months" value={form.ramp_months} onChange={update("ramp_months")} placeholder="optional" />
        <Field label="Ramp Schedule" value={form.ramp_schedule} onChange={update("ramp_schedule")} placeholder='e.g. "0.25 0.50 0.75"' />
      </div>
      <div>
        <label className="block text-xs font-medium text-ink2 mb-1">Category Quotas (JSON)</label>
        <input
          value={form.category_quotas}
          onChange={update("category_quotas")}
          placeholder='{"new_business": "60000", "expansion": "40000"}'
          className="w-full px-3 py-2 rounded-lg text-xs font-mono bg-soft border border-line text-ink focus:outline-none focus:border-accent"
        />
      </div>
      <div className="flex gap-2 justify-end">
        <button onClick={onCancel} className="px-3 py-1.5 rounded-lg text-xs bg-soft border border-line text-ink2 hover:text-ink cursor-pointer">Cancel</button>
        <button onClick={onSave} className="px-3 py-1.5 rounded-lg text-xs font-medium bg-accent text-white hover:bg-ink cursor-pointer">
          {isEdit ? "Update" : "Add"}
        </button>
      </div>
    </div>
  );
}

function Field({ label, value, onChange, disabled, placeholder }: {
  label: string; value: string; onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  disabled?: boolean; placeholder?: string;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-ink2 mb-1">{label}</label>
      <input
        value={value}
        onChange={onChange}
        disabled={disabled}
        placeholder={placeholder}
        className="w-full px-3 py-2 rounded-lg text-xs bg-soft border border-line text-ink focus:outline-none focus:border-accent disabled:opacity-50"
      />
    </div>
  );
}

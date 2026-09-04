import type {
  CalculateResponse,
  Payee,
  PlanFromTextRequest,
  PlanFromTextResponse,
  SavedPlan,
} from "./types";

function getBase(): string {
  const stored = localStorage.getItem("icm_api_base");
  if (stored) return stored;
  // Use relative URLs when no explicit base is set (desktop app / same-origin dev)
  return "";
}

function v1(path: string): string {
  return `${getBase()}/v1${path}`;
}

export async function calculate(
  args: { plan?: File; transactions: File; payees?: File; allowUnknownPayees?: boolean },
  signal?: AbortSignal,
): Promise<CalculateResponse> {
  const form = new FormData();
  if (args.plan) {
    form.append("plan", args.plan);
  }
  if (args.payees) {
    form.append("payees", args.payees);
  }
  form.append("transactions", args.transactions);

  const query = args.allowUnknownPayees ? "?allow_unknown_payees=true" : "";
  const res = await fetch(v1("/calculate") + query, { method: "POST", body: form, signal });

  if (!res.ok) {
    const text = await res.text();
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new Error(`Server error (${res.status}): ${text.slice(0, 500)}`);
    }
    const detail = parsed.detail as Record<string, unknown> | undefined;

    // Pre-flight rejections carry the specific problems; showing only the
    // headline would tell the user something is wrong but not which row.
    const issues = detail?.issues as
      | Array<{ severity: string; code: string; message: string }>
      | undefined;
    if (issues?.length) {
      const lines = issues.map(
        (i) => `${i.severity === "error" ? "Error" : "Warning"}: ${i.message}`,
      );
      // The API hint names a query parameter, which is useless to someone
      // clicking buttons; the UI offers a checkbox instead.
      const err = new Error(
        `${String(detail?.error ?? "Cannot calculate")}\n\n${lines.join("\n\n")}`,
      ) as Error & { codes?: string[] };
      err.codes = issues.map((i) => i.code);
      throw err;
    }

    // Plan-library rejections carry a `missing` list naming which plans and
    // which payees; the headline alone leaves the user guessing.
    const missing = detail?.missing as string[] | undefined;
    if (missing?.length) {
      const hint = detail?.hint ? `\n\n${String(detail.hint)}` : "";
      throw new Error(
        `${String(detail?.error ?? "Cannot calculate")}\n\n${missing.join("\n")}${hint}`,
      );
    }

    const msg = detail?.traceback
      ? `${detail.detail}\n\n${detail.traceback}`
      : (detail?.detail as string) ?? (detail?.error as string) ?? JSON.stringify(detail) ?? "Calculation failed";
    throw new Error(String(msg));
  }
  return res.json();
}

export async function generatePlan(
  req: PlanFromTextRequest, signal?: AbortSignal,
): Promise<PlanFromTextResponse> {
  const res = await fetch(v1("/plan-from-text"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  });
  if (!res.ok) {
    const text = await res.text();
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new Error(`Server error (${res.status}): ${text.slice(0, 500)}`);
    }
    const detail = parsed.detail as Record<string, unknown> | undefined;
    throw new Error(String(detail?.detail ?? detail?.error ?? "Plan generation failed"));
  }
  return res.json();
}

export async function healthCheck(signal?: AbortSignal): Promise<boolean> {
  try {
    const res = await fetch(`${getBase()}/health`, { signal });
    return res.ok;
  } catch {
    return false;
  }
}

// ------------------------------------------------------------------
// Settings
// ------------------------------------------------------------------

export async function getSetting(key: string, signal?: AbortSignal): Promise<string | null> {
  const res = await fetch(v1(`/settings/${encodeURIComponent(key)}`), { signal });
  if (!res.ok) return null;
  const data = await res.json();
  return data.value ?? null;
}

export async function setSetting(key: string, value: string, signal?: AbortSignal): Promise<void> {
  const res = await fetch(v1(`/settings/${encodeURIComponent(key)}`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
    signal,
  });
  if (!res.ok) throw new Error(`Failed to save setting "${key}" (${res.status})`);
}

export async function deleteSetting(key: string, signal?: AbortSignal): Promise<void> {
  const res = await fetch(v1(`/settings/${encodeURIComponent(key)}`), { method: "DELETE", signal });
  if (!res.ok) throw new Error(`Failed to delete setting "${key}" (${res.status})`);
}

// ------------------------------------------------------------------
// Plans
// ------------------------------------------------------------------

export async function listPlans(signal?: AbortSignal): Promise<SavedPlan[]> {
  const res = await fetch(v1("/plans"), { signal });
  if (!res.ok) return [];
  return res.json();
}

export async function savePlan(
  name: string, yaml_content: string, plan_id?: string, description?: string,
  signal?: AbortSignal,
): Promise<string> {
  const res = await fetch(v1("/plans"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, yaml_content, plan_id, description }),
    signal,
  });
  if (!res.ok) throw new Error("Failed to save plan");
  const data = await res.json();
  return data.id;
}

export async function deletePlan(plan_id: string, signal?: AbortSignal): Promise<void> {
  const res = await fetch(v1(`/plans/${encodeURIComponent(plan_id)}`), { method: "DELETE", signal });
  if (!res.ok) throw new Error("Failed to delete plan");
}

// ------------------------------------------------------------------
// Export
// ------------------------------------------------------------------

export async function exportStatements(args: {
  plan?: File;
  transactions: File;
  payees?: File;
  formats?: string;
  period?: string;
  emit_zero?: boolean;
  plan_text?: string;
  txn_text?: string;
  payee_text?: string;
}, signal?: AbortSignal): Promise<string | undefined> {
  const form = new FormData();
  if (args.plan_text) {
    form.append("plan_text", args.plan_text);
  } else if (args.plan) {
    form.append("plan", args.plan);
  }
  if (args.txn_text) {
    form.append("txn_text", args.txn_text);
  } else {
    form.append("transactions", args.transactions);
  }
  if (args.payee_text) {
    form.append("payee_text", args.payee_text);
  } else if (args.payees) {
    form.append("payees", args.payees);
  }
  if (args.formats) form.append("formats", args.formats);
  if (args.period) form.append("period", args.period);
  if (args.emit_zero) form.append("emit_zero", "1");

  const res = await fetch(v1("/export"), { method: "POST", body: form, signal });
  if (!res.ok) {
    const text = await res.text();
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new Error(`Server error (${res.status}): ${text.slice(0, 500)}`);
    }
    const detail = parsed.detail as Record<string, unknown> | undefined;
    throw new Error(String(detail?.detail ?? detail?.error ?? "Export failed"));
  }

  // Desktop mode: server returns JSON with saved path
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const data = await res.json();
    return data.saved_to as string;
  }

  // Web mode: download as blob
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "commission_statements.zip";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  return undefined;
}

// ------------------------------------------------------------------
// Order trace
// ------------------------------------------------------------------

import type { OrderTrace } from "./types";

export async function fetchTrace(transaction_id: string, payee_id: string, signal?: AbortSignal): Promise<OrderTrace> {
  const params = new URLSearchParams({ transaction_id, payee_id });
  const res = await fetch(v1(`/trace?${params}`), { signal });
  if (!res.ok) {
    const text = await res.text();
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new Error(`Server error (${res.status}): ${text.slice(0, 500)}`);
    }
    throw new Error(String(parsed.detail ?? "Trace not found"));
  }
  return res.json();
}

// ------------------------------------------------------------------
// Payee CRUD
// ------------------------------------------------------------------

export interface PayeeSaveArgs {
  payee_id: string;
  name: string;
  quota: string;
  plan_id: string;
  effective_from: string;
  effective_to?: string;
  email?: string;
  ramp_months?: number;
  ramp_schedule?: string;
  category_quotas?: string;
  manager_id?: string;
  manager_override?: string;
  team_id?: string;
}

export async function listPayees(signal?: AbortSignal): Promise<Payee[]> {
  const res = await fetch(v1("/payees"), { signal });
  if (!res.ok) return [];
  return res.json();
}

export async function savePayee(args: PayeeSaveArgs, signal?: AbortSignal): Promise<void> {
  const res = await fetch(v1(`/payees/${encodeURIComponent(args.payee_id)}`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
    signal,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text.slice(0, 200));
  }
}

export async function deletePayee(payee_id: string, signal?: AbortSignal): Promise<void> {
  const res = await fetch(v1(`/payees/${encodeURIComponent(payee_id)}`), { method: "DELETE", signal });
  if (!res.ok) throw new Error("Delete failed");
}

export interface PayeeImportResult {
  imported: number;
  total_in_roster: number;
  replace: boolean;
}

export async function importPayees(file: File, replace: boolean = false, signal?: AbortSignal): Promise<PayeeImportResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("replace", String(replace));
  const res = await fetch(v1("/payees/import"), { method: "POST", body: form, signal });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text.slice(0, 500));
  }
  return res.json();
}

// ------------------------------------------------------------------
// File preview
// ------------------------------------------------------------------

interface FilePreview {
  headers: string[];
  preview_rows: string[][];
  mapping: Record<string, string>;
  is_xlsx: boolean;
}

export async function previewFile(file: File, type: string = "transactions", signal?: AbortSignal): Promise<FilePreview> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(v1(`/preview?type=${encodeURIComponent(type)}`), { method: "POST", body: form, signal });
  if (!res.ok) throw new Error("Preview failed");
  return res.json();
}

// ------------------------------------------------------------------
// Data model queries
// ------------------------------------------------------------------

export interface CalculationRow {
  id: string;
  plan_id: string;
  period: string;
  version: number;
  status: string;
  created_at: string;
  input_summary: string;
}

export interface TransactionRow {
  id: string;
  payee_id: string;
  deal_id: string;
  period: string;
  amount: string;
  product: string | null;
  close_date: string | null;
  metadata: string;
  created_at: string;
}

export interface PeriodStatusRow {
  period: string;
  versions: number;
  latest_version: number;
  status: string;
  locked_calc_id: string | null;
}

export async function listCalculations(plan_id?: string, period?: string, signal?: AbortSignal): Promise<CalculationRow[]> {
  const params = new URLSearchParams();
  if (plan_id) params.set("plan_id", plan_id);
  if (period) params.set("period", period);
  const qs = params.toString();
  const res = await fetch(v1(`/calculations${qs ? "?" + qs : ""}`), { signal });
  if (!res.ok) return [];
  return res.json();
}

export async function listTransactions(period?: string, signal?: AbortSignal): Promise<TransactionRow[]> {
  const params = new URLSearchParams();
  if (period) params.set("period", period);
  params.set("limit", "500");
  const res = await fetch(v1(`/transactions?${params}`), { signal });
  if (!res.ok) return [];
  return res.json();
}

export async function listPeriods(plan_id: string, signal?: AbortSignal): Promise<PeriodStatusRow[]> {
  const res = await fetch(v1(`/periods/${encodeURIComponent(plan_id)}`), { signal });
  if (!res.ok) return [];
  return res.json();
}

export async function listMappings(signal?: AbortSignal): Promise<Record<string, unknown>[]> {
  const res = await fetch(v1("/mappings"), { signal });
  if (!res.ok) return [];
  return res.json();
}

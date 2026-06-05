import type {
  CalculateResponse,
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

export async function calculate(args: {
  plan: File;
  transactions: File;
  payees: File;
}): Promise<CalculateResponse> {
  const form = new FormData();
  form.append("plan", args.plan);
  form.append("transactions", args.transactions);
  form.append("payees", args.payees);

  const res = await fetch(v1("/calculate"), { method: "POST", body: form });

  if (!res.ok) {
    const text = await res.text();
    try {
      const err = JSON.parse(text);
      const detail = err.detail;
      const msg = detail?.traceback
        ? `${detail.detail}\n\n${detail.traceback}`
        : detail?.detail ?? detail?.error ?? JSON.stringify(detail) ?? "Calculation failed";
      throw new Error(String(msg));
    } catch (e) {
      if (e instanceof Error && e.message.startsWith("Server error")) throw e;
      throw new Error(`Server error (${res.status}): ${text.slice(0, 500)}`);
    }
  }
  return res.json();
}

export async function generatePlan(req: PlanFromTextRequest): Promise<PlanFromTextResponse> {
  const res = await fetch(v1("/plan-from-text"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const text = await res.text();
    try {
      const err = JSON.parse(text);
      throw new Error(String(err.detail?.detail ?? err.detail?.error ?? "Plan generation failed"));
    } catch (e) {
      if (e instanceof Error && e.message.startsWith("Server error")) throw e;
      throw new Error(`Server error (${res.status}): ${text.slice(0, 500)}`);
    }
  }
  return res.json();
}

export async function healthCheck(): Promise<boolean> {
  try {
    const res = await fetch(`${getBase()}/health`);
    return res.ok;
  } catch {
    return false;
  }
}

// ------------------------------------------------------------------
// Settings
// ------------------------------------------------------------------

export async function getSetting(key: string): Promise<string | null> {
  const res = await fetch(v1(`/settings/${encodeURIComponent(key)}`));
  if (!res.ok) return null;
  const data = await res.json();
  return data.value ?? null;
}

export async function setSetting(key: string, value: string): Promise<void> {
  await fetch(v1(`/settings/${encodeURIComponent(key)}`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
}

export async function deleteSetting(key: string): Promise<void> {
  await fetch(v1(`/settings/${encodeURIComponent(key)}`), { method: "DELETE" });
}

// ------------------------------------------------------------------
// Plans
// ------------------------------------------------------------------

export async function listPlans(): Promise<SavedPlan[]> {
  const res = await fetch(v1("/plans"));
  if (!res.ok) return [];
  return res.json();
}

export async function savePlan(
  name: string, yaml_content: string, plan_id?: string, description?: string,
): Promise<string> {
  const res = await fetch(v1("/plans"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, yaml_content, plan_id, description }),
  });
  if (!res.ok) throw new Error("Failed to save plan");
  const data = await res.json();
  return data.id;
}

export async function deletePlan(plan_id: string): Promise<void> {
  const res = await fetch(v1(`/plans/${encodeURIComponent(plan_id)}`), { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete plan");
}

// ------------------------------------------------------------------
// Export
// ------------------------------------------------------------------

export async function exportStatements(args: {
  plan: File;
  transactions: File;
  payees: File;
  formats?: string;
  period?: string;
  plan_text?: string;
  txn_text?: string;
  payee_text?: string;
}): Promise<string | undefined> {
  const form = new FormData();
  if (args.plan_text) {
    form.append("plan_text", args.plan_text);
  } else {
    form.append("plan", args.plan);
  }
  if (args.txn_text) {
    form.append("txn_text", args.txn_text);
  } else {
    form.append("transactions", args.transactions);
  }
  if (args.payee_text) {
    form.append("payee_text", args.payee_text);
  } else {
    form.append("payees", args.payees);
  }
  if (args.formats) form.append("formats", args.formats);
  if (args.period) form.append("period", args.period);

  const res = await fetch(v1("/export"), { method: "POST", body: form });
  if (!res.ok) {
    const text = await res.text();
    try {
      const err = JSON.parse(text);
      throw new Error(String(err.detail?.detail ?? err.detail?.error ?? "Export failed"));
    } catch (e) {
      if (e instanceof Error && e.message.startsWith("Server error")) throw e;
      throw new Error(`Server error (${res.status}): ${text.slice(0, 500)}`);
    }
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
// File preview
// ------------------------------------------------------------------

interface FilePreview {
  headers: string[];
  preview_rows: string[][];
  mapping: Record<string, string>;
  is_xlsx: boolean;
}

export async function previewFile(file: File, type: string = "transactions"): Promise<FilePreview> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(v1(`/preview?type=${encodeURIComponent(type)}`), { method: "POST", body: form });
  if (!res.ok) throw new Error("Preview failed");
  return res.json();
}

export interface Commission {
  transaction_id: string;
  payee_id: string;
  period: string;
  origin_period: string;
  rule_id: string;
  base_amount: string;
  rate: string;
  commission_amount: string;
  notes: string;
}

export interface LedgerEntry {
  timestamp: string;
  transaction_id: string;
  payee_id: string;
  rule_id: string;
  event_type: string;
  inputs: Record<string, string>;
  outputs: Record<string, string>;
  human_readable: string;
}

export interface CalculateResponse {
  commissions: Commission[];
  ledger: LedgerEntry[];
  summary: Record<string, string>;
  calculation_ids?: Record<string, string>;
}

export interface PlanFromTextRequest {
  description: string;
  plan_id?: string;
  api_key: string;
}

export interface PlanFromTextResponse {
  yaml: string;
  plan: Record<string, unknown>;
}

export interface AppSettings {
  anthropicApiKey: string;
  apiBaseUrl: string;
}

export type SortDir = "asc" | "desc";
export interface SortState {
  column: string;
  dir: SortDir;
}

export interface SavedPlan {
  id: string;
  name: string;
  description: string;
  yaml_content: string;
  created_at: string;
  updated_at: string;
}

// ------------------------------------------------------------------
// Order trace
// ------------------------------------------------------------------

export interface TraceStep {
  rule_id: string;
  status: "matched" | "skipped";
  reason: string | null;
  events: {
    event_type: string;
    human_readable: string;
    inputs: Record<string, string>;
    outputs: Record<string, string>;
  }[];
}

export interface OrderTrace {
  transaction_id: string;
  payee_id: string;
  order: Record<string, string>;
  steps: TraceStep[];
  total: string;
  summary: string;
}

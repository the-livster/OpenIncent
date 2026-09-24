import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "../App";
import {
  exportSavedStatements,
  getCalculationInputs,
  getCalculationResult,
  listCalculations,
} from "../api";
import type { CalculationResult } from "../types";

vi.mock("../api", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api")>(),
  listPlans: vi.fn().mockResolvedValue([]),
  listCalculations: vi.fn().mockResolvedValue([]),
  getCalculationResult: vi.fn(),
  getCalculationInputs: vi.fn().mockResolvedValue([]),
  exportSavedStatements: vi.fn().mockResolvedValue(undefined),
}));

afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

const RUN = {
  id: "calc-abc-123", plan_id: "saas_ae", period: "2026-03", version: 2,
  status: "completed", created_at: "2026-04-01T09:30:00", input_summary: "{}",
};

const RESULT: CalculationResult = {
  calculation_id: RUN.id,
  plan_id: RUN.plan_id,
  period: RUN.period,
  version: RUN.version,
  status: RUN.status,
  created_at: RUN.created_at,
  locked: true,
  ledger_truncated: false,
  commissions: [{
    transaction_id: "T1", payee_id: "P1", period: "2026-03", origin_period: "2026-03", rule_id: "base",
    base_amount: "10000.00", rate: "0.10", commission_amount: "1000.00", notes: "",
  }],
  ledger: [],
  summary: { P1: "1000.00" },
  payouts: [{ payee_id: "P1", name: "Priya", period: "2026-03", currency: "GBP", total: "1000.00" }],
  payout_totals: { GBP: "1000.00" },
  calculation_ids: { "2026-03": RUN.id },
} as CalculationResult;

async function openHistory() {
  const user = userEvent.setup();
  render(<App />);
  await user.click(within(screen.getByRole("banner")).getByRole("button", { name: "History" }));
  return user;
}

it("lists past runs with their full calculation id", async () => {
  vi.mocked(listCalculations).mockResolvedValue([RUN]);
  await openHistory();
  expect(await screen.findByText("2026-03")).toBeTruthy();
  // The prefix alone cannot be used to export or quote a run.
  expect(screen.getByText("calc-abc-123")).toBeTruthy();
});

it("opens a stored run and shows its payouts without recalculating", async () => {
  vi.mocked(listCalculations).mockResolvedValue([RUN]);
  vi.mocked(getCalculationResult).mockResolvedValue(RESULT);
  vi.mocked(getCalculationInputs).mockResolvedValue([{
    id: "T1", payee_id: "P1", deal_id: "D1", period: "2026-03", amount: "10000.00",
    product: "Enterprise", close_date: "2026-03-14", metadata: "{}", created_at: "2026-03-14",
  }]);

  const user = await openHistory();
  await user.click(await screen.findByRole("button", { name: "Open" }));

  expect(await screen.findByText("Priya")).toBeTruthy();
  // The stat card and the per-payee row both carry it.
  expect(screen.getAllByText("GBP 1,000.00").length).toBe(2);
  expect(screen.getByText("Locked")).toBeTruthy();
  expect(screen.getByText("Enterprise")).toBeTruthy();
  expect(getCalculationResult).toHaveBeenCalledWith(RUN.id);
});

it("shows the payable total, not the sum of the raw commission lines", async () => {
  vi.mocked(listCalculations).mockResolvedValue([RUN]);
  // Raw lines sum to 1000.4444; the plan's rounding makes 1000.45 payable.
  vi.mocked(getCalculationResult).mockResolvedValue({
    ...RESULT,
    summary: { P1: "1000.4444" },
    payouts: [{ payee_id: "P1", name: "Priya", period: "2026-03", currency: "GBP", total: "1000.45" }],
    payout_totals: { GBP: "1000.45" },
  });

  const user = await openHistory();
  await user.click(await screen.findByRole("button", { name: "Open" }));

  expect((await screen.findAllByText("GBP 1,000.45")).length).toBeGreaterThan(0);
  expect(screen.queryByText("1,000.44")).toBeNull();
});

it("keeps currencies apart in the totals", async () => {
  vi.mocked(listCalculations).mockResolvedValue([RUN]);
  vi.mocked(getCalculationResult).mockResolvedValue({
    ...RESULT,
    payout_totals: { GBP: "1000.00", USD: "250.00" },
  });

  const user = await openHistory();
  await user.click(await screen.findByRole("button", { name: "Open" }));

  expect(await screen.findByText("Total Payable (GBP)")).toBeTruthy();
  expect(screen.getByText("Total Payable (USD)")).toBeTruthy();
  // A single combined figure across currencies would be meaningless.
  expect(screen.queryByText("1,250.00")).toBeNull();
});

it("exports statements from the stored result", async () => {
  vi.mocked(listCalculations).mockResolvedValue([RUN]);
  vi.mocked(getCalculationResult).mockResolvedValue(RESULT);

  const user = await openHistory();
  await user.click(await screen.findByRole("button", { name: "Open" }));
  await user.click(await screen.findByRole("button", { name: "Download PDF" }));

  expect(exportSavedStatements).toHaveBeenCalledWith(RESULT, ["pdf"]);
});

it("warns when the audit ledger could not be loaded in full", async () => {
  vi.mocked(listCalculations).mockResolvedValue([RUN]);
  vi.mocked(getCalculationResult).mockResolvedValue({ ...RESULT, ledger_truncated: true });

  const user = await openHistory();
  await user.click(await screen.findByRole("button", { name: "Open" }));

  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toContain("incomplete");
});

it("surfaces a calculation that has no stored snapshot instead of failing silently", async () => {
  vi.mocked(listCalculations).mockResolvedValue([RUN]);
  vi.mocked(getCalculationResult).mockRejectedValue(
    new Error("This older calculation has no statement snapshot."),
  );

  const user = await openHistory();
  await user.click(await screen.findByRole("button", { name: "Open" }));

  expect((await screen.findByRole("alert")).textContent).toContain("no statement snapshot");
});

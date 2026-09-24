import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "../App";
import { calculate, exportSavedStatements, parsePayees, previewFile } from "../api";
import type { PayeeRow } from "../components/Pipeline";
import StageReporting from "../components/StageReporting";
import { payeeCsv } from "../payeeCsv";

vi.mock("../api", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api")>(),
  listPlans: vi.fn().mockResolvedValue([]),
  parsePayees: vi.fn(),
  previewFile: vi.fn(),
  calculate: vi.fn(),
  exportSavedStatements: vi.fn(),
}));

afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

const payee = (id: string): PayeeRow => ({ id, name: 'Alex "Ace", Sales', quota: "10000", plan_id: "plan",
  effective_from: "2026-01-01", effective_to: "", email: "", ramp_months: "", ramp_schedule: "",
  category_quotas: "{}", draw_amount: "", draw_recoverable: "", manager_id: "", manager_override: "", team_id: "" });

it("keeps the pipeline stage when switching main tabs", async () => {
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole("button", { name: "Quotas" }));
  const header = within(screen.getByRole("banner"));
  await user.click(header.getByRole("button", { name: "Quick Calc" }));
  await user.click(header.getByRole("button", { name: "Pipeline" }));
  expect(screen.getByRole("heading", { name: /2. Quotas/ })).toBeTruthy();
});

it("offers a guided sample run before asking for files", () => {
  render(<App />);
  expect(screen.getByRole("button", { name: "Try sample data" })).toBeTruthy();
});

it("imports a full roster and preserves the original transaction file across steps and tabs", async () => {
  const user = userEvent.setup();
  vi.mocked(parsePayees).mockResolvedValue(Array.from({ length: 125 }, (_, i) => payee(`P${i}`)));
  vi.mocked(previewFile).mockResolvedValue({ headers: ["id", "payee_id", "period", "amount"],
    preview_rows: [["T1", "P0", "2026-08", "10000"]],
    mapping: { id: "id", payee_id: "payee_id", period: "period", amount: "amount" }, is_xlsx: false });
  vi.mocked(calculate).mockResolvedValue({ commissions: [], ledger: [], summary: {}, calculation_ids: { "2026-08": "run-123" } });
  render(<App />);
  await user.upload(screen.getByLabelText("Upload Payees"), new File(["full roster"], "payees.csv"));
  expect(await screen.findByRole("button", { name: "125 payees loaded →" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Crediting" }));
  const original = new File(["full transactions with extra metadata and more than 50 rows"], "transactions.csv");
  await user.upload(screen.getByLabelText("Upload Transactions"), original);
  await screen.findByText(/full original file will be calculated/);
  await user.click(screen.getByRole("button", { name: "Quotas" }));
  const header = within(screen.getByRole("banner"));
  await user.click(header.getByRole("button", { name: "Quick Calc" }));
  await user.click(header.getByRole("button", { name: "Pipeline" }));
  await user.click(screen.getByRole("button", { name: "Crediting" }));
  await user.click(screen.getByRole("button", { name: "Calculate Commissions →" }));
  expect(vi.mocked(calculate).mock.calls[0][0].transactions).toBe(original);
  await screen.findByRole("heading", { name: /6. Attainment/ });
  expect((screen.getByRole("button", { name: "Reporting" }) as HTMLButtonElement).disabled).toBe(false);
  await user.click(screen.getAllByRole("button", { name: "Payees" }).find(b => !screen.getByRole("banner").contains(b))!);
  await user.click(screen.getByRole("button", { name: "Clear" }));
  expect((screen.getByRole("button", { name: "Reporting" }) as HTMLButtonElement).disabled).toBe(true);
});

it("keeps a valid roster and shows an error when a replacement fails", async () => {
  const user = userEvent.setup();
  vi.mocked(parsePayees).mockResolvedValueOnce([payee("P1")]).mockRejectedValueOnce(new Error("Row 81: invalid quota"));
  render(<App />);
  await user.upload(screen.getByLabelText("Upload Payees"), new File(["valid"], "payees.csv"));
  await screen.findByRole("button", { name: "1 payees loaded →" });
  await user.upload(screen.getByLabelText("Upload Payees"), new File(["invalid"], "bad.csv"));
  expect((await screen.findByRole("alert")).textContent).toContain("Row 81");
  expect(screen.getByRole("button", { name: "1 payees loaded →" })).toBeTruthy();
  expect((screen.getByLabelText("Upload Payees") as HTMLInputElement).disabled).toBe(false);
});

it("exports the reviewed result directly from Reporting and exposes export errors", async () => {
  const user = userEvent.setup();
  const result = { commissions: [], ledger: [], summary: { P1: "0.02" },
    calculation_ids: { "2026-08": "reviewed-run" },
    payouts: [{ payee_id: "P1", name: "Alex", period: "2026-08", currency: "USD", total: "0.02" }],
    payout_totals: { USD: "0.02" } };
  vi.mocked(exportSavedStatements).mockRejectedValueOnce(new Error("Export unavailable")).mockResolvedValueOnce("D:\\Exports\\statements.zip");
  render(<StageReporting result={result} onBack={vi.fn()} />);
  await user.click(screen.getByLabelText("PDF"));
  await user.click(screen.getByRole("button", { name: "Download statements" }));
  expect(vi.mocked(exportSavedStatements).mock.calls[0]).toEqual([result, ["xlsx", "pdf"]]);
  expect((await screen.findByRole("alert")).textContent).toBe("Export unavailable");
  await user.click(screen.getByRole("button", { name: "Download statements" }));
  expect((await screen.findByRole("status")).textContent).toContain("statements.zip");
  expect(screen.queryByRole("button", { name: /Back to Calculator/ })).toBeNull();
});

it("serializes quoted names and period quotas without losing precision", () => {
  const csv = payeeCsv([{ ...payee("P1"), quota: "10000.123456789", quotas: { "2026-08": "8000.987654321" } }]);
  expect(csv).toContain('"Alex ""Ace"", Sales"');
  expect(csv).toContain('"10000.123456789"');
  expect(csv).toContain('"8000.987654321"');
  expect(csv.split("\r\n")).toHaveLength(3);
  expect(csv).toContain('"2026-08"');
});

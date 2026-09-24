import { useCallback, useEffect, useState } from "react";
import {
  exportSavedStatements,
  getCalculationInputs,
  getCalculationResult,
  listCalculations,
  listPlans,
  type CalculationRow,
  type TransactionRow,
} from "../api";
import type { CalculationResult, SavedPlan } from "../types";
import CommissionsTable from "./CommissionsTable";
import PayeeTrace from "./PayeeTrace";
import { Th, Td } from "./Table";

const MONEY = { minimumFractionDigits: 2, maximumFractionDigits: 2 } as const;

function money(value: string): string {
  return parseFloat(value).toLocaleString(undefined, MONEY);
}

/**
 * Past runs, read back from storage.
 *
 * Nothing here recalculates. Every number comes from the stored commission
 * lines and the payees/plans snapshot frozen when the run happened, so a
 * statement re-issued in June still shows what March actually paid.
 */
export default function History() {
  const [rows, setRows] = useState<CalculationRow[]>([]);
  const [plans, setPlans] = useState<SavedPlan[]>([]);
  const [planFilter, setPlanFilter] = useState("");
  const [periodFilter, setPeriodFilter] = useState("");
  const [listError, setListError] = useState("");
  const [loadingList, setLoadingList] = useState(true);
  const [reloadToken, setReloadToken] = useState(0);

  const [result, setResult] = useState<CalculationResult | null>(null);
  const [inputs, setInputs] = useState<TransactionRow[]>([]);
  const [detailError, setDetailError] = useState("");
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [exportMessage, setExportMessage] = useState("");
  const [tracePayee, setTracePayee] = useState("");

  // Cancellation matters here: changing the filter twice quickly must not let
  // the first response land on top of the second.
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      listCalculations(planFilter || undefined, periodFilter || undefined),
      listPlans(),
    ])
      .then(([calcs, savedPlans]) => {
        if (cancelled) return;
        setRows(calcs);
        setPlans(savedPlans);
        setListError("");
      })
      .catch((e: unknown) => {
        if (!cancelled) setListError(e instanceof Error ? e.message : "Could not load past runs.");
      })
      .finally(() => { if (!cancelled) setLoadingList(false); });
    return () => { cancelled = true; };
  }, [planFilter, periodFilter, reloadToken]);

  const open = useCallback(async (calculationId: string) => {
    setLoadingDetail(true);
    setDetailError("");
    setExportMessage("");
    setTracePayee("");
    try {
      const [loaded, linked] = await Promise.all([
        getCalculationResult(calculationId),
        getCalculationInputs(calculationId),
      ]);
      setResult(loaded);
      setInputs(linked);
    } catch (e: unknown) {
      setResult(null);
      setInputs([]);
      setDetailError(e instanceof Error ? e.message : "Could not open this calculation.");
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  const exportStatements = useCallback(async (formats: string[]) => {
    if (!result) return;
    setExportMessage("");
    try {
      const savedTo = await exportSavedStatements(result, formats);
      setExportMessage(savedTo ? "Statements saved to " + savedTo : "Statements downloaded.");
    } catch (e: unknown) {
      setDetailError(e instanceof Error ? e.message : "Export failed.");
    }
  }, [result]);

  if (result || loadingDetail || detailError) {
    return (
      <Detail
        result={result}
        inputs={inputs}
        loading={loadingDetail}
        error={detailError}
        exportMessage={exportMessage}
        tracePayee={tracePayee}
        onTracePayee={setTracePayee}
        onExport={exportStatements}
        onBack={() => { setResult(null); setDetailError(""); setInputs([]); setTracePayee(""); }}
      />
    );
  }

  return (
    <div className="max-w-5xl space-y-4">
      <div>
        <h1 className="text-lg font-bold text-ink">History</h1>
        <p className="text-sm text-ink2">
          Every run that has been calculated and saved. Opening one reads back the stored
          result -- it never recalculates, so later plan or roster edits cannot change it.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-ink2">
          Plan
          <select
            aria-label="Filter by plan"
            value={planFilter}
            onChange={e => setPlanFilter(e.target.value)}
            className="block mt-1 px-2 py-1.5 text-sm border border-line rounded-lg bg-white text-ink"
          >
            <option value="">All plans</option>
            {plans.map(p => <option key={p.id} value={p.id}>{p.name || p.id}</option>)}
          </select>
        </label>
        <label className="text-xs text-ink2">
          Period
          <input
            aria-label="Filter by period"
            value={periodFilter}
            onChange={e => setPeriodFilter(e.target.value)}
            placeholder="2026-01"
            className="block mt-1 px-2 py-1.5 text-sm border border-line rounded-lg bg-white text-ink w-32"
          />
        </label>
        <button
          onClick={() => setReloadToken(t => t + 1)}
          className="px-3 py-1.5 text-sm rounded-lg border border-line text-ink2 hover:text-ink cursor-pointer"
        >
          Refresh
        </button>
      </div>

      {listError && <p role="alert" className="p-3 bg-red-50 text-red-700 text-sm rounded-lg">{listError}</p>}

      {loadingList ? (
        <p className="text-sm text-ink2">Loading past runs...</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-ink2">
          No saved runs yet. Calculate a period in the Pipeline and it will appear here.
        </p>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-soft border-b border-line">
                <Th>Period</Th><Th>Plan</Th><Th>Version</Th><Th>Status</Th>
                <Th>Calculated</Th><Th>ID</Th><Th>Action</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map(row => (
                <tr key={row.id} className="hover:bg-soft">
                  <Td mono>{row.period || "(none)"}</Td>
                  <Td mono>{row.plan_id}</Td>
                  <Td>{row.version}</Td>
                  <Td>{row.status}</Td>
                  <Td>{row.created_at?.slice(0, 16)}</Td>
                  {/* Full id, not a prefix: it is what identifies a run in an export or a support thread. */}
                  <Td mono className="text-ink2 select-all">{row.id}</Td>
                  <Td>
                    <button
                      onClick={() => void open(row.id)}
                      className="px-2 py-1 rounded-lg text-xs font-medium bg-blue-600 text-white hover:bg-blue-700 cursor-pointer"
                    >
                      Open
                    </button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

interface DetailProps {
  result: CalculationResult | null;
  inputs: TransactionRow[];
  loading: boolean;
  error: string;
  exportMessage: string;
  tracePayee: string;
  onTracePayee: (payeeId: string) => void;
  onExport: (formats: string[]) => void;
  onBack: () => void;
}

function Detail({
  result, inputs, loading, error, exportMessage, tracePayee, onTracePayee, onExport, onBack,
}: DetailProps) {
  // payout_totals is the payable figure -- per-payee totals already put through
  // the plan's rounding policy, and kept apart by currency. Summing the raw
  // commission lines instead would show a total nobody is actually paid.
  const totals = Object.entries(result?.payout_totals ?? {});

  return (
    <div className="max-w-5xl space-y-4">
      <button
        onClick={onBack}
        className="text-sm text-ink2 hover:text-ink transition-colors cursor-pointer"
      >
        &lt;- Back to history
      </button>

      {loading && <p className="text-sm text-ink2">Loading the stored result...</p>}
      {error && <p role="alert" className="p-3 bg-red-50 text-red-700 text-sm rounded-lg">{error}</p>}

      {result && (
        <>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h1 className="text-lg font-bold text-ink">{result.period || "(no period)"}</h1>
            <span className="text-sm text-ink2">{result.plan_id}</span>
            <span className="text-xs text-ink2">version {result.version}</span>
            <span className="text-xs text-ink2">{result.status}</span>
            {result.locked && (
              <span className="px-2 py-0.5 rounded-full text-xs bg-amber-100 text-amber-800">Locked</span>
            )}
            <span className="text-xs text-ink2">calculated {result.created_at?.slice(0, 16)}</span>
          </div>

          {result.ledger_truncated && (
            <p role="alert" className="p-3 bg-amber-50 text-amber-800 text-sm rounded-lg">
              This run's audit ledger is too large to load in full, so the traces below are
              incomplete. Use the CLI (icm trace) for the whole record.
            </p>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {totals.length > 0 ? (
              totals.map(([currency, amount]) => (
                <Stat
                  key={currency}
                  label={"Total Payable (" + currency + ")"}
                  value={currency + " " + money(amount)}
                />
              ))
            ) : (
              <Stat label="Total Payable" value="none" />
            )}
            <Stat label="Payees" value={String(Object.keys(result.summary).length)} />
            <Stat label="Commission Lines" value={String(result.commissions.length)} />
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2">
            {exportMessage && <span className="text-sm text-ink2 mr-auto">{exportMessage}</span>}
            {[["xlsx", "Excel"], ["pdf", "PDF"], ["html", "HTML"]].map(([format, label]) => (
              <button
                key={format}
                onClick={() => onExport([format])}
                className="px-3 py-1.5 rounded-lg text-sm font-medium bg-soft border border-line text-ink hover:border-ink2 cursor-pointer"
              >
                Download {label}
              </button>
            ))}
          </div>

          <div className="card overflow-hidden">
            <div className="px-5 py-3 border-b border-line">
              <h3 className="text-sm font-semibold text-ink">Per Payee</h3>
            </div>
            <div className="divide-y divide-line">
              {result.payouts?.length
                ? result.payouts.map(p => (
                    <div key={p.payee_id + p.period} className="px-5 py-2.5 flex justify-between items-center text-sm gap-3">
                      <span className="text-ink font-medium">{p.name || p.payee_id}</span>
                      <span className="flex items-center gap-3">
                        <span className="text-ink font-mono">{p.currency} {money(p.total)}</span>
                        <button
                          onClick={() => onTracePayee(p.payee_id)}
                          className="text-xs text-blue-600 hover:underline cursor-pointer"
                        >
                          Explain
                        </button>
                      </span>
                    </div>
                  ))
                : Object.entries(result.summary).map(([payeeId, amount]) => (
                    <div key={payeeId} className="px-5 py-2.5 flex justify-between items-center text-sm gap-3">
                      <span className="text-ink font-medium">{payeeId}</span>
                      <span className="flex items-center gap-3">
                        <span className="text-ink font-mono">{money(amount)}</span>
                        <button
                          onClick={() => onTracePayee(payeeId)}
                          className="text-xs text-blue-600 hover:underline cursor-pointer"
                        >
                          Explain
                        </button>
                      </span>
                    </div>
                  ))}
            </div>
          </div>

          <CommissionsTable
            commissions={result.commissions}
            ledger={result.ledger}
            calculationId={result.calculation_id}
          />

          <div className="card overflow-hidden">
            <div className="px-5 py-3 border-b border-line">
              <h3 className="text-sm font-semibold text-ink">
                Input Transactions ({inputs.length})
              </h3>
              <p className="text-xs text-ink2 mt-0.5">The deals this run was calculated from.</p>
            </div>
            <div className="overflow-x-auto max-h-96">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-soft">
                  <tr className="border-b border-line">
                    <Th>ID</Th><Th>Payee</Th><Th>Deal</Th><Th>Period</Th>
                    <Th>Amount</Th><Th>Product</Th><Th>Close date</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {inputs.map(t => (
                    <tr key={t.id} className="hover:bg-soft">
                      <Td mono>{t.id}</Td>
                      <Td mono>{t.payee_id}</Td>
                      <Td mono>{t.deal_id}</Td>
                      <Td mono>{t.period}</Td>
                      <Td mono>{t.amount}</Td>
                      <Td>{t.product ?? ""}</Td>
                      <Td>{t.close_date ?? ""}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {tracePayee && (
            <PayeeTrace
              payeeId={tracePayee}
              period={result.period}
              commissions={result.commissions}
              ledger={result.ledger}
              onClose={() => onTracePayee("")}
            />
          )}
        </>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card p-4">
      <div className="text-xs text-ink2">{label}</div>
      <div className="text-lg font-bold text-ink mt-0.5">{value}</div>
    </div>
  );
}

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
import { money } from "../format";
import CommissionsTable from "./CommissionsTable";
import PayeeTrace from "./PayeeTrace";
import StatementPreview, { type StatementTarget } from "./StatementPreview";
import { Th, Td } from "./Table";
import {
  Badge, Button, Callout, Card, CardHeader, EmptyState, Icon, PageHeader, Spinner, Stat, type IconName,
} from "./ui";

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
    <div className="animate-in">
      <PageHeader
        title="History"
        description="Every run that has been calculated and saved. Opening one reads back the stored result -- it never recalculates, so later plan or roster edits cannot change it."
        actions={
          <Button icon="refresh" onClick={() => setReloadToken(t => t + 1)}>Refresh</Button>
        }
      />

      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-end gap-3 border-b border-line px-5 py-3.5">
          <label className="text-[12.5px] font-medium text-ink-2">
            Plan
            <select
              aria-label="Filter by plan"
              value={planFilter}
              onChange={e => setPlanFilter(e.target.value)}
              className="mt-1 block min-w-44"
            >
              <option value="">All plans</option>
              {plans.map(p => <option key={p.id} value={p.id}>{p.name || p.id}</option>)}
            </select>
          </label>
          <label className="text-[12.5px] font-medium text-ink-2">
            Period
            <input
              aria-label="Filter by period"
              value={periodFilter}
              onChange={e => setPeriodFilter(e.target.value)}
              placeholder="2026-01"
              className="mt-1 block w-32"
            />
          </label>
          {!loadingList && rows.length > 0 && (
            <span className="ml-auto pb-2 text-[12.5px] text-ink-3 num">{rows.length} run{rows.length === 1 ? "" : "s"}</span>
          )}
        </div>

        {listError && <div className="p-4"><Callout tone="danger" role="alert">{listError}</Callout></div>}

        {loadingList ? (
          <div className="flex items-center justify-center gap-2 py-14 text-[13px] text-ink-2">
            <Spinner /> Loading past runs...
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon="history"
            title="No saved runs yet"
            description="Calculate a period in the Pipeline and it will appear here."
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <Th>Period</Th><Th>Plan</Th><Th>Version</Th><Th>Status</Th>
                  <Th>Calculated</Th><Th>Run ID</Th><Th className="w-0" />
                </tr>
              </thead>
              <tbody>
                {rows.map(row => (
                  <tr key={row.id}>
                    <Td mono className="font-medium">{row.period || "(none)"}</Td>
                    <Td mono className="text-ink-2">{row.plan_id}</Td>
                    <Td><span className="badge num">v{row.version}</span></Td>
                    <Td><StatusBadge status={row.status} /></Td>
                    <Td className="text-ink-2">{row.created_at?.slice(0, 16).replace("T", " ")}</Td>
                    {/* Full id, not a prefix: it is what identifies a run in an export or a support thread. */}
                    <td className="max-w-[150px] break-all font-mono text-[11px] leading-snug text-ink-3 select-all">{row.id}</td>
                    <Td className="text-right">
                      <Button size="sm" iconRight="chevronRight" onClick={() => void open(row.id)}>Open</Button>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const tone = status === "completed" ? "success" : status === "failed" ? "danger" : "neutral";
  return <Badge tone={tone}>{status}</Badge>;
}

const EXPORTS: [format: string, label: string, icon: IconName][] = [
  ["html", "HTML", "sparkles"], ["pdf", "PDF", "printer"], ["xlsx", "Excel", "table"],
];

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
  const [preview, setPreview] = useState<StatementTarget | null>(null);
  // payout_totals is the payable figure -- per-payee totals already put through
  // the plan's rounding policy, and kept apart by currency. Summing the raw
  // commission lines instead would show a total nobody is actually paid.
  const totals = Object.entries(result?.payout_totals ?? {});

  return (
    <div className="space-y-5 animate-in">
      <Button variant="ghost" size="sm" icon="arrowLeft" onClick={onBack} className="-ml-2">
        Back to history
      </Button>

      {loading && (
        <div className="flex items-center gap-2 text-[13px] text-ink-2"><Spinner /> Loading the stored result...</div>
      )}
      {error && <Callout tone="danger" role="alert">{error}</Callout>}

      {result && (
        <>
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="eyebrow mb-1.5">Saved run</p>
              <h1 className="text-[22px] font-semibold leading-tight tracking-[-0.015em] text-ink">
                {result.period || "(no period)"}
              </h1>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[12.5px] text-ink-2">
                <span className="font-mono">{result.plan_id}</span>
                <span className="text-line-strong">/</span>
                <span className="badge num">version {result.version}</span>
                <StatusBadge status={result.status} />
                {result.locked && <Badge tone="warning" icon="lock">Locked</Badge>}
                <span className="inline-flex items-center gap-1 text-ink-3">
                  <Icon name="clock" size={13} />calculated {result.created_at?.slice(0, 16).replace("T", " ")}
                </span>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {EXPORTS.map(([format, label, icon]) => (
                <Button key={format} icon={icon} onClick={() => onExport([format])}>
                  Download {label}
                </Button>
              ))}
            </div>
          </div>

          {exportMessage && <Callout tone="success">{exportMessage}</Callout>}

          {result.ledger_truncated && (
            <Callout tone="warning" role="alert">
              This run's audit ledger is too large to load in full, so the traces below are
              incomplete. Use the CLI (icm trace) for the whole record.
            </Callout>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {totals.length > 0 ? (
              totals.map(([currency, amount]) => (
                <Stat
                  key={currency}
                  icon="coins"
                  label={"Total payable (" + currency + ")"}
                  value={currency + " " + money(amount)}
                />
              ))
            ) : (
              <Stat label="Total payable" value="none" icon="coins" />
            )}
            <Stat label="Payees" value={String(Object.keys(result.summary).length)} icon="users" />
            <Stat label="Commission lines" value={String(result.commissions.length)} icon="listChecks" />
          </div>

          <Card className="overflow-hidden">
            <CardHeader
              title="Per payee"
              description="Preview the statement each person receives, or explain how their payout was built."
            />
            <ul className="divide-y divide-line">
              {result.payouts?.length
                ? result.payouts.map(p => (
                    <PayeeRow
                      key={p.payee_id + p.period}
                      name={p.name || p.payee_id}
                      id={p.payee_id}
                      amount={p.currency + " " + money(p.total)}
                      onPreview={() => setPreview({ payeeId: p.payee_id, name: p.name || p.payee_id, period: p.period })}
                      onExplain={() => onTracePayee(p.payee_id)}
                    />
                  ))
                : Object.entries(result.summary).map(([payeeId, amount]) => (
                    <PayeeRow
                      key={payeeId}
                      name={payeeId}
                      amount={money(amount)}
                      onExplain={() => onTracePayee(payeeId)}
                    />
                  ))}
            </ul>
          </Card>

          <CommissionsTable
            commissions={result.commissions}
            ledger={result.ledger}
            calculationId={result.calculation_id}
          />

          <Card className="overflow-hidden">
            <CardHeader
              title={`Input transactions (${inputs.length})`}
              description="The deals this run was calculated from."
            />
            <div className="table-wrap max-h-96">
              <table>
                <thead>
                  <tr>
                    <Th>ID</Th><Th>Payee</Th><Th>Deal</Th><Th>Period</Th>
                    <Th className="text-right">Amount</Th><Th>Product</Th><Th>Close date</Th>
                  </tr>
                </thead>
                <tbody>
                  {inputs.map(t => (
                    <tr key={t.id}>
                      <Td mono>{t.id}</Td>
                      <Td mono>{t.payee_id}</Td>
                      <Td mono>{t.deal_id}</Td>
                      <Td mono className="text-ink-2">{t.period}</Td>
                      <Td num>{t.amount}</Td>
                      <Td>{t.product ?? ""}</Td>
                      <Td className="text-ink-2">{t.close_date ?? ""}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {tracePayee && (
            <PayeeTrace
              payeeId={tracePayee}
              period={result.period}
              commissions={result.commissions}
              ledger={result.ledger}
              onClose={() => onTracePayee("")}
            />
          )}

          <StatementPreview
            target={preview}
            calculationIds={[result.calculation_id]}
            sample={result.sample}
            onClose={() => setPreview(null)}
          />
        </>
      )}
    </div>
  );
}

function PayeeRow({ name, id, amount, onPreview, onExplain }: {
  name: string; id?: string; amount: string; onPreview?: () => void; onExplain: () => void;
}) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
      <div className="flex min-w-0 items-center gap-3">
        <span aria-hidden="true" className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-accent-soft text-[12px] font-semibold text-accent-ink">
          {initials(name)}
        </span>
        <div className="min-w-0">
          <span className="block truncate text-[13.5px] font-medium text-ink">{name}</span>
          {id && id !== name && <span className="block font-mono text-[11.5px] text-ink-3">{id}</span>}
        </div>
      </div>
      <div className="flex items-center gap-2">
        <span className="num mr-2 text-[13.5px] font-semibold text-ink">{amount}</span>
        {onPreview && (
          <Button size="sm" icon="eye" aria-label={`Statement for ${name}`} onClick={onPreview}>Statement</Button>
        )}
        <Button size="sm" variant="ghost" icon="info" onClick={onExplain}>Explain</Button>
      </div>
    </li>
  );
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() || "?";
}

import { useCallback, useEffect, useState } from "react";
import { fetchTrace } from "../api";
import type { OrderTrace, TraceStep } from "../types";
import { Callout, Drawer, Icon, Spinner } from "./ui";
import { cx } from "./ui/cx";

interface Props {
  transactionId: string;
  payeeId: string;
  /** Pins the trace to one stored run; omitted for a live result. */
  calculationId?: string;
  onClose: () => void;
}

export default function TracePanel({ transactionId, payeeId, calculationId, onClose }: Props) {
  const [trace, setTrace] = useState<OrderTrace | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expandedEvents, setExpandedEvents] = useState<Set<number>>(new Set());

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    fetchTrace(transactionId, payeeId, calculationId)
      .then((data) => { if (!cancelled) setTrace(data); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [transactionId, payeeId, calculationId]);

  const toggleEvent = useCallback((stepIdx: number) => {
    setExpandedEvents((prev) => {
      const next = new Set(prev);
      if (next.has(stepIdx)) next.delete(stepIdx);
      else next.add(stepIdx);
      return next;
    });
  }, []);

  return (
    <Drawer open onClose={onClose} title="Order trace" subtitle={`${transactionId} · ${payeeId}`} width={460}>
      <div className="space-y-4 px-5 py-4">
        {loading && (
          <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-ink-2">
            <Spinner /> Loading trace...
          </div>
        )}

        {error && <Callout tone="danger">{error}</Callout>}

        {trace && (
          <>
            {/* Order header */}
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 rounded-xl border border-line bg-surface-2/60 px-4 py-3 text-[12.5px]">
              {Object.entries(trace.order).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-ink-3">{k}</dt>
                  <dd className="truncate text-right font-medium text-ink">{String(v)}</dd>
                </div>
              ))}
            </dl>

            {/* Steps */}
            <ol className="space-y-3">
              {trace.steps.map((step, si) => (
                <StepCard key={si} step={step} expanded={expandedEvents.has(si)} onToggle={() => toggleEvent(si)} />
              ))}
            </ol>

            {/* Total */}
            <div className="flex items-center justify-between rounded-xl bg-accent-soft px-4 py-3 text-sm">
              <span className="font-semibold text-accent-ink">Total from this order</span>
              <span className="num font-semibold text-accent-ink">{trace.total}</span>
            </div>

            <p className="text-[12.5px] text-ink-2">{trace.summary}</p>
          </>
        )}
      </div>
    </Drawer>
  );
}

function StepCard({ step, expanded, onToggle }: { step: TraceStep; expanded: boolean; onToggle: () => void }) {
  const matched = step.status === "matched";
  return (
    <li className={cx("overflow-hidden rounded-xl border", matched ? "border-success/30 bg-success-soft/50" : "border-line bg-surface")}>
      <div className="flex items-center justify-between gap-2 px-3.5 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <Icon name={matched ? "checkCircle" : "minus"} size={15} className={matched ? "text-success-ink" : "text-ink-3"} />
          <span className="truncate font-mono text-[12.5px] font-medium text-ink">{step.rule_id}</span>
          <span className={cx("text-[12px] font-medium", matched ? "text-success-ink" : "text-ink-3")}>
            {matched ? "matched" : "skipped"}
          </span>
        </div>
        {step.reason && <span className="badge">{step.reason}</span>}
      </div>

      <div className="space-y-1 px-3.5 pb-2.5">
        {step.events.map((ev, ei) => (
          <p key={ei} className="text-[12.5px] text-ink-2">{ev.human_readable}</p>
        ))}
      </div>

      <button
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center gap-1 border-t border-line/70 px-3.5 py-1.5 text-[12px] text-ink-3 hover:text-ink"
      >
        <Icon name={expanded ? "chevronUp" : "chevronDown"} size={13} />
        {expanded ? "Hide raw events" : "Show raw events"}
      </button>
      {expanded && (
        <div className="px-3.5 pb-3">
          <pre className="audit-panel max-h-48 p-3 text-[11.5px] select-text">{JSON.stringify(step.events, null, 2)}</pre>
        </div>
      )}
    </li>
  );
}

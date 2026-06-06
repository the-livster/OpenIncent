import { useCallback, useEffect, useState } from "react";
import { fetchTrace } from "../api";
import type { OrderTrace, TraceStep } from "../types";

interface Props {
  transactionId: string;
  payeeId: string;
  onClose: () => void;
}

export default function TracePanel({ transactionId, payeeId, onClose }: Props) {
  const [trace, setTrace] = useState<OrderTrace | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expandedEvents, setExpandedEvents] = useState<Set<number>>(new Set());

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    fetchTrace(transactionId, payeeId)
      .then((data) => { if (!cancelled) setTrace(data); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [transactionId, payeeId]);

  const toggleEvent = useCallback((stepIdx: number) => {
    setExpandedEvents((prev) => {
      const next = new Set(prev);
      if (next.has(stepIdx)) next.delete(stepIdx);
      else next.add(stepIdx);
      return next;
    });
  }, []);

  return (
    <div className="fixed inset-y-0 right-0 w-[420px] max-w-[90vw] bg-white border-l border-line shadow-xl z-50 flex flex-col animate-in">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-line shrink-0">
        <div>
          <h3 className="text-sm font-semibold text-ink">Order Trace</h3>
          <p className="text-xs text-ink2 mt-0.5">
            {transactionId} &middot; {payeeId}
          </p>
        </div>
        <button
          onClick={onClose}
          className="text-ink2 hover:text-ink cursor-pointer text-lg leading-none px-1"
        >
          ×
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
        {loading && (
          <div className="text-center py-8">
            <div className="inline-block w-5 h-5 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
            <p className="text-xs text-ink2 mt-2">Loading trace...</p>
          </div>
        )}

        {error && (
          <div className="px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-red-700 text-xs select-text">
            {error}
          </div>
        )}

        {trace && (
          <>
            {/* Order header */}
            <div className="px-3 py-2.5 rounded-lg bg-soft text-xs space-y-1">
              {Object.entries(trace.order).map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span className="text-ink2">{k}</span>
                  <span className="text-ink font-medium">{String(v)}</span>
                </div>
              ))}
            </div>

            {/* Steps */}
            {trace.steps.map((step, si) => (
              <StepCard key={si} step={step} expanded={expandedEvents.has(si)} onToggle={() => toggleEvent(si)} />
            ))}

            {/* Total */}
            <div className="flex justify-between items-center px-3 py-2.5 border-t-2 border-line text-sm">
              <span className="font-semibold text-ink">Total from this order</span>
              <span className="font-mono font-semibold text-success">${trace.total}</span>
            </div>

            {/* Summary */}
            <p className="text-xs text-ink2">{trace.summary}</p>
          </>
        )}
      </div>
    </div>
  );
}

function StepCard({ step, expanded, onToggle }: { step: TraceStep; expanded: boolean; onToggle: () => void }) {
  const matched = step.status === "matched";
  return (
    <div className={`rounded-lg border ${matched ? "border-green-200 bg-green-50/50" : "border-line bg-soft"}`}>
      <div className="flex items-center justify-between px-3 py-2.5">
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${matched ? "bg-green-500" : "bg-ink2/40"}`} />
          <span className="text-xs font-medium text-ink">{step.rule_id}</span>
          <span className={`text-xs font-medium ${matched ? "text-green-700" : "text-ink2"}`}>
            {matched ? "matched" : "skipped"}
          </span>
        </div>
        {step.reason && (
          <span className="text-xs text-ink2 bg-white/60 px-1.5 py-0.5 rounded">{step.reason}</span>
        )}
      </div>

      {/* Event summaries */}
      <div className="px-3 pb-2 space-y-1">
        {step.events.map((ev, ei) => (
          <div key={ei} className="text-xs text-ink2">{ev.human_readable}</div>
        ))}
      </div>

      {/* Expand raw events */}
      <button
        onClick={onToggle}
        className="w-full px-3 py-1.5 text-xs text-ink2 hover:text-ink border-t border-line/50 cursor-pointer transition-colors"
      >
        {expanded ? "Hide raw events" : "Show raw events"}
      </button>
      {expanded && (
        <div className="px-3 pb-2.5">
          <pre className="text-xs text-ink2 bg-white rounded p-2 overflow-x-auto select-text max-h-48">
            {JSON.stringify(step.events, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

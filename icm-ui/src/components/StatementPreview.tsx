import { useEffect, useState } from "react";
import { previewStatement } from "../api";
import { Callout, Drawer, Icon, Spinner } from "./ui";

export interface StatementTarget {
  payeeId: string;
  name: string;
  period: string;
}

/**
 * The payee's interactive statement, rendered by the engine and shown as-is.
 *
 * The frame is sandboxed without same-origin access: the statement's own
 * scripts (filters, CSV, print) run, but cannot reach the app or its storage.
 */
export default function StatementPreview({ target, calculationIds, sample, onClose }: {
  target: StatementTarget | null;
  calculationIds: string[];
  sample?: boolean;
  onClose: () => void;
}) {
  const [html, setHtml] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const key = target ? `${target.payeeId}|${target.period}|${calculationIds.join(",")}` : "";

  useEffect(() => {
    if (!target) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setHtml("");
    previewStatement({ calculationIds, payeeId: target.payeeId, period: target.period, sample }, controller.signal)
      .then(setHtml)
      .catch((e: unknown) => {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load this statement.");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
    // `key` captures everything the request depends on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return (
    <Drawer
      open={!!target}
      onClose={onClose}
      width={1000}
      title="Statement preview"
      subtitle={target ? `${target.name} · ${target.period} — exactly what they will receive` : undefined}
    >
      <div className="flex h-full flex-col bg-canvas">
        {loading && (
          <div className="flex flex-1 items-center justify-center gap-2 text-sm text-ink-2">
            <Spinner size={16} /> Rendering statement...
          </div>
        )}
        {error && <div className="p-5"><Callout tone="danger" role="alert">{error}</Callout></div>}
        {html && (
          <iframe
            title={target ? `Statement for ${target.name}` : "Statement"}
            srcDoc={html}
            sandbox="allow-scripts allow-modals allow-downloads"
            className="w-full flex-1 border-0 bg-canvas"
          />
        )}
        <p className="flex items-center gap-1.5 border-t border-line bg-surface px-5 py-2.5 text-xs text-ink-3">
          <Icon name="lock" size={13} />
          Only this person's figures are in the statement. Use its own Print and CSV buttons to save a copy.
        </p>
      </div>
    </Drawer>
  );
}

import type { ReactNode } from "react";
import { Button } from "./ui";
import { cx } from "./ui/cx";

/** The frame every Pipeline stage sits in: numbered heading, body, and a
 *  footer with Back / Continue. */
export default function StageShell({
  n, group, title, description, actions, children, onBack, onNext, nextLabel = "Continue", nextDisabled,
  footer, bodyClassName,
}: {
  n: number;
  group: string;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  onBack?: () => void;
  onNext?: () => void;
  nextLabel?: string;
  nextDisabled?: boolean;
  /** Extra controls on the right of the footer, before Continue. */
  footer?: ReactNode;
  bodyClassName?: string;
}) {
  return (
    <section className="card animate-in" aria-labelledby={`stage-${n}`}>
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-line px-6 pb-4 pt-5">
        <div className="min-w-0">
          <p className="eyebrow mb-1">{group}</p>
          <h2 id={`stage-${n}`} className="text-lg font-semibold tracking-[-0.01em] text-ink">
            <span className="num text-ink-3">{n}.</span> {title}
          </h2>
          {description && <p className="mt-1 max-w-2xl text-[13.5px] text-ink-2">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      <div className={cx("space-y-4 px-6 py-5", bodyClassName)}>{children}</div>
      {(onBack || onNext || footer) && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-b-[var(--radius-card)] border-t border-line bg-surface-2/60 px-6 py-3.5">
          <div>{onBack && <Button variant="ghost" icon="arrowLeft" onClick={onBack}>Back</Button>}</div>
          <div className="flex flex-wrap items-center gap-2">
            {footer}
            {onNext && (
              <Button variant="primary" iconRight="arrowRight" disabled={nextDisabled} onClick={onNext}>{nextLabel}</Button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

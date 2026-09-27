import { useEffect, useRef, type ButtonHTMLAttributes, type HTMLAttributes, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cx } from "./cx";
import { Icon, type IconName } from "./Icon";

export { Icon, LogoMark, type IconName } from "./Icon";


// ------------------------------------------------------------------
// Button
// ------------------------------------------------------------------

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "link";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm" | "md" | "lg";
  icon?: IconName;
  iconRight?: IconName;
  loading?: boolean;
}

export function Button({
  variant = "secondary", size = "md", icon, iconRight, loading, className, children, type = "button", ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx("btn", `btn-${variant}`, size !== "md" && `btn-${size}`, !children && "btn-icon", className)}
      {...rest}
    >
      {loading ? <Spinner size={size === "sm" ? 12 : 14} /> : icon && <Icon name={icon} />}
      {children}
      {iconRight && <Icon name={iconRight} />}
    </button>
  );
}

export function Spinner({ size = 14, className }: { size?: number; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cx("inline-block shrink-0 animate-spin rounded-full border-2 border-current border-r-transparent", className)}
      style={{ width: size, height: size }}
    />
  );
}

// ------------------------------------------------------------------
// Surfaces
// ------------------------------------------------------------------

export function Card({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cx("card", className)} {...rest}>{children}</div>;
}

export function CardHeader({ title, description, actions, icon, className }: {
  title: ReactNode; description?: ReactNode; actions?: ReactNode; icon?: IconName; className?: string;
}) {
  return (
    <div className={cx("flex flex-wrap items-start justify-between gap-3 px-5 py-4 border-b border-line", className)}>
      <div className="flex items-start gap-3 min-w-0">
        {icon && (
          <span className="grid place-items-center w-8 h-8 shrink-0 rounded-lg bg-surface-2 border border-line text-ink-2">
            <Icon name={icon} />
          </span>
        )}
        <div className="min-w-0">
          <h3 className="text-[15px] font-semibold text-ink leading-snug">{title}</h3>
          {description && <p className="mt-0.5 text-[13px] text-ink-2">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2 shrink-0">{actions}</div>}
    </div>
  );
}

export function PageHeader({ title, description, actions, eyebrow }: {
  title: ReactNode; description?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <p className="eyebrow mb-1.5">{eyebrow}</p>}
        <h1 className="text-[22px] leading-tight font-semibold tracking-[-0.015em] text-ink">{title}</h1>
        {description && <p className="mt-1.5 max-w-2xl text-[13.5px] text-ink-2">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

type Tone = "neutral" | "accent" | "success" | "warning" | "danger";

export function Badge({ tone = "neutral", icon, children, className }: {
  tone?: Tone; icon?: IconName; children: ReactNode; className?: string;
}) {
  return (
    <span className={cx("badge", tone !== "neutral" && `badge-${tone}`, className)}>
      {icon && <Icon name={icon} />}
      {children}
    </span>
  );
}

export function Stat({ label, value, hint, icon, className }: {
  label: ReactNode; value: ReactNode; hint?: ReactNode; icon?: IconName; className?: string;
}) {
  return (
    <div className={cx("card px-4 py-3.5", className)}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-[12.5px] font-medium text-ink-2">{label}</div>
        {icon && <Icon name={icon} className="text-ink-3" />}
      </div>
      <div className="mt-1 text-[22px] leading-tight font-semibold tracking-[-0.01em] num text-ink">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-ink-3">{hint}</div>}
    </div>
  );
}

export function EmptyState({ icon = "layers", title, description, action, compact }: {
  icon?: IconName; title: ReactNode; description?: ReactNode; action?: ReactNode; compact?: boolean;
}) {
  return (
    <div className={cx("flex flex-col items-center px-6 text-center", compact ? "py-8" : "py-14")}>
      <span className="mb-3 grid place-items-center w-11 h-11 rounded-xl border border-line bg-surface-2 text-ink-3">
        <Icon name={icon} size={20} />
      </span>
      <p className="text-sm font-semibold text-ink">{title}</p>
      {description && <p className="mt-1 max-w-sm text-[13px] text-ink-2">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

const CALLOUT: Record<"info" | "success" | "warning" | "danger", { icon: IconName; cls: string }> = {
  info: { icon: "info", cls: "bg-accent-soft border-accent/20 text-accent-ink" },
  success: { icon: "checkCircle", cls: "bg-success-soft border-success/25 text-success-ink" },
  warning: { icon: "alertTriangle", cls: "bg-warning-soft border-warning/25 text-warning-ink" },
  danger: { icon: "alertCircle", cls: "bg-danger-soft border-danger/25 text-danger-ink" },
};

/** A tinted message. Only the children are its text: role="alert" readers
 *  (and tests) get exactly the message, nothing decorative. */
export function Callout({ tone = "info", title, children, role, action, className }: {
  tone?: keyof typeof CALLOUT; title?: ReactNode; children?: ReactNode; role?: "alert" | "status";
  action?: ReactNode; className?: string;
}) {
  const style = CALLOUT[tone];
  return (
    <div className={cx("flex items-start gap-3 rounded-xl border px-4 py-3 text-[13px]", style.cls, className)}>
      <Icon name={style.icon} className="mt-[1px] shrink-0" />
      <div role={role} className="min-w-0 flex-1 whitespace-pre-wrap select-text">
        {title && <p className="font-semibold">{title}</p>}
        {title ? <div className="mt-0.5 opacity-90">{children}</div> : children}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

export function Segmented<T extends string>({ value, onChange, options, label, className }: {
  value: T; onChange: (value: T) => void; label: string; className?: string;
  options: { value: T; label: ReactNode; icon?: IconName; title?: string }[];
}) {
  return (
    <div role="group" aria-label={label}
      className={cx("inline-flex items-center gap-0.5 rounded-[10px] border border-line bg-surface-2 p-[3px]", className)}>
      {options.map(o => (
        <button
          key={o.value}
          type="button"
          title={o.title}
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
          className={cx(
            "inline-flex h-7 items-center gap-1.5 rounded-[7px] px-2.5 text-[12.5px] font-medium transition-colors",
            o.value === value ? "bg-surface text-ink shadow-[0_1px_2px_rgb(16_24_40/0.12)]" : "text-ink-2 hover:text-ink",
          )}
        >
          {o.icon && <Icon name={o.icon} size={14} />}
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Pagination({ page, totalPages, onPage, className }: {
  page: number; totalPages: number; onPage: (page: number) => void; className?: string;
}) {
  if (totalPages <= 1) return null;
  return (
    <div className={cx("flex items-center justify-between gap-3 px-4 py-2.5 text-[12.5px] text-ink-2", className)}>
      <span className="num">Page {page} of {totalPages}</span>
      <div className="flex gap-1.5">
        <Button size="sm" icon="chevronLeft" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button>
        <Button size="sm" iconRight="chevronRight" disabled={page >= totalPages} onClick={() => onPage(page + 1)}>Next</Button>
      </div>
    </div>
  );
}

/** Slide-over panel. Esc and the backdrop close it; focus moves into it.
 *  Rendered into <body>: an animated ancestor (any transform) would
 *  otherwise become the containing block and trap the fixed overlay. */
export function Drawer({ open, onClose, title, subtitle, actions, width = 480, children }: {
  open: boolean; onClose: () => void; title: ReactNode; subtitle?: ReactNode; actions?: ReactNode;
  width?: number; children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); previous?.focus?.(); };
  }, [open, onClose]);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-[rgb(10_12_18/0.38)] backdrop-blur-[2px] animate-fade" onClick={onClose} />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        tabIndex={-1}
        className="relative flex h-full flex-col border-l border-line bg-surface shadow-[var(--shadow-pop)] outline-none animate-drawer"
        style={{ width: `min(${width}px, 96vw)` }}
      >
        <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
            {subtitle && <p className="mt-0.5 text-[12.5px] text-ink-2">{subtitle}</p>}
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            {actions}
            <Button variant="ghost" size="sm" icon="x" aria-label="Close" onClick={onClose} />
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

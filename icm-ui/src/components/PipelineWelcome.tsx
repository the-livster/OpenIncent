import { Button, Callout, Icon } from "./ui";

interface Props { onSample: () => void; loading: boolean; error: string }

export default function PipelineWelcome({ onSample, loading, error }: Props) {
  return (
    <section
      aria-label="Getting started"
      className="card relative overflow-hidden animate-in"
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(60%_120%_at_0%_0%,var(--c-accent-soft),transparent_70%)]"
      />
      <div className="relative grid gap-6 p-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] lg:p-8">
        <div>
          <p className="eyebrow mb-2 text-accent-ink">Your first commission run</p>
          <h2 className="text-[24px] font-semibold leading-tight tracking-[-0.02em] text-ink">
            From sales data to clear, checked pay.
          </h2>
          <p className="mt-2 max-w-xl text-[14px] text-ink-2">
            Add your people, check their quotas and plans, then upload the period's sales. Review each payout, then send
            everyone an interactive statement that explains every line.
          </p>
          <ul className="mt-5 space-y-2 text-[13.5px] text-ink-2">
            {[
              "Every number traces to the rule and deal behind it",
              "Statements open in any browser, and still print",
              "Runs are saved and never silently recalculated",
            ].map(text => (
              <li key={text} className="flex items-center gap-2">
                <span className="grid h-5 w-5 place-items-center rounded-full bg-success-soft text-success-ink">
                  <Icon name="check" size={12} strokeWidth={2.6} />
                </span>
                {text}
              </li>
            ))}
          </ul>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
          <div className="rounded-xl border border-line bg-surface p-4 shadow-[var(--shadow-card)]">
            <div className="flex items-center gap-2">
              <Icon name="play" className="text-accent-ink" />
              <h3 className="text-sm font-semibold text-ink">Explore with sample data</h3>
            </div>
            <p className="mt-1.5 text-[13px] text-ink-2">
              Three fictional people, one 10% plan, and USD 3,500 in expected commissions. Sample runs are kept separate
              from your business data.
            </p>
            <Button variant="primary" className="mt-3" onClick={onSample} disabled={loading} loading={loading}>
              {loading ? "Loading sample..." : "Try sample data"}
            </Button>
          </div>
          <div className="rounded-xl border border-line bg-surface p-4 shadow-[var(--shadow-card)]">
            <div className="flex items-center gap-2">
              <Icon name="folderOpen" className="text-ink-2" />
              <h3 className="text-sm font-semibold text-ink">Start with your own files</h3>
            </div>
            <p className="mt-1.5 text-[13px] text-ink-2">
              Use these as templates. Match each person's plan_id to a plan, and each sale's payee_id to a person.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <a className="btn btn-secondary btn-sm" href="/sample/payees.csv" download><Icon name="download" />People CSV</a>
              <a className="btn btn-secondary btn-sm" href="/sample/transactions.csv" download><Icon name="download" />Sales CSV</a>
              <a className="btn btn-secondary btn-sm" href="/sample/openincent_sample.yaml" download><Icon name="download" />Plan YAML</a>
            </div>
          </div>
        </div>
      </div>
      {error && <div className="relative px-6 pb-6 lg:px-8"><Callout tone="danger" role="alert">{error}</Callout></div>}
    </section>
  );
}

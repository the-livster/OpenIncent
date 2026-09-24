interface Props { onSample: () => void; loading: boolean; error: string }

export default function PipelineWelcome({ onSample, loading, error }: Props) {
  return (
    <section className="rounded-xl border border-blue-200 bg-blue-50/60 p-6 space-y-4 mb-6" aria-label="Getting started">
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-blue-700 mb-2">Your first commission run</p>
        <h2 className="text-xl font-semibold text-zinc-900">From sales data to clear, checked pay.</h2>
        <p className="mt-2 text-sm text-zinc-600 max-w-2xl">Add your people, check their quotas and plans, then upload sales. Review each payout and download individual statements.</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="rounded-lg bg-white border border-blue-100 p-4 space-y-3">
          <h3 className="font-semibold text-sm">Explore with sample data</h3>
          <p className="text-sm text-zinc-600">Three fictional people, one 10% plan, and USD 3,500 in expected commissions. Sample runs are kept separate from your business data.</p>
          <button onClick={onSample} disabled={loading} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">{loading ? "Loading sample..." : "Try sample data"}</button>
        </div>
        <div className="rounded-lg bg-white border border-zinc-200 p-4 space-y-3">
          <h3 className="font-semibold text-sm">Start with your own files</h3>
          <p className="text-sm text-zinc-600">Use these examples as templates. Match each person's plan_id to a plan, and each sale's payee_id to a person.</p>
          <div className="flex gap-4 text-sm text-blue-700 underline">
            <a href="/sample/payees.csv" download>People CSV</a>
            <a href="/sample/transactions.csv" download>Sales CSV</a>
            <a href="/sample/openincent_sample.yaml" download>Plan YAML</a>
          </div>
        </div>
      </div>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </section>
  );
}

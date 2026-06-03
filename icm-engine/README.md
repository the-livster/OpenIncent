# OpenIncent

**Commission math you can actually see.**

OpenIncent is an open-source incentive-compensation engine. Give it your comp plan and your deal data; it returns every commission payout — with a full audit trail explaining how each number was produced. Self-host it, read the source, own your comp logic.

No black box. No per-seat SaaS. Your comp and payee data never leave your control.

**License:** AGPL-3.0-only

## Why OpenIncent

Most commission tools are closed SaaS: you upload sensitive pay data to someone else's cloud, pay per rep per month forever, and can't see how a number was calculated. OpenIncent inverts that:

- **Auditable** — every commission line traces back to the exact rule, transaction, and inputs that produced it, recorded in an immutable ledger. You can answer *"why did this rep get paid $X?"* down to the cent.
- **Deterministic** — the same inputs always produce the same commission numbers. Reproducible and testable.
- **Self-hosted** — runs locally or on your own infrastructure. Your data stays yours.
- **Open source (AGPL-3.0)** — read and audit every line. No lock-in, no per-seat fees.
- **Penny-precise** — all money uses `Decimal`. No floating-point drift.

## Who it's for

- RevOps, data, and finance engineers who want to **own their comp logic** instead of renting a black box.
- Teams with comp too custom for cheap tools, or who need **self-hosting and auditability** for privacy or compliance.
- Anyone tired of reconciling commissions in a spreadsheet they don't fully trust.

**Not for you if** you have a handful of reps on a flat percentage and you're happy in a spreadsheet — you won't feel the pain this solves.

## 60-second example

```bash
uv run icm \
  --plan examples/saas_ae_plan.yaml \
  --transactions examples/saas_transactions.csv \
  --payees examples/saas_payees.csv \
  --output ./output
```

Outputs:
- `commissions.xlsx` (or `.csv`) — every commission line, with the rule that produced it
- `summary.xlsx` — per-payee period totals
- `ledger.jsonl` — the full audit trail of every decision

## Define a plan in YAML

```yaml
plan_id: saas_ae
name: "SaaS AE Plan"
period_type: monthly
currency: USD
rules:
  - id: tiered_core
    type: tiered
    tiers:
      - threshold_pct: "1.0"    # up to 100% of quota
        rate: "0.05"
      - threshold_pct: "100.0"  # above 100%
        rate: "0.10"
  - id: enterprise_spif
    type: flat_rate
    rate: "0.02"
    filter: 'product == "Enterprise"'
```

Rule types today: **flat-rate**, **tiered** (boundary-crossing attainment), and **accelerator**. Filters support `==`, `!=`, `<`, `>`, `<=`, `>=`, and `in`, combined with `and` / `or`.

## The audit trail is the point

Every calculation emits ledger entries you can read:

```json
{"event_type": "commission_computed", "transaction_id": "D-1042", "payee_id": "P-014",
 "rule_id": "tiered_core", "inputs": {"amount": "7000", "rate": "0.10", "cumulative_pct": "1.07"},
 "outputs": {"commission_amount": "700.00"},
 "human_readable": "Tiered: 7000 @ 0.10 (at 107% of quota) = 700.00"}
```

No number appears in a payout that the ledger can't explain.

## Data in

- **CSV** and **Excel (.xlsx)** — with fuzzy column mapping, so `Rep Name`, `ACV ($)`, and `Close` map to the right fields automatically
- **Parquet** — feed warehouse exports (Snowflake, BigQuery, DuckDB) straight in

## Install

Requires Python 3.11+. Using [uv](https://docs.astral.sh/uv/):

```bash
uv sync                 # core engine + CLI
uv sync --extra all     # + Excel, Parquet, and AI features
```

Optional extras: `excel` (xlsx + fuzzy mapping), `parquet` (warehouse exports), `ai` (generate a plan from a plain-English description).

It also ships a local desktop app and an HTTP API (`icm serve`) for non-CLI workflows.

## Status

Early, pre-1.0. The calculation core is well-tested, but the plan format and API may still change. Use it, file issues, and tell us what your plans need — that's what shapes the roadmap.

## Roadmap

- Split & overlay crediting (one deal, multiple payees)
- Retroactive recompute & true-ups (clawbacks and adjustments)
- Per-rep statements (export / email) and an order-level "trace" view
- Ramp periods

## Develop

```bash
uv run pytest                   # tests
uv run ruff check src/ tests/   # lint
uv run mypy src/                # typecheck
```

## License

AGPL-3.0-only — self-host and modify freely; if you offer it as a network service, you must share your changes.

**Commercial licensing** — for embedding in a proprietary product or using it without AGPL obligations — is available. See [`LICENSING.md`](./LICENSING.md).

## Contributing

Contributions are welcome. We ask for a quick [Contributor License Agreement](./CONTRIBUTING.md) on your first PR — it keeps the project dual-licensable.

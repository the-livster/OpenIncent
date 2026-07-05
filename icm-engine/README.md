# OpenIncent

**Commission math you can actually see.**

OpenIncent is an open-source **incentive-compensation system**. Give it your comp plans, your payees, and your deal data; it computes every payout — crediting, quota attainment, draws, caps, bonuses, and adjustments — with a full audit trail explaining how each number was produced. Self-host it, read the source, own your comp logic.

No black box. No per-seat SaaS. Your comp and payee data never leave your control.

**License:** AGPL-3.0-only

## Why OpenIncent

Most commission tools are closed SaaS: you upload sensitive pay data to someone else's cloud, pay per rep per month forever, and can't see how a number was calculated. OpenIncent inverts that:

- **Auditable** — every commission line traces back to the exact rule, transaction, and inputs that produced it, recorded in an immutable ledger. You can answer *"why did this rep get paid $X?"* down to the cent.
- **Deterministic** — the same inputs always produce the same commission numbers. Reproducible and testable.
- **Self-hosted** — runs locally or on your own infrastructure. Your data stays yours.
- **Open source (AGPL-3.0)** — read and audit every line. No lock-in, no per-seat fees.
- **Penny-precise** — all money uses `Decimal`. No floating-point drift.
- **Complete** — not just a rate calculator: the whole comp workflow, from crediting through draws, caps, adjustments, and locked, audited statements.

## What it does

A run takes your **plans**, **payees**, and **deals** and produces audited payouts. It handles:

- **Rule types** — flat-rate, tiered (marginal / boundary-crossing attainment), and accelerator. Each rule can carry a **cap** and a **minimum-attainment gate**. When those can't express a plan, a **formula** rule evaluates a custom arithmetic expression per deal (safe DSL — variables like `amount`, `margin`, `attainment_pct`, any data column; `min`/`max`/`round`/`if(...)`).
- **Crediting** — splits (carve up a deal; must total 100%) and overlays (additive double-credit).
- **Quotas** — per-payee, with per-period overrides, **quota categories**, and new-hire **ramp** schedules.
- **Draws & guarantees** — recoverable draws (advances recovered from future earnings, balance carried across periods) and non-recoverable guarantees (a per-period floor).
- **Caps & thresholds** — per-rule caps, a per-payee/period plan payout cap, and minimum-attainment gates.
- **MBOs / bonuses** — non-commission period payouts (KPI bonuses, SPIFs).
- **Manual adjustments** — audited one-off corrections and discretionary amounts, each with a required reason.
- **Period locking, versioning & true-ups** — lock a closed period; late deals and clawbacks become delta true-ups attributed to the payout period, with origin tracking, while the locked statement stays intact.
- **Per-rep statements** — one privacy-isolated file per payee, in PDF, XLSX, or HTML, rounded to cents; brandable via `icm statements --theme theme.yaml` (company name, logo, accent colour, currency symbol, white-label switch).
- **Audit ledger & order trace** — every figure backed by a readable ledger entry; trace one deal through the whole plan.

Money is exact `Decimal` throughout, and every run is deterministic.

## 60-second example

```bash
uv run icm \
  --plan examples/saas_ae_plan.yaml \
  --transactions examples/saas_transactions.csv \
  --payees examples/saas_payees.csv \
  --output ./output
```

By default, results are written to the output directory **and** persisted to a local SQLite database (`%APPDATA%/OpenIncent/openincent.db` on Windows, `~/.openincent/openincent.db` otherwise). This enables versioning, period locking, and historical lookback. Use `--no-db` to skip database persistence and write files only.

Outputs:
- `commissions.xlsx` (or `.csv`) — every commission line, with the rule that produced it
- `summary.xlsx` — per-payee period totals
- `ledger.jsonl` — the full audit trail of every decision

Per-rep statements (`icm statements`) and natural-language plan drafting (`icm plan-from-text`) are separate commands.

For a complete, reconciled run — perm + contract desks, desk splits, a new-hire ramp, a manager override, and a reconciliation that surfaces underpaid and missed lines — see [`examples/staffing_reference/`](./examples/staffing_reference/).

## Define a plan in YAML

```yaml
plan_id: saas_ae
name: "SaaS AE Plan"
period_type: monthly   # monthly, quarterly, or annual
currency: USD
payout_cap: "20000"    # optional: max commission per payee per period
rules:
  - id: tiered_core
    type: tiered
    tiers:
      - threshold_pct: "1.0"    # up to 100% of quota
        rate: "0.05"
      - threshold_pct: "100.0"  # above 100% (sentinel for "the rest")
        rate: "0.10"
  - id: enterprise_spif
    type: flat_rate
    rate: "0.02"
    filter: 'product == "Enterprise"'
    cap: "5000"               # optional: cap this rule
    min_attainment_pct: "0.5" # optional: pays nothing until 50% of quota
```

Rule types: **flat-rate**, **tiered** (boundary-crossing attainment), **accelerator**, and **formula** (a custom per-deal expression, the escape hatch for everything else) — each optionally capped and/or gated by minimum attainment. Plans may also carry a per-period **payout cap** and a **draw**; payees may carry a draw, ramp, and quota categories.

Filters support `==`, `!=`, `<`, `>`, `<=`, `>=`, and `in`, combined with `and` / `or` — on **any input column** (canonical fields like `amount` and `product`, plus any extra/metadata column like `region` or `tier`). Values are auto-coerced: numeric strings compare as numbers, date strings as dates. Use backticks for field names with spaces: `` `Deal Type` == "Perm" ``.

## The audit trail is the point

Every calculation emits ledger entries you can read:

```json
{"event_type": "commission_computed", "transaction_id": "D-1042", "payee_id": "P-014",
 "rule_id": "tiered_core", "inputs": {"amount": "7000", "rate": "0.10", "cumulative_pct": "1.07"},
 "outputs": {"commission_amount": "700.00"},
 "human_readable": "Tiered: 7000 @ 0.10 (at 107% of quota) = 700.00"}
```

Caps, draws, MBOs, manual adjustments, and true-ups each emit their own ledger event and commission line — nothing silently mutates a rule's output, so the sum of lines always equals the payout. No number appears in a payout that the ledger can't explain.

## Period locks & versioning

Every calculation run is versioned per `(plan_id, period)`. When you close a period (e.g., March 2026), you **lock** it to pin the official calculation. Later corrections still work:

- **Late transactions** — upload a March deal in June. March attainment is recalculated, but the commission payout is attributed to June (the *effective period*). The March statement stays intact (locked v1); a new draft (v2) shows the updated attainment with a cross-reference to where the commission was paid.
- **Draft versions** — each recalculation creates a new version. Locks stay on the previously pinned version until you deliberately re-lock.
- **Origin tracking** — commission lines carry an `origin_period` field showing which period the deal actually closed in, distinct from the payout period.

Lock and unlock via the HTTP API or the CLI (`icm db` subcommands). Recalculation on locked periods is allowed by default; use `--no-allow-recalculate-locked` to enforce strict mode.

| Flag | Default | Purpose |
|------|---------|---------|
| `--no-db` | off | Skip database persistence (files only) |
| `--org ORG` | `default` | Multi-tenancy org ID |
| `--db-path PATH` | platform default | Custom database location |
| `--effective-period YYYY-MM` | current month | Payout period for late transactions |
| `--allow-recalculate-locked` / `--no-allow-recalculate-locked` | allowed | Whether locked periods can be recalculated |

> **Note:** period locking and true-ups are fully exercised for `monthly` plans. Quarterly/annual locking is not yet verified, and recoverable draws are not yet reconciled against locked-period true-ups — see [`docs/commission_logic.md`](./docs/commission_logic.md) for current limitations.

## Data in

- **CSV** and **Excel (.xlsx)** — with fuzzy column mapping, so `Rep Name`, `ACV ($)`, and `Close` map to the right fields automatically. Unrecognized columns are preserved as metadata and are filterable.
- **Parquet** — feed warehouse exports (Snowflake, BigQuery, DuckDB) straight in

## Interfaces

- **CLI** — `uv run icm …` (calculate, `statements`, `trace`, `reconcile`, `validate`, `check-plan`, `distribute`, `plan-from-text`, `db` subcommands)
- **HTTP API** — `icm serve`
- **Desktop app** — a local native window (with auto-update; see [`RELEASING.md`](./RELEASING.md))

## Install

Requires Python 3.11+. Using [uv](https://docs.astral.sh/uv/):

```bash
uv sync                 # core engine + CLI
uv sync --extra all     # + Excel, Parquet, PDF, and AI features
```

Optional extras: `excel` (xlsx + fuzzy mapping), `parquet` (warehouse exports), `pdf` (PDF statements), `ai` (generate a plan from a plain-English description).

## Status

Pre-1.0. The calculation core is well-tested (520+ tests, type-checked) and covers a full single- and multi-plan comp workflow. The plan format and API may still change. Use it, file issues, and tell us what your plans need — that's what shapes the roadmap.

## Roadmap

Shipped:
- ~~Split & overlay crediting~~ ✅ · ~~Retroactive recompute & true-ups~~ ✅ · ~~Ramp periods~~ ✅
- ~~Period locking & versioning~~ ✅ · ~~Per-rep statements + order trace~~ ✅
- ~~Draws & guarantees~~ ✅ · ~~Caps, thresholds & MBOs~~ ✅ · ~~Manual adjustments~~ ✅
- ~~Quota categories~~ ✅ · ~~Metadata-aware filters~~ ✅ · ~~Desktop auto-update~~ ✅
- ~~Multi-plan runs~~ ✅ — route each payee through their assigned plan in a single run
- ~~Manager hierarchy~~ ✅ — auto-generate upline overrides from `manager_id` + `manager_override`
- ~~Finance payout register~~ ✅ — rounded-to-cents XLSX auto-generated on period lock
- ~~Commission on gross profit (margin)~~ ✅ · ~~Plan assertions (`check-plan`)~~ ✅ · ~~Ingestion validation (`validate`)~~ ✅ · ~~Reconciliation (`reconcile`)~~ ✅

Next:
- **Plan effective-dating**, what-if modeling, and org-level reporting

See [`ROADMAP.md`](./ROADMAP.md) for the field-validated priorities and their current status.

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

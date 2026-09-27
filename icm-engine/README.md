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
- **Per-rep statements** — one privacy-isolated file per payee, rounded to cents: an interactive HTML page (each deal with how it was calculated, a pay breakdown that filters the deals, search, CSV download, print, dark mode — self-contained, no network requests, still readable with scripts off), a matching PDF, or XLSX. Brandable via `icm statements --theme theme.yaml` (company name, logo, accent colour, currency symbol, white-label switch).
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

Lock and unlock through the HTTP API (`POST` / `DELETE /v1/periods/{plan_id}/{period}/lock`), or ask the assistant to lock ([below](#ask-an-assistant-mcp)); the CLI has no lock command yet. A lock pins one calculation of that plan and period: naming a run from another plan or month is refused. A run that covers several plans saves each plan's month as its own calculation, so lock each plan that paid the month. Recalculation on locked periods is allowed by default; use `--no-allow-recalculate-locked` to enforce strict mode.

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

- **CLI** — `uv run icm …` (calculate, `statements`, `trace`, `reconcile`, `validate`, `check-plan`, `distribute`, `plan-from-text`, `mcp`, `access`, `db` subcommands)
- **HTTP API** — `icm serve`
- **MCP server** — `icm mcp` for AI assistants, and `icm mcp --http` with `icm access` to give each person their own ([below](#ask-an-assistant-mcp))
- **Desktop app** — a local native window (with auto-update; see [`RELEASING.md`](./RELEASING.md))

## Ask an assistant (MCP)

`icm mcp` connects OpenIncent to an AI assistant that speaks the Model Context Protocol, such as Claude Desktop, Claude Code or Cursor. Ask in plain English: *"What did Priya earn in May, and why?"*, *"Trace deal PL-1087"*, *"What would a £10,000 deal do to my May pay?"* The tools return figures already calculated and formatted, and the assistant is told to quote them rather than do its own arithmetic, so its answers match the statements.

```bash
uv sync --extra mcp
uv run icm mcp --print-config    # the entry for your client's config, and the `claude mcp add` line
```

This runs on your machine, for you, and reads the same database as the app (`--db PATH`, else `ICM_DB_PATH`, else the app's own). To give other people access, see [below](#give-each-person-their-own-access). What the assistant reads goes to the model behind it, so connect only an assistant you're allowed to share pay data with.

| Command | What it can do |
|---------|----------------|
| `icm mcp` | Answer about anyone; calculate a period, lock it, export statements, email them |
| `icm mcp --read-only` | Answer about anyone; change nothing |
| `icm mcp --payee P-101` | Answer only as that payee would see it, to check what they'll get |

Tools: `get_overview`, `find_payees`, `list_runs`, `get_run`, `get_pay`, `explain_pay`, `trace_deal`, `get_plan` and `simulate_deal` (a what-if deal, never saved). Admins also get `calculate_period`, `lock_period`, `export_statements` and `send_statements`.

**Every change is confirmed.** A pay-cycle tool first returns a preview and a `confirm_token`. Nothing changes until the assistant calls it again with that token, which it's told to do only after you agree. A token fits only its own preview: if the files or the saved results change in between, it's refused.

**Email** goes out over SMTP once `ICM_SMTP_HOST`, `ICM_SMTP_PORT`, `ICM_SMTP_USER`, `ICM_SMTP_PASS` and `ICM_SMTP_FROM` are set (`ICM_SMTP_TLS=0` connects over TLS from the start, as port 465 expects, instead of STARTTLS), the same settings as `icm distribute --smtp-from-env`. Without them, the assistant can write one `.eml` per person to a folder for you to send.

### Give each person their own access

Don't hand out the database: whoever can open that file can read everyone's pay. Instead, run the assistant as a small web service beside it and give each person their own access code. They sign in from their own chat app and see only what their access allows: a payee, their own pay and nobody else's; an administrator, everyone's. Nobody can change anything this way.

1. **Grant access.** Each command prints that person's code once. Send it to them privately.

   ```bash
   icm access grant P-101                          # Priya: their own pay only (a name works too)
   icm access grant --admin --label "Sam (finance)"  # everyone's pay, read-only
   ```

2. **Serve it** where their chat apps can reach it, over HTTPS:

   ```bash
   icm mcp --http --public-url https://comp.example.com
   ```

   It listens on `127.0.0.1:8765` (`--host`, `--port`). Put it behind anything that provides HTTPS at that address, such as Caddy, nginx or a Cloudflare tunnel. Sign-in refuses plain HTTP except on the same computer.

3. **Each person connects.** In Claude: Settings > Connectors > Add custom connector, with `https://comp.example.com/mcp`. A sign-in page opens; they paste their code, and that's it. In Claude Code: `claude mcp add --transport http openincent https://comp.example.com/mcp`, then `/mcp` to sign in. A client that sends headers can use the code directly: `Authorization: Bearer <code>`.

`icm access list` shows who has access and when they last used it. `icm access revoke <id>` ends someone's access at once, including every chat app they signed in with. Codes and tokens are stored only as hashes; a signed-in app holds a token that lasts an hour and renews itself while the person's access stands.

The pay cycle stays with `icm mcp` on your machine: its tools read and write files where they run, so the web service never offers them.

## Install

Requires Python 3.11+. Using [uv](https://docs.astral.sh/uv/):

```bash
uv sync                 # core engine + CLI
uv sync --extra all     # + Excel, Parquet, PDF, AI and MCP features
```

Optional extras: `excel` (xlsx + fuzzy mapping), `parquet` (warehouse exports), `pdf` (PDF statements), `ai` (generate a plan from a plain-English description), `mcp` (serve to an AI assistant).

## Status

Pre-1.0. The calculation core is well-tested (800+ tests, type-checked) and covers a full single- and multi-plan comp workflow. The plan format and API may still change. Use it, file issues, and tell us what your plans need — that's what shapes the roadmap.

## Roadmap

Shipped:
- ~~Split & overlay crediting~~ ✅ · ~~Retroactive recompute & true-ups~~ ✅ · ~~Ramp periods~~ ✅
- ~~Period locking & versioning~~ ✅ · ~~Per-rep statements + order trace~~ ✅
- ~~Draws & guarantees~~ ✅ · ~~Caps, thresholds & MBOs~~ ✅ · ~~Manual adjustments~~ ✅
- ~~Quota categories~~ ✅ · ~~Metadata-aware filters~~ ✅ · ~~Desktop auto-update~~ ✅
- ~~Multi-plan runs~~ ✅ — route each payee through their assigned plan in a single run
- ~~Manager hierarchy~~ ✅ — auto-generate upline overrides from `manager_id` + `manager_override`
- ~~Finance payout register~~ ✅ — rounded-to-cents XLSX auto-generated on period lock
- ~~Mid-year plan changes (dated versions)~~ ✅
- ~~Commission on gross profit (margin)~~ ✅ · ~~Plan assertions (`check-plan`)~~ ✅ · ~~Ingestion validation (`validate`)~~ ✅ · ~~Reconciliation (`reconcile`)~~ ✅
- ~~Assistant access over MCP~~ ✅ — ask about pay, trace deals, try a what-if deal, and run the pay cycle with a confirmation for each change; each person signs in to see their own pay

Next:
- Broader what-if modeling and org-level reporting

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

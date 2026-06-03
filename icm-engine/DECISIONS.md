# Design decisions

## Accelerator rule includes its own `rate`
The accelerator rule has a `rate` field in addition to `threshold_pct` and
`multiplier`. This makes it self-contained — it computes commission on the
above-threshold portion at `rate * multiplier` without needing to reference
another rule's rate.

## CLI uses `@app.callback()` not subcommands
The entry point `icm` takes options directly (`icm --plan ...`) rather than
`icm calculate --plan ...`. This avoids a typer subcommand resolution issue
with console_scripts on Windows. A `calculate` subcommand can be added later.

## Period is a standalone class, not a pydantic model
`Period` has ordering helpers and date boundaries. Making it a full pydantic
model would add overhead without benefit since it's always constructed from
validated YYYY-MM strings in model validators.

## Filter parser uses recursive descent, never eval()
The filter expression parser is a hand-written recursive descent parser that
produces an AST. No `eval()`, no `exec()`. This is safe and predictable.

## Tiers are inclusive on the upper bound
A transaction exactly at a tier boundary stays in the lower tier. The tier
boundary is crossed when cumulative attainment strictly exceeds the threshold.
This is the standard ICM convention.

## Ledger entries are emitted at every decision point
Every filter match/skip, tier crossing, and commission computation produces a
ledger entry. The property test ensures every Commission in the output has at
least one corresponding `commission_computed` ledger entry with the same
rule_id + transaction_id.

## Building on Windows
The `Makefile` targets can be run directly via `uv run` on Windows without make:
```bash
uv run ruff check src/ tests/   # lint
uv run mypy src/                # typecheck
uv run pytest tests/            # test
```

## AI plan generation uses Claude with 3-retry validation loop
`plan-from-text` calls the Anthropic API and validates the response against the
Pydantic schema. If validation fails, the error is fed back to the model as
context for a retry — up to 3 attempts. This corrective loop catches ~95% of
schema violations (missing quoted Decimals, wrong field names, etc.) without
human intervention.

## Fuzzy column mapping uses rapidfuzz with a 70% threshold
The column mapper uses a combination of `token_sort_ratio` and `partial_ratio`
from rapidfuzz to match messy Excel headers to canonical field names. The 70%
threshold was tuned against real-world sales data exports (Salesforce, HubSpot,
Google Sheets) where column names like "Rep Name", "ACV ($)", and "Close" need
to map to `payee_id`, `amount`, and `close_date`. Below 70% produces too many
false positives; above 80% misses common aliases.

## Excel header detection scans the first 10 rows
Real-world Excel exports often have merged title rows, subtitle rows, or
metadata before the actual headers. `_find_header_row` scores the first 10 rows
by text-cell count and uniqueness, favoring the row with the most unique text
values. This handles the common pattern of a merged "Q1 2026 Sales Report"
title row followed by the actual column headers.

## anthropic, openpyxl, rapidfuzz, and pyarrow are optional extras
The core engine only needs `pydantic`, `fastapi`, `typer`, `rich`, and `pyyaml`.
AI, Excel, and Parquet features are opt-in via `pip install icm-engine[ai]`,
`icm-engine[excel]`, or `icm-engine[parquet]`. The `[all]` extra installs
everything. This keeps the base install lightweight.

## AGPL-3.0 license
The engine is licensed under AGPL-3.0-only. This keeps it genuinely open source
(credibility, distro inclusion) while closing the SaaS free-rider loophole —
anyone offering the engine as a service must release their changes. Permissive
licenses (MIT/Apache) give away too much control; source-available licenses
(BSL/SSPL) cost the "open source" label. AGPL is the right tradeoff.

## Require CLA from all external contributors
A CLA (Contributor License Agreement) is required from the first outside
contribution onward. This preserves the ability to dual-license — offer the
engine under AGPL *and* sell commercial licenses — and to relicense if needed
later. Cheap to set up now; effectively impossible to retrofit once many
contributors have merged code under the original license alone.

## Parquet ingestion via pyarrow (optional extra)
Parquet support is opt-in via `pip install icm-engine[parquet]`. Uses pyarrow,
the most interoperable Parquet library (works with Snowflake COPY INTO,
BigQuery exports, DuckDB, Pandas). Column names must match canonical fields
exactly — no fuzzy mapping (unlike XLSX). This completes the "warehouse-first"
ingestion story: customers export from their warehouse as Parquet and feed it
directly to the engine.

---

# Business decisions

## Target market: SMBs, not enterprise
The initial target is businesses with 5–200 reps who have outgrown spreadsheets
but cannot afford Xactly or Varicent. Enterprise is a future goal, reached by
growing with SMB customers as they scale (the Wealthsimple model: start simple,
add adjacent products for the same customer base).

## Revenue model: free core + paid add-ons
- **Free**: local desktop app with full calculation engine, open source (AGPL)
- **Paid SaaS**: competitively priced cloud hosting (~$20–30/user/month)
- **Paid AI**: hosted chatbot/assistant, or BYOK (bring your own key)
- **Paid consulting**: custom plan modeling for clients who need it
- **Future**: plan template marketplace, statement portals

Rejected: SLA/accountability as a revenue stream. Partner firms can underprice
on insurance-like products because they can spread risk across a book of business.

## SaaS is an add-on, not the primary product
The desktop app is the free tier AND the trial for SaaS. Users who start local
and outgrow it upgrade to hosted without switching products. This creates a
natural upgrade funnel with zero customer acquisition cost for the SaaS tier.

## Competitive positioning
- **vs Commissionly**: same simplicity, but local data (privacy), open source
  (transparency), and more customizability
- **vs Xactly/Varicent**: 80% of the functionality at 20% of the price, faster
  implementation, no IT dependency
- **Unique moat**: local-only desktop app is genuinely undefended by any competitor

## AGPL-3.0 as competitive defense
AGPL requires anyone offering the engine as a network service to release their
modifications. This prevents partners like Canidium from forking and offering a
competing SaaS without contributing back. For enterprises that need a different
license, dual-licensing via CLA is available.

## API versioning from day one
All routes are prefixed `/v1/` to allow breaking changes in future versions
without breaking existing integrations. Enterprises have long-lived API consumers.

## Multi-tenancy in the data model from day one
Every database table has an `org_id` column, even though it defaults to
"default" for the single-tenant desktop app. This makes the transition to SaaS
multi-tenancy a configuration change, not a migration.

## Audit trail as a database entity, not just a file
Ledger entries are stored in SQLite alongside plans, payees, and settings.
This makes them queryable by date range, payee, and calculation — a requirement
for enterprise audit and dispute resolution workflows.

## Growth strategy: revenue-funded through mid-market
Stay revenue-funded through SMB and mid-market growth. Raise a Series A only
when the business has 20+ mid-market customers and proven unit economics —
specifically for compliance (SOC 2), security, and a sales team.

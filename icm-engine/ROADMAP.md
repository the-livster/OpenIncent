# Roadmap

OpenIncent's priorities are anchored in real failure modes of commission operations — the places
where a deployed system computes the wrong number (or refuses to compute at all) and a human quietly
patches it in a spreadsheet every month. The thesis: those fixes belong in auditable, tested code.

Status legend: ✅ shipped · 🟡 partial · ⬜ planned

## Recently shipped

The calculation core already models the common staffing and SaaS comp patterns natively, with a
stronger audit story than most closed tools:

- **Rule types** — flat-rate, **tiered (boundary-crossing)**, accelerator; each with optional cap,
  minimum-attainment gate, quota category, and an `amount` / `margin` base.
- **Commission on gross profit (margin)** — `base: margin` with `bill_rate`/`pay_rate`/`units` or a
  direct `margin` column, on flat **and** tiered **and** accelerator rules (contract/temp desks).
- **Crediting** — splits (sum to 100%) and overlays, including a `credits` column in CSV/XLSX
  (`P1:0.6;P2:0.4`, `60%/40%`, JSON, `@overlay`) for desk splits straight from a client file.
- **Manager hierarchy, team/shared quotas, ramps, draws (recoverable + guarantee), caps, MBOs,
  manual adjustments, period locking + delta true-ups, multi-currency display, configurable rounding.**
- **Per-rep statements** (XLSX / HTML / PDF) and an immutable audit ledger + `icm trace`.

## Field-validated priorities

### 1. Intra-deal threshold splitting ✅

When a rep at 80% of quota closes a deal that carries them past 100%, the portions below and above the
threshold must pay at their own rates. Several deployed commercial systems pay the whole deal at one
rate and rely on a human to adjust afterward. **Shipped:** `_calc_tiered` slices every credit across
attainment bands — each slice is its own commission line at its own rate, with `tier_crossed` events
in the ledger and the slice math rendered in plain English on the statement (see #6).

### 2. Dual credit channels — attainment vs payment 🟡

Real plans break the assumption that money and quota travel together: bookings that pay but retire
quota only up to a cap, SPIFs that pay but never retire quota, categories that earn partial credit.
**Partial:** a transaction's `quota_amount` already decouples *counts-toward-quota* from
*pays-commission* (set it to 0 for a SPIF-only deal, or below `amount` for capped quota retirement).
**Planned:** the fully general form — every *credit* carrying independent quota/payment channels, each
with its own caps.

### 3. Plan assertion fixtures ✅

A rate table transcribed slightly wrong can underpay by a small, invisible amount at exactly 100%
attainment. **Shipped:** a plan can declare executable invariants — `at 100% attainment, payout == OTE`;
`a deal crossing tier 1 splits as X/Y` — run through the real engine by `icm check-plan` on every plan
change. The annual plan rebuild goes from "pray" to "compile."

### 4. Underspecification as a validation error 🟡

Policy documents contain contradictions that surface at payroll time as disputes. A DSL can refuse to
run an ambiguous plan instead of silently defaulting. **Present:** strict validation (ascending tiers,
period formats, splits summing to 1.0), loud filter-compile errors. **Planned:** payee-level overrides
with provenance (a comp-letter term supersedes the plan default, and the override records its source);
sensitivity runs (execute a period under two readings of an ambiguous plan and diff the payouts — the
true-up diff machinery already does most of the comparison).

### 5. Ingestion validation layer ✅ (core)

The errors that corrupt a run are checks, not judgment. **Shipped** (`icm validate`): duplicate
detection (identical payee/period/amount, plus transaction IDs one character apart), per-transaction
eligibility guard (a booking dated after a payee's end date — earning after termination), and currency
consistency / FX-table completeness that fails loudly rather than silently mixing units. **Planned:**
payroll-handoff reconciliation (every payfile line maps to a valid payroll ID).

### 6. Rep-facing explanation statements ✅

Reps have no tolerance for not knowing exactly what they earned, and disputes end fast when the
walkthrough is pre-written. **Shipped:** each statement renders the slice math in plain English —
"40,000 of this deal fell between 100% and 125% of quota → 12%" — backed by the same audit ledger that
`icm trace` reads.

## On the radar (built on demand / on customer pull)

- YTD / cumulative attainment mode (tiers against fiscal-year-cumulative bookings).
- Plan effective-date versioning (Plan A Jan–Mar, Plan B from Apr) without manual partitioning.
- What-if / sensitivity modeling and org-level reporting.

## Deliberate non-goals (for now)

To stay simple and auditable, OpenIncent is **not** chasing: hosted multi-tenant SaaS, rep self-serve
portals, dashboards/leaderboards, approval workflows, CRM/HRIS/payroll integrations (CSV/XLSX/Parquet
in, plus one-click CRM export, already covers ingestion), or a no-code formula layer. The moat is a
calculation you can read and reproduce — not feature-parity with enterprise ICM.

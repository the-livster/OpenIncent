# The OpenIncent Method

**Version 0.1 — 2026-06-28**

A repeatable way of taking commission from a messy spreadsheet to **paid,
reconciled, and explainable** — and proving it worked at every step.

This document is the product. The [OpenIncent engine](icm-engine/) is free
software that *runs* the method for you — but the method is the thing that
matters. You can follow it by hand; the engine just makes it cheap and fast. Both
are free and open. What you can pay for is having someone run it for you
([services](https://openincent.com/services)).

> **Why a method and not just software?** Commission software is cheap to write
> and easy to clone. What is scarce is knowing *how to get a four-year-old
> spreadsheet into a shape you can pay people from without breaking payroll* — and
> how to prove, line by line, that you did. That knowledge is a process. It is
> written down here, named, and versioned. The engine enforces it; this document
> defines it.

---

## The problem this solves

Commission rarely breaks because the math is hard. It breaks because **nobody can
prove the number.**

- The plan lives in a PDF, an email thread, and one person's head — and that
  person is on holiday, or has left.
- The spreadsheet that actually pays people has silent errors no one will find
  until a rep does, in public, at the worst time.
- When a rep asks *"why was I paid this?"*, the honest answer is *"the sheet says
  so"* — which is not an answer, it is a dispute waiting to happen.
- Each year the plan changes and the whole fragile apparatus is rebuilt from
  scratch, on hope.

The cost of all this is not abstract. It is a churned rep who stopped trusting
their pay, a clawback that should have fired and didn't, an overpayment finance
ate, and a month-end that eats someone's evenings.

**The method's promise:** every payout reconciles to reality and explains itself,
the plan is versioned like code, and "is it right?" has a provable answer instead
of a shrug.

---

## Contents

1. [Principles](#1-principles) — the five rules everything follows from
2. [The method](#2-the-method) — the eight steps, messy → paid
3. [The conformance ladder](#3-the-conformance-ladder) — L0–L3, your proof it worked
4. [Why this beats the alternatives](#4-why-this-beats-the-alternatives)
5. [The control library](#5-the-control-library) — the named checks (reference)
6. [The data spec](#6-the-data-spec) — what each input must look like (reference)
7. [Versioning](#7-versioning-this-method)

---

## 1. Principles

Five rules everything else follows from.

1. **The plan is code, not prose.** A comp plan is an executable artifact with a
   defined schema — not a PDF, not an email thread, not knowledge in one person's
   head. If it cannot be expressed in the [plan spec](#61-the-plan), it is not yet
   a plan; it is an intention.

2. **Every number is traceable.** No payout may exist that the audit ledger cannot
   explain back to the exact rule and deal that produced it. "The system says so"
   is not an answer; "rule `tier_2` paid 8% on the slice from 100%–150% of quota"
   is.

3. **Money is exact.** All monetary values are arbitrary-precision decimals. No
   floating point, ever. Rounding happens only at the display boundary and never
   feeds back into a calculation ([CTRL-MONEY-01](#money--precision)).

4. **Ambiguity fails loudly.** Where the data is ambiguous or the plan is
   under-specified, the correct behaviour is to stop and report — not to guess.
   Silent defaults that change a payout are defects.

5. **Data conforms before it pays.** Transactions, payees, and the plan pass the
   [control library](#5-the-control-library) *before* a run is trusted. A run on
   unvalidated data is a draft, not a payout.

---

## 2. The method

Eight steps take a client from a spreadsheet to a conformant, reconciled, paid
run. Each step earns a rung on the [conformance ladder](#3-the-conformance-ladder)
— so at any moment you know exactly how far you've come and what's left to prove.

This is the public method *and* the working process: the same steps whether you
run it yourself with the free engine, or have someone run it for you. The `icm …`
commands name the engine step that does the work; do each by hand if you prefer.

### Step 1 — Intake

Collect what you already have: the plan (in whatever form it exists — a PDF, an
email, the spreadsheet itself), the last full period's deal extract, the payee
roster, and **a copy of what was actually paid** that period. That last one is the
target you will reconcile against; without it, you can compute a number but you
cannot prove it.

### Step 2 — Map to the spec → reaches **L0**

Map the source columns onto the [canonical fields](#6-the-data-spec). The engine
fuzzy-maps common headers (`icm map` previews the inferred mapping); review and
lock it. When all three inputs load, you are at **L0: it parses.**

### Step 3 — Validate the data → reaches **L1**

Run the [data controls](#5-the-control-library) (`icm validate`). Resolve every
**error**. Review every **warning** with the owner of the data — a flagged
near-duplicate or a deal credited after someone's leaving date is either a real
defect or a conscious exception, and you record which. When the data is clean or
every exception is signed off, you are at **L1: validated.**

### Step 4 — Encode the plan as code → reaches **L2**

Translate the plan into the [plan spec](#61-the-plan) — or start from a
[template](icm-engine/examples/templates/) for a common pattern (perm placement,
contract margin, SaaS AE, SDR pipeline) and change the rates to yours. Where the
prose is ambiguous, **do not guess** — get a written answer and encode that. Add
[assertions](#612-assertions): tiny "at 100% of quota this rep earns exactly their
OTE" scenarios that turn the annual plan rebuild from *pray* into *compile*. Run
`icm lint` (static health checks) and `icm check-plan` (runs each assertion
through the real engine) until both are clean. You are at **L2: plan-verified.**

> This is where institutional knowledge gets extracted from someone's head into a
> versioned artifact. It is the most valuable step and the one that cannot be
> automated.

### Step 5 — Calculate

Run the calculation (`icm calculate`). Produce per-rep statements
(`icm statements`) and the full audit ledger. Spot-check a handful of payouts with
`icm trace` to confirm each number explains itself ([Principle 2](#1-principles)).

### Step 6 — Reconcile → reaches **L3** (acceptance)

Diff the computed run against **what was actually paid** (`icm reconcile`).
Investigate every discrepancy:

- An **underpayment** the old process missed is a *win to surface*, not a bug to
  hide.
- An **overpayment** is a control the old process lacked.
- A **missing or unexpected line** is a mapping or eligibility gap to close.

Iterate Steps 4–6 until the diff is clean, or every remaining difference is
explained and signed off. When the run matches reality line for line, you are at
**L3: reconciled** — the highest rung, and the only honest place to go live.

### Step 7 — Lock & hand over

Lock the period, which pins the official run and generates the finance payout
register. Distribute statements (safe by default — nothing sends without an
explicit instruction). The client owns the plan, the config, and the data. There
is no lock-in: the plan is a text file and the engine is free.

### Step 8 — Run the cadence

Each subsequent period repeats Steps 2–7 on new data against the now-fixed plan.
Late deals flow through as period-locked true-ups with origin tracking, so a deal
that arrives two months late is paid correctly *and* traceably, without
rewriting history.

---

## 3. The conformance ladder

Conformance is a ladder, not a checkbox. Each level is the acceptance gate for the
next — you cannot honestly claim a rung until the one below it holds. **L3 is the
only level at which you should pay real people real money.**

| Level | Name | What it means | Proven by |
|---|---|---|---|
| **L0** | Parses | All three inputs load cleanly against the [data spec](#6-the-data-spec). | A calculation runs without a load error. |
| **L1** | Validated | No **error**-severity findings from the data controls; warnings reviewed and accepted. | `icm validate` clean. |
| **L2** | Plan-verified | The plan is structurally sound and every assertion produces its stated payout. | `icm lint` (no errors) + `icm check-plan` (all pass). |
| **L3** | Reconciled | The run matches the last real pay cycle, **line for line.** | `icm reconcile` clean ([CTRL-RECON-01](#reconciliation)). |

A run's conformance claim should cite the level and the method version it was
checked against — e.g. *"L3 conformant, OpenIncent Method v0.1."* It is a claim
anyone can re-check, because the inputs, the engine, and this document are all
open.

This is also the buyer's vocabulary. *"Does your commission process produce
L3-reconcilable output, with a ledger that traces every line?"* is a question you
can put to a spreadsheet, to a six-figure ICM platform, or to a vendor — and most
of them cannot answer yes.

---

## 4. Why this beats the alternatives

Two things pay commission today: a **spreadsheet**, or a **black-box ICM
platform** (Xactly, CaptivateIQ, Spiff, and the like). Both leave the same gap —
you cannot *prove* the number — and they leave it from opposite directions.

| | The spreadsheet | The black-box platform | **The OpenIncent Method** |
|---|---|---|---|
| **Can it explain a payout?** | Only by re-reading formulas by hand. | "The system calculated it." | **Yes — every line traces to its exact rule and deal.** |
| **Is the plan versioned?** | No — it *is* the spreadsheet. | Inside the vendor, hard to diff. | **Yes — plain text, in version control.** |
| **Does it self-check?** | No. Silent errors until a rep finds one. | Some validation, opaque. | **Yes — named controls + plan assertions before payday.** |
| **Is "is it right?" provable?** | No. | No — no reconciliation gate. | **Yes — L3 reconciles line-for-line to what you actually paid.** |
| **Who can run it?** | The one person who built it. | A full-time admin / the vendor. | **Anyone who follows the method.** |
| **Where does your data live?** | Your machine. | The vendor's cloud. | **Your machine. It never has to leave.** |
| **What does it cost?** | "Free" + hidden risk + evenings. | Per-seat, forever, climbing. | **The engine is free. You pay only for someone to run it, if you want that.** |
| **What happens when you stop?** | It rots. | You lose access and your history. | **You keep everything — text plan, open engine, your ledger.** |

The spreadsheet is cheap but unprovable and fragile: it holds the whole business
in formulas one person understands, and it fails silently. The platform is
powerful but rented: it needs a specialist to operate, bills per head forever,
keeps your pay data in someone else's cloud, and *still* leaves you doing manual
adjustments — with no way to prove the result against reality.

The method is the third option: **own the logic, read every line, and prove the
result.** The engine that runs it is free and auditable, so you are never trusting
a black box — you can read exactly how every number was reached, or have a real
person who owns the math run it and stand behind it. The defensible thing was
never the code. It is the discipline, written down — and the proof at the end.

---

## 5. The control library

A control is a named, machine-checkable property that conforming data must
satisfy. Each lists its **enforcement** — when the engine applies it. Codes are
stable so you can cite them.

Severity:
- **error** — data is not conforming; do not pay on it.
- **warning** — likely a defect; a human must confirm intent.
- **info** — a recommendation toward higher conformance.

### Money & precision

| Code | Property | Severity | Enforcement |
|---|---|---|---|
| **CTRL-MONEY-01** | All monetary values are exact decimals; no float. Rounding is display-only and never re-enters calc. | error | Engine-wide invariant (`Decimal` throughout; rounding applies at the output boundary only). |

### Plan structure

Checked by `icm lint` (static, no data) and by model validation at load time.

| Code | Property | Severity | Enforcement |
|---|---|---|---|
| **CTRL-PLAN-01** | Plan has at least one rule. | error | `icm lint` (`no_rules`). |
| **CTRL-PLAN-02** | Rule ids are unique within the plan. | error | `icm lint` (`duplicate_rule_id`). |
| **CTRL-PLAN-03** | `period_type` ∈ {monthly, quarterly, annual}; `pro_rating` ∈ {full, daily, zero}; periods match `YYYY-MM`. | error | Model validation (load fails otherwise). |
| **CTRL-PLAN-04** | Tier thresholds are strictly ascending. | error | Model validation (`TieredRule`). |
| **CTRL-PLAN-05** | No rule has rate 0 (a rule that pays nothing). | warning | `icm lint` (`zero_rate`, `zero_tier_rate`). |
| **CTRL-PLAN-06** | Tier marginal rates do not decrease (higher attainment shouldn't pay a lower marginal rate). | warning | `icm lint` (`decreasing_tier_rate`). |
| **CTRL-PLAN-07** | An accelerator has a companion base (flat/tiered) rule, or low attainment pays nothing. | warning | `icm lint` (`accelerator_no_base`). |
| **CTRL-PLAN-08** | `payout_cap` is not below stated `ote` (on-target reps would be capped). | warning | `icm lint` (`cap_below_ote`). |
| **CTRL-PLAN-09** | A min-attainment gate is not above 100% unless intended. | warning | `icm lint` (`gate_above_target`). |
| **CTRL-PLAN-10** | Plan declares assertions (so transcription errors are caught). | info | `icm lint` (`no_assertions`). |

### Plan correctness (assertions)

| Code | Property | Severity | Enforcement |
|---|---|---|---|
| **CTRL-ASSERT-01** | Every declared assertion's actual payout matches `expect_total` within `tolerance`. | error | `icm check-plan` (runs each scenario through the real engine). |
| **CTRL-ASSERT-02** | At 100% attainment, payout equals stated OTE (the canonical assertion). | error | `icm check-plan`, where declared. |

### Dedup & integrity

Checked by `icm validate`.

| Code | Property | Severity | Enforcement |
|---|---|---|---|
| **CTRL-DUP-01** | No two transactions share `(payee_id, period, amount)` (likely double-entry). | warning | `icm validate` (`duplicate`). |
| **CTRL-DUP-02** | No two transaction ids differ by a single character (planted/typo near-duplicate). | warning | `icm validate` (`duplicate`, Levenshtein ≤ 1). |
| **CTRL-CREDIT-01** | Split credits on a deal sum to exactly 1.0. | error | Model validation (load fails otherwise). |
| **CTRL-CREDIT-02** | Each credit share is in `(0, 1]`. | error | Model validation. |

### Eligibility

| Code | Property | Severity | Enforcement |
|---|---|---|---|
| **CTRL-ELIG-01** | No transaction is credited to a payee after their `effective_to` (earning after termination). | warning | `icm validate` (`ineligible`). |

> **The payroll-handoff control (planned).** Every line that reaches payroll must
> map to a valid, currently-employed payroll ID — the control that would have
> caught the dummy-payee incident behind this project's origin. The eligibility
> guard above is its first half; full payroll-handoff reconciliation is on the
> [roadmap](icm-engine/ROADMAP.md).

### FX & currency

| Code | Property | Severity | Enforcement |
|---|---|---|---|
| **CTRL-FX-01** | If `reporting_currency` differs from `currency`, an exchange rate exists for every currency involved. | error | `icm validate` (`missing_fx`). |

### Reconciliation

| Code | Property | Severity | Enforcement |
|---|---|---|---|
| **CTRL-RECON-01** | Every computed payout matches what was actually paid, for one full real pay cycle (no underpaid / overpaid / missing / unexpected lines). | error (acceptance gate) | `icm reconcile`. |

---

## 6. The data spec

Three inputs feed a commission run: the **plan**, the **payees**, and the
**transactions**. Optional side-inputs (manual adjustments, MBOs, FX rates) are
defined in [§6.4](#64-side-inputs).

Conventions:

- **Required** fields must be present and non-empty.
- **Type `decimal`** means an exact decimal number; no thousands separators, no
  currency symbols in the canonical form (the engine's tolerant readers strip
  them, but canonical data is clean).
- **Type `period`** means a calendar month as `YYYY-MM` (e.g. `2026-03`).
- **Type `date`** accepts `YYYY-MM-DD`, `YYYY/MM/DD`, or `MM/DD/YYYY`; canonical
  form is `YYYY-MM-DD` (ISO 8601).
- **Aliases** are alternative column headers the engine will fuzzy-map to the
  canonical field. They exist so messy files can be ingested; they are *not* an
  excuse to leave data messy once it is yours.

### 6.1 The plan

Authored as YAML. This is the canonical, human-authored artifact — keep it in
version control.

| Field | Type | Required | Notes |
|---|---|---|---|
| `plan_id` | string | ✓ | Stable identifier; payees reference it. |
| `name` | string | ✓ | Human-readable plan name. |
| `period_type` | `monthly` \| `quarterly` \| `annual` | ✓ | Attainment window. |
| `currency` | ISO 4217 code | ✓ | The currency deals are denominated in. |
| `reporting_currency` | ISO 4217 code | | Blank = report in `currency` (no conversion). If set and different, FX rates are required ([CTRL-FX-01](#fx--currency)). |
| `rules` | list of rule | ✓¹ | See [rule types](#611-rule-types). A plan with no rules pays nothing — flagged by [CTRL-PLAN-01](#plan-structure). |
| `payout_cap` | decimal ≥ 0 | | Per-payee, per-period ceiling on total payout. |
| `draw` | draw object | | Plan-level draw; a payee-level draw overrides it. |
| `pro_rating` | `full` \| `daily` \| `zero` | | How partial periods (mid-period hires/leavers) are pro-rated. Default `full`. |
| `rounding` | rounding object | | Display-boundary rounding. Default: exact (no rounding). |
| `ote` | decimal ≥ 0 | | Stated on-target earnings. Metadata, but powers [CTRL-PLAN-08](#plan-structure) and the canonical OTE assertion. |
| `assertions` | list of assertion | | Executable invariants — see [§6.1.2](#612-assertions). Strongly recommended. |

¹ Structurally optional so a skeleton plan loads, but a plan with no rules is a
defect ([CTRL-PLAN-01](#plan-structure)).

#### 6.1.1 Rule types

Every rule has an `id` (unique within the plan), an optional `filter` (a boolean
expression over transaction fields — e.g. `` product == "Enterprise" and `deal value` > 50000 ``),
an optional `cap` (decimal ≥ 0), an optional `min_attainment_pct` (gate below which
the rule pays nothing), an optional `quota_category`, and a `base` of `amount`
(default) or `margin` (commission on gross profit, for staffing/contract desks).

- **`flat_rate`** — pays `rate × base` on every matching deal.
  - `rate`: decimal ≥ 0.
- **`tiered`** — boundary-crossing marginal tiers. Each `tier` has a
  `threshold_pct` (> 0) and a `rate` (≥ 0); the slice of attainment between one
  threshold and the next pays that tier's rate. Tiers **must** be sorted ascending
  by `threshold_pct` ([CTRL-PLAN-04](#plan-structure)).
  - `tiers`: list of `{threshold_pct, rate}`.
- **`accelerator`** — pays `rate × multiplier × base` on deals once attainment
  passes `threshold_pct`.
  - `rate`: decimal ≥ 0; `threshold_pct`: decimal > 0; `multiplier`: decimal > 0.

#### 6.1.2 Assertions

An assertion is a tiny scenario plus the payout it must produce. It turns the
annual plan rebuild from "pray" into "compile."

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | ✓ | What the invariant means, in words. |
| `quota` | decimal > 0 | ✓ | Quota for the synthetic payee. |
| `deals` | list of decimal | ✓ | Deal values for one synthetic payee. |
| `expect_total` | decimal | ✓ | Total commission the scenario must produce. |
| `base` | `amount` \| `margin` | | Treat `deals` as amount or margin. |
| `period` | period | | Defaults to `2026-01`. |
| `tolerance` | decimal ≥ 0 | | Allowed difference. Default `0.01`. |

The **canonical OTE assertion**: deals summing to `quota` (100% attainment) must
produce `expect_total == ote`. Every plan that states an OTE should declare it.

### 6.2 Payees

CSV or XLSX (one row per payee; or one row per payee-period for time-varying
quotas). Canonical headers below; common aliases in parentheses.

| Field (aliases) | Type | Required | Notes |
|---|---|---|---|
| `id` | string | ✓ | Stable payee identifier. Transactions and credits reference it. |
| `name` | string | ✓ | Display name. |
| `quota` (`target`, `goal`) | decimal ≥ 0 | ✓ | Default quota. |
| `plan_id` (`plan`, `comp plan`) | string | ✓² | Which plan this payee runs. |
| `period` + `quota` | period + decimal | | Repeat-row form: per-period quota overrides (time-varying quotas). |
| `effective_from` (`start date`, `hire date`) | date | | Employment window start. |
| `effective_to` (`end date`, `through`) | date | | Employment window end. Drives [CTRL-ELIG-01](#eligibility). |
| `email` | string | | For statement distribution. |
| `ramp_months` + `ramp_schedule` | int + decimals | | New-hire quota relief. `ramp_schedule` is space-separated multipliers; its length must equal `ramp_months`. |
| `draw_amount` + `draw_recoverable` | decimal + bool | | Per-payee draw/guarantee. |
| `category_quotas` | JSON object | | Per-category quotas, e.g. `{"New":100000,"Renewal":50000}`. |
| `manager_id` (`reports to`, `supervisor`) | string | | Upline for hierarchy override credits. |
| `manager_override` (`override`, `manager %`) | decimal in [0,1] | | Override rate for this payee's manager. |
| `team_id` | string | | Shared/pooled quota grouping. |

² Defaults to the payee's own `id` if absent, so single-plan files load — but in
canonical data, set it explicitly.

### 6.3 Transactions

CSV, XLSX, or Parquet (one row per deal, or per deal-payee when credits are split
in the file). Parquet requires exact canonical column names (no fuzzy mapping).

| Field (aliases) | Type | Required | Notes |
|---|---|---|---|
| `id` (`transaction id`, `record id`) | string | | Unique row identifier. Auto-assigned (`T001`…) if absent — but a stable `id` makes [dedup](#dedup--integrity) and reconciliation meaningful. |
| `payee_id` (`rep`, `sales rep`, `owner`, `recruiter`) | string | ✓ | Who earns on this deal (unless overridden by `credits`). |
| `deal_id` (`opportunity`, `order id`) | string | | Business deal key. Defaults to `id`. |
| `period` | period | ✓³ | Attainment/payout period. |
| `close_date` (`booked date`, `won date`) | date | ✓³ | Used to derive `period` if absent; drives [CTRL-ELIG-01](#eligibility). |
| `amount` (`acv`, `value`, `deal size`, `revenue`) | decimal | ✓⁴ | Deal value. May be negative (refunds/cancellations). |
| `product` (`sku`, `solution`, `product line`) | string | | For `filter` targeting. |
| `bill_rate` / `pay_rate` / `units` (`hours`) | decimal | | Margin desks: gross profit = `(bill_rate − pay_rate) × units`. |
| `margin` (`gp`, `gross profit`, `spread`) | decimal | | Direct gross-profit override; takes priority over computed margin. |
| `credits` (`splits`, `deal split`) | spec string or JSON | | Per-deal split/overlay (see [§6.3.1](#631-the-credits-field)). |
| `quota_amount` (`quota credit`, `quota retired`) | decimal | | Amount this deal contributes to attainment, decoupled from what it pays. `0` = pays but doesn't retire quota (SPIFs); below `amount` = capped quota retirement. |
| *any other column* | passthrough | | Preserved as `metadata`, usable in `filter` expressions. |

³ A transaction must carry **either** `period` **or** `close_date`; the engine
derives one from the other. Carrying both, consistently, is canonical.

⁴ `amount` defaults to `0` for margin-only rows, but a deal with neither `amount`
nor margin inputs pays nothing.

#### 6.3.1 The `credits` field

Crediting answers "who gets what share of this deal." Two kinds:

- **split** — shares of the deal that must sum to exactly `1.0`
  ([CTRL-CREDIT-01](#dedup--integrity)). Example: a 360/180 desk split.
- **overlay** — additive credit on top (e.g. an overlay specialist or manager),
  not constrained to sum to 1.

Accepted forms in a file cell:

```
P1:0.6;P2:0.4              # compact: splits summing to 1.0
P1:60%;P2:40%             # percentages also accepted
P1:0.5;P2:0.5;P3:0.1@overlay   # @overlay suffix = overlay credit
[{"payee_id":"P1","split_pct":"0.6"},{"payee_id":"P2","split_pct":"0.4"}]   # JSON
```

Each `split_pct` must be in `(0, 1]`.

### 6.4 Side-inputs

| Input | Format | Required fields | Notes |
|---|---|---|---|
| **Manual adjustments** | CSV | `payee_id`, `period`, `amount`, `reason` | Applied last; not subject to caps or draws. `reason` is **required** — an adjustment with no reason is rejected. Amount may be negative (clawbacks). |
| **MBOs / bonuses** | CSV | `payee_id`, `period`, `amount` | Non-commission payout. Added before caps/draws. Does **not** affect attainment. |
| **FX rates** | map | currency → rate | Required when `reporting_currency` differs from `currency` ([CTRL-FX-01](#fx--currency)). |

---

## 7. Versioning this method

The method is versioned independently of the engine.

- **Patch** (0.1.x) — clarifications, new aliases, new controls that don't change
  existing conformance.
- **Minor** (0.x) — new fields or controls that may change what passes at a given
  conformance level; additive where possible.
- **Major** (x.0) — breaking changes to the data spec or conformance ladder.

The engine version that performed a check is recorded in the audit ledger, so a
conformance claim ("L3 conformant, OpenIncent Method v0.1") is always
reproducible.

Status of v0.1: **the spec and control library track the current engine; the
conformance ladder and the method are stable in shape.** Gaps explicitly named
above (full payroll-handoff reconciliation, non-monthly period verification) are
tracked in the [engine roadmap](icm-engine/ROADMAP.md).

---

*Part of [OpenIncent](README.md) · the engine is AGPL-3.0 · [openincent.com](https://openincent.com)*

# Commission Logic — Out-of-the-Box Specification

This is the authoritative reference for **how OpenIncent calculates commissions by default.**
Every behavior below is what the engine does today with no configuration beyond a plan, a set of
transactions, and a set of payees. Where a behavior is a deliberate default that is intended to become
configurable, it is marked **▶ Knob** with the proposed setting.

The guiding principle: **no number appears in a payout that the audit ledger cannot explain.** Read this
document alongside a `ledger.jsonl` and every figure should be traceable.

---

## 1. Principles

- **Deterministic.** The same inputs always produce the same outputs. No clocks, no randomness inside the
  calculation (the only "now" is the late-transaction payout period, which is an explicit input).
- **Exact decimal.** All money is `Decimal`. There is no binary floating point and therefore no drift.
  (Note: "exact" is *not* the same as "rounded to cents" — see [§10 Money & rounding](#10-money--rounding).)
- **Attainment resets each window.** Quota attainment is measured per payee per period window and does not
  carry across windows by default. ▶ **Knob:** cumulative / year-to-date modes (see [§3](#3-periods--windows)).
- **Rules stack.** Every rule in a plan is evaluated independently; a payee's payout is the sum of all
  rules that fire for them.

---

## 2. The calculation pipeline

A single `calculate()` runs these stages in order:

1. **Load & validate** the plan, transactions, and payees. Invalid input fails loudly with the offending
   row/field — it is never silently coerced.
2. **Resolve credits** — expand each transaction into one or more *credit units* (who is credited, how
   much, and how). See [§4](#4-crediting-splits--overlays).
3. **Compute attainment** — sum credited bookings per (payee, window) and compare to quota. See
   [§6](#6-attainment).
4. **Evaluate each rule, in plan order**, over the credit-expanded transactions. Each rule emits commission
   lines and ledger entries. Rules with `min_attainment_pct` gates skip payees below threshold.
   Per-rule `cap` is applied after each rule. See [§7](#7-rule-types).
5. **Add MBOs / bonuses** — any `MBO` (a non-commission period payout) is added on top of rule output. MBOs
   do not affect attainment.
6. **Apply plan payout cap** — if `Plan.payout_cap` is set, per-payee totals are capped. See [§9.1](#91-caps--threshold-gates).
7. **Resolve draw** — if a draw (guarantee) is configured, the post-cap total is compared against the draw
   amount. Non-recoverable draws top up; recoverable draws thread a balance across periods. See [§9.2](#92-draws--guarantees).
8. **Apply locked-period adjustments** (only if some transaction periods are locked) — convert locked-period
   changes into delta true-ups attributed to the payout period. See [§8](#8-locking-versioning--payout-adjustments).
9. **Apply manual adjustments** — each `ManualAdjustment` is added last, not subject to caps, draws, or the
   locked-period diff. See [§9.3](#93-manual-adjustments).
10. **Return** the commission lines, the attainment summary, draw balances, and the full ledger.

Output `Commission.period` is always the **window key** (e.g. `2026-Q1`), not the raw transaction month.

---

## 3. Periods & windows

Every transaction has a `period` in `YYYY-MM` form (derived from `close_date` if not given). The plan's
`period_type` maps that month to the **window** that attainment and grouping use:

| `period_type` | `2026-02` becomes | Meaning                          |
|---------------|-------------------|----------------------------------|
| `monthly`     | `2026-02`         | each calendar month is a window  |
| `quarterly`   | `2026-Q1`         | Jan–Mar, Apr–Jun, Jul–Sep, Oct–Dec |
| `annual`      | `2026`            | the whole year is one window     |

Attainment **resets at the start of each window** — a payee starts each window at 0% of quota.

▶ **Knob:** this mapping is the single seam for future windowing modes (cumulative, rolling, YTD, custom
fiscal calendars). Today only reset-each-window is implemented.

> **Current limitation:** period **locking and true-ups are only fully exercised for `monthly` plans.**
> For quarterly/annual plans the caller compares raw `YYYY-MM` transaction periods against window-key lock
> records, so a lock may not be detected. Treat non-monthly locking as unverified until covered by tests.

---

## 4. Crediting (splits & overlays)

Before any rule runs, each transaction is expanded into **credit units**. A credit unit is one payee's
share of one deal.

**Default (no `credits` on the transaction):** the whole deal is credited to the transaction's `payee_id`
at **100%**, `kind = split`.

A transaction may instead carry an explicit `credits` list, each entry being:

- `payee_id` — who is credited
- `split_pct` — their share, `0 < pct ≤ 1`
- `kind` — `split` or `overlay`

Rules:

- **Splits must sum to exactly 100%** across a deal (validated; a 60/40 that totals 0.9 is rejected). A
  split *carves up* the deal — credited amount = `deal_amount × split_pct`.
- **Overlays are additive and unconstrained.** An overlay (e.g. a sales engineer or a manager who gets
  credit on top of the closer) does **not** count toward the 100% split total and can push total credited
  amount above the deal value. Use overlays for "double credit," splits for "divide the credit."

Everything downstream — attainment *and* every rule — operates on the **credited amount**, not the raw
deal amount. A 60% split on a $10,000 deal contributes $6,000 to that payee's attainment and is the base
for their commission.

Ledger event: `credit_allocated` (one per credit unit).

---

## 5. Quotas & ramps

Each payee has a base `quota` and may have:

- **Per-window quota overrides** (`quotas`, a map of window-key → quota). `quota_for(window)` returns the
  override for that window if present, otherwise the base quota.
- **A ramp schedule** (`ramp`) for new-hire quota relief.

**Ramp semantics:** `schedule[i]` is the quota multiplier for the *(i+1)-th month on the job*, counted from
`effective_from`. The resolved quota is `base_quota × multiplier`.

- Reference month per window type: monthly → that month; **quarterly → the last month of the quarter**
  (Q2 → June); annual → December.
- A window **before** `effective_from` resolves to a **quota of 0** (see zero-quota handling per rule).
- After the schedule is exhausted, the **full quota** applies (multiplier = 1).

Example: `effective_from = 2026-01-01`, ramp `[0.25, 0.5, 0.75]`, base quota $100k →
Jan quota $25k, Feb $50k, Mar $75k, Apr onward $100k.

> Caveat: if a payee has a ramp but **no** `effective_from`, it defaults to *today*, which can make past
> windows resolve to 0. Always set `effective_from` for ramped payees.

---

## 6. Attainment

For each (payee, window), **bookings = the sum of all credited amounts** in that window. Attainment % =
`bookings ÷ quota_for(window)`.

- **By default, every credited booking counts** toward attainment. There is no "new-business only" or
  quota-category filtering yet. ▶ **Knob:** restrict which deals count toward quota (e.g. by product/type).
- If quota is 0, attainment % is reported as **N/A** (undefined), and rules apply their zero-quota behavior
  ([§7](#7-rule-types)).

Ledger event: `attainment_computed` (one per payee/window).

---

## 7. Rule types

All rules support an optional `filter` ([§7.4](#74-filters)). Rules are evaluated in the order they appear
in the plan, and a payee's total is the **sum of every rule that fires**. Within `tiered` and `accelerator`
rules, a payee's deals in a window are processed **sorted by `close_date`** (undated deals last, stable
order). Per-line attribution depends on that order; **the per-payee total does not** — tiered and
accelerator totals are order-independent.

### 7.1 Flat rate

`commission = rate × credited_amount`, per credited transaction that passes the filter. Quota is not
involved. Use it for fixed-percentage commissions and product SPIFs.

Ledger events: `commission_computed`; `rule_skipped` for filtered-out deals.

### 7.2 Tiered (boundary-crossing, marginal)

A payee's bookings climb through attainment tiers; **each slice of bookings is paid at the rate of the tier
it falls in** (a marginal/boundary-crossing model, like tax brackets — not a single retroactive rate).

`threshold_pct` is a **fraction of quota where `1.0` = 100%.** A tier's rate applies from the previous
tier's threshold up to its own.

> **⚠ Convention foot-gun:** because `1.0` means 100%, a "top" tier is conventionally written as a large
> number like `threshold_pct: 100.0` — which is literally **10,000% of quota**, used as a
> catch-everything-above sentinel. It does *not* mean "100%." ▶ **Knob / cleanup candidate:** switch to an
> unambiguous convention (e.g. percent integers, or an explicit open-ended top tier).

**Worked example** — quota $10,000, tiers `[1.0 → 5%, 1.5 → 8%, 100.0 → 12%]`, a single $12,000 deal:

```
0%→100%  : $10,000 @ 5%  = $500.00   (fills the first tier)
100%→150%: $ 2,000 @ 8%  = $160.00   (remainder lands in the second tier)
                          ---------
total on $12,000          = $660.00
```

- **Zero quota:** the **top tier rate is applied to everything** (you can't measure attainment with no
  quota, so the engine pays the highest defined rate rather than nothing). ▶ **Knob.**

Ledger events: `rule_evaluated` (per payee/window), `tier_crossed` (when a boundary is crossed),
`commission_computed` (per slice), `rule_skipped` (filtered, or payee not found).

### 7.3 Accelerator

Pays an accelerated rate **only on bookings above a threshold attainment** — it does *not* pay anything on
the portion below. `threshold_amount = threshold_pct × quota` (same `1.0` = 100% convention).

`commission = amount_above_threshold × rate × multiplier`.

An accelerator is meant to **stack on top of a base rule** (flat or tiered). The canonical pattern (see
`examples/accelerator_plan.yaml`) is `flat_rate 5%` + `accelerator 5% × 1.5 above 100%`.

**Worked example** — quota $10,000, base flat 5%, accelerator 5% × 1.5 above 100%, a single $12,000 deal:

```
base flat   : $12,000 @ 5%          = $600.00
accelerator : $ 2,000 @ 5% × 1.5    = $150.00   (only the $2k above 100% quota)
                                     ---------
total                                = $750.00
```

- **Zero quota:** the accelerator is **skipped** (`rule_skipped`, reason `zero_quota`) — the opposite of
  tiered's zero-quota behavior. ▶ **Knob:** make zero-quota behavior consistent/configurable across rules.

Ledger events: `commission_computed`; `rule_skipped` (filtered, payee not found, or zero quota).

### 7.4 Filters

A rule with no filter applies to all transactions. A filter is a boolean expression over transaction
fields:

- **Operators:** `==`, `!=`, `<`, `>`, `<=`, `>=`, `in [ … ]`, combined with `and` / `or` and parentheses.
  `and` binds tighter than `or`.
- **Fields** are **any input column.** Canonical fields (`amount`, `product`, `period`, `payee_id`,
  `close_date`, `id`, `deal_id`) are resolved directly. Any other column name is resolved from the
  transaction's `metadata` dict (extra/unrecognized CSV columns). If a field is neither canonical nor
  in metadata, its value is `None`.
- **Type coercion.** Metadata values arrive as strings, but the evaluator coerces intelligently:
  - **Numeric:** if both sides parse as `Decimal`, comparison is numeric (so `tier > 2` works on a
    metadata column containing `"3"`).
  - **Date:** if both sides parse as dates (YYYY-MM-DD, YYYY/MM/DD, MM/DD/YYYY), the comparison is
    chronological (so `close_date >= "2026-04-01"` works).
  - **String:** if neither numeric nor date coercion applies, `==` / `!=` compare as strings. Ordering
    operators (`<`, `>`, `<=`, `>=`) on strings return `False`.
- **Missing-field rule:** If a referenced field is neither canonical nor in the row's metadata, its value
  is `None`, and **every** comparison against it returns `False` (including `!=`). A row never matches
  on a field it does not have. No exceptions are raised at eval time.
- **Backtick-quoting:** Field names containing spaces or special characters can be quoted with backticks:
  `` `Deal Type` == "Perm" ``.

Examples: `product == "Enterprise"`, `amount >= 50000`,
`product in ["Pro", "Enterprise"] and amount > 1000`,
`region == "EMEA"` (metadata), `` `GP %` >= 0.3 `` (metadata with backtick quoting),
`close_date >= "2026-04-01"` (date comparison).

---

## 8. Locking, versioning & payout adjustments

**Versioning.** Every calculation run is versioned per `(plan_id, period)`. Re-running creates a new draft
version; nothing is overwritten.

**Locking.** Closing a period **locks** it to one official calculation (the pinned version). A lock holds
until you deliberately unlock; re-running a locked period creates a new *draft* without disturbing the lock.

**Recalculation of a locked period (true-ups).** When a new run includes transactions whose window is
locked, the engine does **not** rewrite the locked statement. Instead:

1. Non-locked-period commissions pass through at their **full amount**, in their own period.
2. For each locked period, the engine **diffs the new result against the prior official commissions** and
   emits **true-up lines** equal to the *delta*, attributed to the **payout period** (`effective_period`,
   defaulting to the current month). Each true-up line carries `origin_period` = the locked window the deal
   actually belongs to.

This is what makes the headline scenarios correct:

- **Late deal** — a March deal uploaded in June: March's locked statement is untouched; June receives a
  true-up for exactly the *additional* commission the late deal creates (including any attainment shift it
  causes for other March deals), tagged `origin_period = 2026-03`.
- **Clawback** — a previously-paid deal removed: a **negative** true-up in the payout period.
- **No change** — identical recompute: **no true-up lines** (delta is zero).

Only locked-period deltas become true-ups; open-period deals are never double-counted. (This was a critical
fix — a regression test for the mixed locked+open scenario lives in `tests/test_periods.py`.)

**Strict mode.** Recalculating a locked period is allowed by default (it produces drafts + true-ups). Pass
`allow_recalculate_locked = false` to **reject** any run that touches a locked period instead (HTTP 409 /
CLI error). ▶ This is already a knob; consider which default a given client wants.

Ledger event: `true_up` (one per delta), recording prior amount, new amount, delta, origin and payout
periods.

---

## 9. Rule stacking & order of operations

- Rules are evaluated **in plan order**; all matching rules contribute. A payee's payout is the **sum** of
  every line from every rule.
- A typical plan layers a **base** rule (flat or tiered) with **add-ons** (accelerator, product SPIF). See
  `examples/saas_ae_plan.yaml`: tiered core + a `product == "Enterprise"` flat SPIF.

### 9.1 Caps & threshold gates

**Per-rule cap.** Any rule (`flat_rate`, `tiered`, `accelerator`) may carry an optional `cap: Decimal`.
After the rule evaluates, if the sum of its commission lines for a given `(payee, period)` exceeds the cap,
a negative `cap_adjustment` line is emitted (rule_id=`<rule>.id`) that brings the total down to the cap.
The cap does NOT affect attainment and does NOT interact with other rules — it limits only that rule's
output.

**Plan payout cap.** `Plan.payout_cap: Decimal` (optional) is a per-payee, per-period maximum. It is
applied **after** all rules have run and any per-rule caps have been applied. If the sum exceeds the cap,
a negative line (rule_id=`payout_cap`) reduces the total. Emits `cap_applied` ledger events.

**Threshold gate.** Each rule may also carry `min_attainment_pct: Decimal`. If a payee's attainment %
(bookings / quota, for the window) is below this threshold, the rule pays **zero** for that payee. A
`rule_skipped` ledger event is emitted with reason `below_threshold_gate`. The gate is checked before the
rule evaluates its transactions.

Convention: `min_attainment_pct` uses the same fraction convention as tier thresholds — `1.0` = 100%.

### 9.2 Draws / guarantees

A draw (also called a guarantee or floor) ensures a minimum payout per payee per period.

**Plan-level vs payee-level.** `Plan.draw` sets a default; `Payee.draw` overrides it per payee.

**Non-recoverable** (`Draw.recoverable=False`): a simple floor. If `earned < draw.amount`, a positive
`draw_topup` line makes up the difference. No balance is tracked.

**Recoverable** (`Draw.recoverable=True`): an advance that is recovered from future earnings above the
draw. The engine threads a balance across periods via `prior_draw_balances` / `draw_balances`:

```
available_to_recover = max(0, earned - draw)
recovered            = min(balance, available_to_recover)
payout               = max(earned - recovered, draw)
new_shortfall        = max(0, draw - earned)
new_balance          = balance - recovered + new_shortfall
```

- If `earned < draw`: emits `draw_topup` (positive).
- If `recovered > 0`: emits `draw_recovery` (negative).
- Emits `draw` ledger events with earned, draw, recovered, prior_balance, new_balance.

**Worked example** (draw 3000, recoverable):
| Period | Earned | Balance in | Recovery | Top-up | Payout | Balance out |
|--------|--------|-----------|----------|--------|--------|------------|
| P1     | 1000   | 0         | 0        | 2000   | 3000   | 2000       |
| P2     | 5000   | 2000      | 2000     | 0      | 3000   | 0          |
| P3     | 5000   | 0         | 0        | 0      | 5000   | 0          |

**⚠ Known limitation:** The recoverable-draw balance threading is implemented on the standard (non-locked)
calculation path only. Recalculation of locked periods with draw balances is not yet supported — the draw
logic runs before true-ups and does not interact with locked-period delta emission. Contact the maintainer
if this is a requirement.

### 9.3 Manual adjustments

`ManualAdjustment` objects represent a manual override — e.g., a discretionary bonus, a one-off clawback,
or a correction. Each adjustment is added as a Commission line (rule_id=`manual_adjustment`) with a
ledger event.

Adjustments are **not subject to caps or draws.** They are added after all other processing, so their
amount passes through to the final payout unchanged. Amount may be negative. Reason is required.

Adjustments do not pass through rules and do not affect attainment.

---

## 10. Money & rounding

- All amounts are `Decimal`; **negative amounts are allowed** end-to-end (refunds, cancellations,
  clawbacks).
- **No rounding is applied.** A commission is the exact product of its inputs: `0.05 × 1291.90 = 64.595`
  is stored and reported as `64.595`, **not** `$64.60`. There is currently no quantization to a currency's
  minor unit.
  ▶ **Knob (recommended default):** round each commission line **half-up to the currency's minor unit**
  (2 dp for USD). Decide line-level vs total-level rounding. **Set this before producing client-facing
  statements** — ragged decimals undermine the "penny-precise" promise on screen.
- `currency` is a label only; there is **no multi-currency conversion**.

---

## 11. Determinism & ordering

- Transactions within a tiered/accelerator group are sorted by `close_date`; **undated transactions sort
  last** in a stable order.
- All grouping iterates in sorted key order, so ledger output is reproducible run to run.

---

## 12. The audit ledger — event catalogue

Every figure is backed by one or more of these events (`ledger.jsonl`):

| `event_type`          | Emitted when…                                              |
|-----------------------|------------------------------------------------------------|
| `attainment_computed` | per (payee, window): bookings vs quota                     |
| `credit_allocated`    | per credit unit: who got what share, split or overlay      |
| `rule_evaluated`      | a tiered/accelerator rule begins for a payee/window        |
| `rule_skipped`        | a deal is excluded (filter, payee-not-found, or zero-quota) |
| `tier_crossed`        | cumulative attainment crosses a tier boundary              |
| `commission_computed` | a commission slice is produced (the core "why $X" record)  |
| `true_up`             | a locked-period delta is paid into the payout period       |

---

## 13. Defaults → customisation map

The roadmap for "everything customisable, sensible defaults." Each row is a current default and the knob it
should become.

| Area                    | Default today                                  | Proposed knob                                        |
|-------------------------|------------------------------------------------|------------------------------------------------------|
| Crediting               | 100% to deal's payee, `kind=split`             | default credit rule; split/overlay templates         |
| Attainment basis        | all credited bookings count                    | quota categories (which deals count)                 |
| Windowing               | reset each month/quarter/year                  | cumulative / YTD / rolling / fiscal calendar         |
| Tier threshold units    | fraction (`1.0` = 100%), `100.0` = sentinel    | unambiguous percent + explicit open-ended top tier   |
| Tier model              | marginal / boundary-crossing                   | optional retroactive (single-rate) tiers             |
| Zero quota              | tiered → top tier; accelerator → skip          | one consistent, configurable policy                  |
| Accelerator base        | pays only above threshold                      | optional built-in base rate                          |
| Rounding                | none (full precision)                          | half-up to currency minor unit (line or total)       |
| Caps/floors/draws       | none                                           | per-payee/period caps, floors, recoverable draws     |
| Filters                 | numeric ordering only; no metadata             | ✅ metadata-aware filters with type coercion; date comparisons; backtick-quoted field names |
| Locked recompute        | allowed (drafts + true-ups)                    | per-client strict vs. permissive default             |
| Late-deal payout period | current month                                  | configurable attribution policy                      |

---

## 14. Known limitations (be honest)

- Non-monthly **locking** is not fully verified ([§3](#3-periods--windows)).
- **No rounding** by default ([§10](#10-money--rounding)).
- No caps/floors/draws/guarantees/MBOs/multi-currency yet.
- Recoverable draws do not interact with locked-period true-up recalculation ([§9.2](#92-draws--guarantees)).
- The `100.0`-as-top-tier convention is easy to misread ([§7.2](#72-tiered-boundary-crossing-marginal)).
- MBOs, multi-currency, and per-category quota attainment remain future work.

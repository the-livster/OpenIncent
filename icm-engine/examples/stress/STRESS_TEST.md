# Plan DSL Expressiveness Stress Test

Tests run 2026-06-04 against `icm-engine` at HEAD (260 tests, lint clean, mypy clean).

## Test plans

### 1. Permanent placement fee — flat rate
**Real-world encoding:** Staffing agency permanent-placement commission: 10% of the placement fee.
**Plan:** `plans/01_perm_placement.yaml` — single `flat_rate` at 10%.
**Data:** Two placements ($50K, $80K) in June 2026 for one payee.
**Result:** $13,000 total ($5K + $8K). ✅ Intended.

### 2. Contract margin commission — flat % of GP
**Real-world encoding:** Contract/temp recruiter commission: 20% of gross profit per contractor.
**Plan:** `plans/02_contract_margin.yaml` — single `flat_rate` at 20%.
**Data:** Two contractors producing $15K and $22K GP in June.
**Result:** $7,400 total ($3K + $4.4K). ✅ Intended.

**⚠️ Gap:** The plan works only if GP is pre-calculated and placed in the `amount` field. The engine cannot compute GP from separate bill-rate and pay-rate fields. See Expressiveness Gaps below.

### 3. Desk split — 180 model (credits/splits)
**Real-world encoding:** A $50K placement is credited 60% to the sales recruiter and 40% to the delivery recruiter. Both earn 10% of their credited share.
**Plan:** `plans/03_desk_split.yaml` — single `flat_rate` at 10%. Credits set programmatically on the Transaction.
**Data:** One $50K transaction with split credits [P1: 0.60, P2: 0.40].
**Result:** P1=$3,000 (60% × $50K × 10%), P2=$2,000 (40% × $50K × 10%). Grand total $5,000. ✅ Intended.

**⚠️ Note:** Credits cannot be expressed in CSV — they require programmatic Transaction construction. The `credits` field is a Pydantic `list[Credit]` on the Transaction model and is not recognized by the CSV loader.

### 4. New-recruiter ramp — tiered with quota relief
**Real-world encoding:** A new recruiter starts January 2026 with a 3-month ramp: 25% of quota in month 1, 50% in month 2, 75% in month 3, full quota thereafter. Tiered rates: 5% up to quota, 10% above.
**Plan:** `plans/04_new_recruiter_ramp.yaml` — tiered rule. Ramp via CSV columns `ramp_months=3` and `ramp_schedule="0.25 0.50 0.75"` on the payee row.
**Data:** One $30K contract in January. Base quota $100K, ramped to $25K effective. Attainment 120%.
**Result:** Tier 1 (0–100%): $25K × 5% = $1,250. Tier 2 (100%+): $5K × 10% = $500. Total $1,750. ✅ Intended.

### 5. Clawback on guarantee refund — negative transaction
**Real-world encoding:** A placement with a 90-day guarantee: if the candidate leaves, the fee is refunded net of earned commission. Engine handles negative `amount` directly.
**Plan:** `plans/05_negative_txn.yaml` — flat rate 10%.
**Data:** One $50K placement (+$50K) and one $15K guarantee refund (-$15K) in June.
**Result:** T1 = $5,000, T2 = −$1,500. Net $3,500. ✅ Intended.

**✔️ Note:** True-up (cross-period clawback via period locking) was also verified in the unit test suite (`test_locked_true_up_clawback`). The engine correctly handles negative amounts on both current-period and locked-period clawbacks.

### 6. SaaS AE — tiered on attainment + accelerator
**Real-world encoding:** SaaS AE with $100K monthly quota. Tiered core: 6% up to 100%, 12% above. Accelerator: 1.5× multiplier on the core rate above 100% attainment.
**Plan:** `plans/06_tiered_accel.yaml` — tiered rule + accelerator rule (rate 0.06, threshold 1.0, multiplier 1.5).
**Data:** Three deals: $60K Pro, $30K Enterprise, $15K Standard. Total bookings $105K (105% attainment).
**Result:**
| Line | Base | Rate | Commission | Mechanism |
|------|------|------|-----------|-----------|
| T1 | $60,000 | 6% | $3,600 | Tier 1 (0–100%) |
| T2 | $30,000 | 6% | $1,800 | Tier 1 (0–100%) |
| T3a | $10,000 | 6% | $600 | Tier 1 (fills remaining 100%) |
| T3b | $5,000 | 12% | $600 | Tier 2 (above 100%) |
| T3c | $5,000 | 9% (6% × 1.5) | $450 | Accelerator (above 100%) |
| **Total** | | | **$7,050** | |

✅ Intended. Boundary-crossing attainment works correctly — transactions are processed in close_date order, and each new transaction fills the remaining quota bands before spilling to higher tiers.

### 7. New business vs. renewal rates — product filter
**Real-world encoding:** SaaS AE gets 10% on new-business deals, 5% on renewals. The deal type lives in the `product` field.
**Plan:** `plans/07_new_vs_renewal.yaml` — two `flat_rate` rules with filters `product == "New Business"` and `product == "Renewal"`.
**Data:** One $50K new-business deal, one $30K renewal.
**Result:** T1: $50K × 10% = $5,000 (new business rule). T2: $30K × 5% = $1,500 (renewal rule). Total $6,500. ✅ Intended.

**✅ This works because the deal type is in `product`.** If the distinction lived in a `metadata` column instead, the filter could not reach it (see Expressiveness Gaps).

### 8. Product SPIF — flat bonus on specific product
**Real-world encoding:** Standard 5% on all deals + an extra 2% SPIF on Enterprise deals.
**Plan:** `plans/08_product_spif.yaml` — two `flat_rate` rules: `standard` (5%, no filter) and `enterprise_spif` (2%, filter `product == "Enterprise"`).
**Data:** One $40K Pro deal, one $60K Enterprise deal.
**Result:** Pro: 5% × $40K = $2,000. Enterprise: 5% × $60K + 2% × $60K = $3,000 + $1,200 = $4,200. Total $6,200. ✅ Intended.

---

## Expressiveness gaps

These are concrete limitations of the current DSL. Each is a candidate for the roadmap.

### G1. Filters cannot access metadata keys (CRITICAL)

**What's missing:** The filter expression parser accesses only top-level Transaction attributes via `getattr(txn, field, None)`. The `metadata` field (a `dict[str, Any]`) is accessible, but its individual keys are not — `deal_type == "new"` fails because there is no `deal_type` attribute on Transaction.

**Real-world impact:** Many comp plans tag deals with metadata columns (deal type, region, product line, channel) that determine rates or eligibility. Without metadata-key access, all such distinctions must be crammed into the single `product` string field.

**What's required:** Extend the filter parser to support dotted field access (e.g., `metadata.deal_type == "new"`) or bracket notation (`metadata['deal_type']`). Alternatively, add a separate mechanism to register known metadata keys as filterable fields.

### G2. Close-date filtering is non-functional

**What's missing:** `close_date` is a Python `date` object. The filter comparison code only handles `Decimal` fields for ordering operators (`>`, `<`, `>=`, `<=`). Comparisons on `close_date` silently return `False`.

**Real-world impact:** Date-windowed SPIFs ("2× payout on deals closed in Q2"), quarterly bonuses, and eligibility windows (e.g., "ramp only applies to deals closed after start date") cannot be expressed.

**What's required:** Extend the comparison AST node to handle `date` and `datetime` field types, parsing date literals from filter strings (e.g., `close_date >= "2026-06-01"`).

### G3. Commission on margin / gross profit (not amount)

**What's missing:** The engine computes commissions exclusively from `amount`. There is no way to express "20% of gross profit" when the transaction contains both a bill rate and a pay rate.

**Real-world impact:** Staffing/recruiting agencies (a primary target ICP) almost universally pay on gross profit margin. They would need to pre-calculate GP and stuff it into `amount`, losing the source data that feeds profitability analysis and audit.

**What's required:** Allow rule definitions to reference a named field other than `amount` (e.g., `base_field: margin`). This requires the Transaction model to accept arbitrary numeric fields (or a typed metadata schema) and the rule executor to use the specified field.

### G4. No caps, floors, draws, or guarantees

**What's missing:** The engine has no mechanism for:
- **Cap:** maximum commission per period ("capped at $20K/month").
- **Floor/minimum:** guaranteed minimum per period ("at least $2K/month").
- **Draw:** recoverable advance against future commissions.
- **Cliff:** zero payout below a threshold (distinct from tiered, which pays at lower rates).

**Real-world impact:** Draws and minimums are near-universal in SaaS comp plans. Caps protect against windfalls on outlier deals.

**What's required:** Post-processing modifiers on commission results: `cap(Decimal)`, `floor(Decimal)`, or a `DrawSchedule` model. A draw is stateful across periods and would require draw-balance tracking in the database.

### G5. No per-product or per-category quotas

**What's missing:** `_compute_attainment` sums ALL credited bookings per (payee_id, window) without any categorization. `Payee.quota_for(window_key)` returns a single Decimal. There is no way to express "50% of quota from new business, 50% from expansion."

**Real-world impact:** SaaS comp plans frequently split quotas by product line, deal type, or region. Without per-category quotas, the attainment calculation for tiered/accelerator rules is always against a blended total.

**What's required:** Extend `Payee` with per-category quotas (e.g., `quota_breakdown: dict[str, Decimal]`) and allow rules to declare which category they draw from. `_compute_attainment` would need to be category-aware.

### G6. No MBO / bonus / non-commission components

**What's missing:** The engine only processes transaction-based commissions. There is no concept of a fixed bonus, an MBO-based payout, or a discretionary adjustment.

**Real-world impact:** Many comp plans have a base + variable split where the variable portion includes both commission and a management-by-objectives bonus (e.g., 70% commission / 30% MBO).

**What's required:** A `Bonus` or `Adjustment` rule type that adds a fixed or calculated amount per payee per period, outside the transaction loop. This may be as simple as a flat_amount rule with an optional filter.

### G7. Overlay credits not independently testable via CSV

**What's missing:** The `credits` field on Transaction (which enables splits and overlays) is only settable programmatically. The CSV loader does not parse a `credits` column.

**Real-world impact:** Anyone testing or operating via CSV files (the primary data-in path) cannot express desk splits, team credits, or overlay bonuses. This is an operational gap, not an expressiveness gap per se, but it blocks real-world CSV-driven workflows.

**What's required:** Parse a `credits` CSV column as JSON (e.g., `[{"payee_id":"P2","split_pct":0.4,"kind":"split"}]`) in the loader.

### G8. No attainment category filtering (blended-only attainment)

**What's missing:** Related to G5. All bookings count toward attainment equally. A deal type that earns a different commission rate still contributes the same to quota attainment, which may over- or under-weight its importance.

**Real-world impact:** If new business earns 10% and renewal earns 5%, but both count equally toward the $100K quota, the rep's attainment is inflated by high-volume/low-rate renewal deals.

**What's required:** Allow rules to declare whether their bookings count toward attainment (`count_toward_attainment: bool`), or support weighted attainment.

### G9. No YTD / cumulative attainment mode

**What's missing:** Attainment always resets per window (monthly/quarterly/annual). There is no mode where attainment accumulates year-to-date and tiers are applied against the cumulative total.

**Real-world impact:** Some comp plans use a YTD attainment curve where the rate increases as cumulative bookings cross thresholds over the year.

**What's required:** Add a `cumulative: true` option on the plan, which changes `_window_key` to accumulate from the start of the fiscal year.

### G10. No plan versioning / effective-date scoping

**What's missing:** A payee can have only one active plan at a time. There is no way to express "Plan A was in effect Jan–Mar, Plan B from April onward" without running separate calculations and stitching results.

**Real-world impact:** Comp plan changes at quarter or year boundaries are common. The engine currently requires the operator to partition transactions manually.

**What's required:** Add `effective_from` / `effective_to` to the Plan model, or allow per-payee plan assignments with date ranges.

---

## Summary

| Plan | Expressible? | Correct? | Notes |
|------|:-----------:|:--------:|-------|
| 1. Perm placement fee | ✅ | ✅ | Flat rate on amount |
| 2. Contract margin (pre-calc GP) | ⚠️ | ✅ | Only if GP is pre-calculated into `amount` |
| 3. Desk split (credits) | ⚠️ | ✅ | Credits require programmatic construction |
| 4. New-recruiter ramp | ✅ | ✅ | Ramp via CSV columns works for tiered plans |
| 5. Clawback (negative txn) | ✅ | ✅ | Negative amount handled directly |
| 6. Tiered + accelerator | ✅ | ✅ | Boundary-crossing correct, accelerator correct |
| 7. New biz vs renewal (product filter) | ✅ | ✅ | Works when type is in `product` field |
| 8. Product SPIF | ✅ | ✅ | Flat + filtered flat rule |

**Verdict:** The DSL can express the core commission math for both staffing and SaaS use cases **if** data is pre-shaped to fit its constraints (GP in `amount`, deal type in `product`, no caps/draws, no date-windowed SPIFs). The 10 gaps above represent the delta between "can technically run" and "can model real comp plans without workarounds."

The top three gaps by real-world blocking power:
1. **G1 (metadata-key filters)** — without this, every deal-type/region/channel distinction must be stuffed into `product`.
2. **G3 (commission on margin/GP)** — blocks the staffing ICP from using the engine natively without pre-computation.
3. **G4 (caps/floors/draws)** — near-universal in SaaS comp plans; without them the engine produces numbers that would need manual adjustment.

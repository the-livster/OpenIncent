# Plan templates

Ready-to-adapt comp plans for the patterns that come up most often. Each is a
complete, valid plan that **ships its own executable assertions** — copy one,
change the rates and quota to yours, and run `icm check-plan` to prove it still
pays what you intend before it ever touches a payee.

| Template | Pattern | Rule types shown |
|----------|---------|------------------|
| [`perm_placement_tiered.yaml`](perm_placement_tiered.yaml) | Perm recruiter — % of placement fees, higher rate above target | tiered (boundary-crossing) |
| [`contract_gp_margin.yaml`](contract_gp_margin.yaml) | Contract/temp recruiter — commission on gross profit, not revenue | tiered, `base: margin` |
| [`saas_ae_tiered.yaml`](saas_ae_tiered.yaml) | Quota-carrying AE — base rate to quota, uplift above | tiered |
| [`saas_ae_accelerator.yaml`](saas_ae_accelerator.yaml) | AE with an overachievement kicker on bookings above quota | flat_rate + accelerator |
| [`sdr_pipeline_flat.yaml`](sdr_pipeline_flat.yaml) | SDR — flat % of sourced pipeline, with a minimum-attainment floor | flat_rate + `min_attainment_pct` |
| [`custom_formula_deal_cap.yaml`](custom_formula_deal_cap.yaml) | Anything the built-in types can't express — here, a per-deal cap | formula (escape hatch) |

## Use one

```bash
cd icm-engine

# 1. Prove the plan pays what you think (runs the assertions through the engine).
uv run icm check-plan examples/templates/perm_placement_tiered.yaml

# 2. Run it against your deals and payees.
uv run icm --plan examples/templates/perm_placement_tiered.yaml \
  --transactions your_deals.csv --payees your_reps.csv --output out/ --csv
```

## Adapting them

- **Rates / tiers / quota** live in the `rules` and per-payee `quota`. Change them,
  then update the `assertions` to match — a failing assertion after an edit means
  the change did something you didn't expect.
- **Caps and floors:** add `cap: "5000"` to any rule, a plan-level `payout_cap`, or
  a `draw` (recoverable or guarantee) on the plan or payee.
- **Filters:** scope a rule to part of the data with `filter: 'product == "Enterprise"'`
  (any input column works). Filtered rules can't be exercised by `check-plan`'s
  synthetic deals — validate those against a sample of real transactions instead.
- **Splits, overlays, manager overrides, ramps:** these live in the payee/transaction
  data, not the plan. See [`../staffing_reference/`](../staffing_reference/) for a full
  worked run that uses them.

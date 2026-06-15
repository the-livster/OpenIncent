# Demo: intra-deal threshold splitting

A single **$120,000** deal carries the rep from 0% past 100% of quota. The engine
splits it across tiers automatically — the part up to quota pays at 5%, the part
above at 10% — and each slice is explained in plain English on the statement.

Run it:

```
icm --plan plan.yaml --transactions deals.csv --payees reps.csv --output out/ --csv
icm statements --plan plan.yaml --transactions deals.csv --payees reps.csv \
    --output out/ --period 2026-06 --format html
icm check-plan plan.yaml
```

Expected for D1 — two commission lines:

| Slice | Base | Rate | Commission | Explanation on statement |
|-------|------|------|-----------|--------------------------|
| 1 | $100,000 | 5% | $5,000 | "100000 of this deal fell between 0% and 100% of quota -> 5.00%" |
| 2 | $20,000 | 10% | $2,000 | "20000 of this deal fell between 100% and 120% of quota -> 10.00%" |
| **Total** | | | **$7,000** | |

`icm check-plan` runs the plan's two assertions (OTE at quota = $5,000; the
crossing deal = $7,000) so a mistranscribed rate fails before payday.

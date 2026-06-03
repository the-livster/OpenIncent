# Excel (.xlsx) support with column mapping

icm-engine reads and writes `.xlsx` files natively. You don't need to rename your columns to match the engine's schema — column mapping figures it out.

## Quick start

```bash
icm --plan plan.yaml --transactions messy_transactions.xlsx --payees payees.csv --output ./out/
```

If your xlsx columns don't match exactly, the engine auto-infers the mapping and logs it:

```
Mapped 'Transaction ID' → id (confidence: 95%)
Mapped 'Rep Name' → payee_id (confidence: 92%)
Mapped 'ACV ($)' → amount → strip_currency (confidence: 88%)
```

## Column mapping

The mapping system uses fuzzy matching (rapidfuzz) against a hardcoded set of common RevOps column names:

| Target field   | Recognized aliases |
|----------------|-------------------|
| `id`           | Transaction ID, Txn ID, Row ID |
| `payee_id`     | Rep Name, Sales Rep, Employee, Agent, Broker |
| `deal_id`      | Deal, Opportunity, Opp ID, Order ID, Contract ID |
| `period`       | Period, Month, Quarter, Fiscal Period |
| `amount`       | Amount, ACV, TCV, Value, Deal Size, Revenue, $ |
| `product`      | Product, SKU, Product Family, Item, Solution, Tier |
| `close_date`   | Close Date, Closed, Won Date, Sign Date, Effective Date |
| `quota`        | Quota, Target, Goal, Annual Quota |
| `name`         | Name, Employee Name, Rep Name, Full Name |
| `plan_id`      | Plan, Plan ID, Comp Plan |
| `effective_from` | Effective From, Start Date, From Date |
| `effective_to` | Effective To, End Date, To Date, Until |

Confidence threshold is 70%. Columns below that aren't mapped.

## Save and reuse mappings

Save an inferred mapping:

```bash
icm map messy_transactions.xlsx --target transactions -o mapping.yaml
```

Reuse it on the next run:

```bash
icm --plan plan.yaml --transactions messy_transactions.xlsx --payees payees.csv --output ./out/ --mapping mapping.yaml
```

## Transformations

Mapping can also apply transformations to values:

| Transform | Input | Output |
|-----------|-------|--------|
| `strip_currency` | $1,234.56 | 1234.56 |
| `parse_date_us` | 04/15/2026 | 2026-04-15 |
| `parse_date_eu` | 15.04.2026 | 2026-04-15 |
| `lowercase` | ALICE | alice |
| `uppercase` | alice | ALICE |
| `strip` | " foo " | "foo" |

Currency stripping and date parsing are auto-suggested when the target field is `amount`/`quota` or the source contains date-like values.

## Excel reading behavior

- **Header auto-detection**: scans the first 10 rows, picks the row with the most text cells. Prefers rows with unique values (headers) over rows with repeated text (merged title cells).
- **Merged cells**: automatically unmerged, with the top-left value propagated.
- **Totals rows**: rows whose first cell is "Total", "Grand Total", or "Totals" (case-insensitive) are skipped.
- **Blank rows**: skipped.
- **Numeric precision**: floats are converted to strings with full precision (no rounding artifacts).
- **Multi-sheet workbooks**: the first non-empty sheet is read. A warning is logged if multiple sheets exist.

## Excel writing behavior

- Money columns (`amount`, `commission_amount`, `rate`, `base_amount`, `quota`, or any column ending in `_amount`) get `$#,##0.0000` format.
- Headers are bold with frozen top row.
- Column widths are auto-sized (capped at 40 characters).

## CLI

```bash
# Infer a column mapping for a file
icm map transactions.xlsx --target transactions
icm map payees.xlsx --target payees -o mapping.yaml

# Calculate with xlsx inputs → xlsx outputs
icm --plan plan.yaml --transactions deals.xlsx --payees reps.xlsx --output ./out/

# Force CSV output even when inputs are xlsx
icm --plan plan.yaml --transactions deals.xlsx --payees reps.xlsx --output ./out/ --csv

# Save the inferred mapping for reuse
icm --plan plan.yaml --transactions deals.xlsx --payees reps.xlsx --output ./out/ --save-mapping mapping.yaml
```

## Limitations

- No `.xls` (legacy Excel) support — only `.xlsx`.
- Column names with duplicate text (e.g., `Amount` and `Amount_2`) get deduplicated with numeric suffixes.
- The mapping inference is best-effort. Review the logged mappings on first run.

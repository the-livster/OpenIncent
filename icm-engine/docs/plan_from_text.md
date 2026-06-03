# plan-from-text

Generate validated commission plan YAML from natural language using Claude.

## What it does

You describe a compensation plan in plain English, and the LLM produces a
validated `Plan` YAML file that round-trips through pydantic v2 validation.
The LLM writes the plan structure only — it never touches calculations.

## 30-second example

```bash
uv run icm plan-from-text \
  "5% on closed deals, 8% above 100% quota, 12% above 150%, plus 2% SPIF on Enterprise" \
  --plan-id saas_ae_2025 \
  --output examples/generated_plan.yaml
```

Output:
```
Generating plan via claude-sonnet-4-5 (~$0.05, attempt 1/3)...
Generated plan with 3 rule(s). Saved to examples/generated_plan.yaml.
Sanity check: SANITY-T001 → $500.00 via R-001
```

Then use the generated plan:
```bash
uv run icm \
  --plan examples/generated_plan.yaml \
  --transactions examples/saas_transactions.csv \
  --payees examples/saas_payees.csv \
  --output ./output
```

## Cost

$0.03–0.10 per generation depending on plan complexity, using
claude-sonnet-4-5. The prompt includes the full JSON schema plus two
examples, so expect ~2-4K input tokens.

## Privacy

Plan descriptions are sent to the Anthropic API. Per Anthropic's API ToS,
data is not used for model training. See https://www.anthropic.com/legal for
details on data retention and privacy.

## Limitations

- Does not handle SPIFs with date windows
- Does not handle weighted deal splits or crediting hierarchies
- May need iteration on complex or unusual comp structures
- No web UI integration (CLI-only for now)
- Non-Anthropic providers not supported in v1

## Running the eval suite

```bash
ICM_RUN_EVALS=1 uv run python evals/plan_author/run_plan_author_evals.py
```

This runs 8 curated test cases through the real API and asserts rule counts,
types, and filter patterns. Exits non-zero on any failure. Used to validate
prompt changes don't regress generation quality.

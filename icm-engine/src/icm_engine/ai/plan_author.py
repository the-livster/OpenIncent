from __future__ import annotations

import json
import os
import re
from typing import Any

import yaml
from anthropic import Anthropic

from icm_engine.exceptions import MissingAPIKeyError, PlanGenerationError
from icm_engine.models import Plan

_DEFAULT_MODEL = "claude-sonnet-4-5"
_MAX_ATTEMPTS = 3

_SYSTEM_TEMPLATE = """\
You generate sales commission plan definitions in YAML for the icm-engine system.

## Schema

The Plan model is a pydantic v2 model. Below is its JSON Schema. Every field,
rule type, and constraint shown here MUST be satisfied by your output.

```json
{schema_json}
```

## Rule types

- **flat_rate**: A fixed percentage on every matching deal. Has `type: flat_rate`,
  `id`, optional `filter`, and `rate`.
- **tiered**: Rate increases at attainment thresholds. Has `type: tiered`, `id`,
  optional `filter`, and `tiers` (a list of `{{threshold_pct, rate}}`, ordered
  ascending by threshold_pct). The first tier starts at 0%; each tier's rate
  applies from the previous threshold up to its threshold_pct. The last tier
  should have a very high threshold_pct (e.g. 100.0).
- **accelerator**: Higher multiplier on above-threshold portion. Has
  `type: accelerator`, `id`, optional `filter`, `rate`, `threshold_pct`, and
  `multiplier`. The rate above threshold is `rate * multiplier`.

## Hard requirements

1. All Decimal values MUST be quoted strings: `rate: "0.05"` NOT `rate: 0.05`.
2. `period_type` MUST be one of: `monthly`, `quarterly`.
3. `currency` MUST be a 3-letter ISO 4217 code (e.g. `USD`, `EUR`).
4. Rule `id` values MUST follow the pattern `R-NNN` (e.g. `R-001`, `R-002`).
5. Filter expressions MUST use only these operators: `==`, `!=`, `>`, `<`,
   `>=`, `<=`, `in`. Join with `and` / `or`. String values in double quotes.
   Example: `product == "Enterprise" and amount > 500`.
6. `tiers` in tiered rules MUST be ordered by threshold_pct ascending.
7. `threshold_pct` in tiers and accelerators is a DECIMAL fraction of quota,
   e.g. `"1.0"` means 100% of quota, `"1.5"` means 150%.
8. Every plan MUST have: `plan_id`, `name`, `period_type`, `currency`, `rules`.

## Examples

### Example 1: Simple flat-rate plan

```yaml
plan_id: flat_standard
name: "Standard Flat Rate Plan"
period_type: monthly
currency: USD
rules:
  - id: R-001
    type: flat_rate
    rate: "0.05"
```

### Example 2: Tiered plan with accelerator

```yaml
plan_id: saas_ae_plan
name: "SaaS AE Plan with Accelerator"
period_type: monthly
currency: USD
rules:
  - id: R-001
    type: tiered
    tiers:
      - threshold_pct: "1.0"
        rate: "0.05"
      - threshold_pct: "1.5"
        rate: "0.08"
      - threshold_pct: "100.0"
        rate: "0.12"
  - id: R-002
    type: accelerator
    rate: "0.05"
    threshold_pct: "1.0"
    multiplier: "2.0"
  - id: R-003
    type: flat_rate
    rate: "0.02"
    filter: 'product == "Enterprise"'
```

Output ONLY the YAML. No prose, no markdown fences, no commentary.\
"""


def _build_prompt(
    description: str,
    plan_id: str | None = None,
    retry_context: str | None = None,
) -> tuple[str, str]:
    """Build the system prompt and user message for the LLM call."""
    schema = Plan.model_json_schema()
    system = _SYSTEM_TEMPLATE.format(schema_json=json.dumps(schema, indent=2))

    user = description
    if plan_id:
        user = f"Use plan_id: {plan_id}\n\n{description}"
    if retry_context:
        user += retry_context

    return system, user


def _call_claude(system: str, user: str, *, model: str, api_key: str) -> str:
    """Call the Claude API and return the raw response text."""
    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    content = response.content[0]
    return content.text if hasattr(content, "text") else str(content)


def generate_plan_from_text(
    description: str,
    *,
    plan_id: str | None = None,
    model: str | None = None,
    console: Any = None,
) -> Plan:
    """Generate a validated Plan from a natural-language description.

    Args:
        description: free-text description of the comp plan
        plan_id: optional override; if None, LLM proposes one
        model: optional model override; defaults to env ICM_LLM_MODEL or claude-sonnet-4-5
        console: optional rich Console instance for status output

    Returns:
        Validated Plan instance.

    Raises:
        PlanGenerationError: after 3 failed attempts.
        MissingAPIKeyError: if ANTHROPIC_API_KEY is unset.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise MissingAPIKeyError()

    model_name = model or os.getenv("ICM_LLM_MODEL") or _DEFAULT_MODEL

    last_yaml = ""
    last_error = ""

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        retry_context = None
        if attempt > 1:
            retry_context = (
                f"\n\nYour previous attempt failed validation with this error: {last_error}\n"
                f"Here is what you produced:\n```yaml\n{last_yaml}\n```\n"
                "Fix it and try again."
            )

        system, user = _build_prompt(description, plan_id=plan_id, retry_context=retry_context)

        msg = (
            f"Generating plan via {model_name} "
            f"(~$0.05, attempt {attempt}/{_MAX_ATTEMPTS})..."
        )
        if console is not None:
            console.print(f"[dim]{msg}[/dim]")
        else:
            print(msg)

        raw = _call_claude(system, user, model=model_name, api_key=api_key)

        try:
            yaml_text = _extract_yaml(raw)
            last_yaml = yaml_text
            return _validate(yaml_text)
        except Exception as e:
            last_error = str(e)

    raise PlanGenerationError(
        last_yaml=last_yaml,
        last_error=last_error,
        attempts=_MAX_ATTEMPTS,
    )


def _extract_yaml(response_text: str) -> str:
    text = response_text.strip()
    m = re.match(r"```(?:yaml)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def _validate(yaml_text: str) -> Plan:
    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise ValueError(f"YAML parse error: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError(f"YAML must be a mapping, got {type(raw).__name__}")
    return Plan.model_validate(raw)

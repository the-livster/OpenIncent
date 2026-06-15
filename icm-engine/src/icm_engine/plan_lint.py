"""Static plan health check — catch comp-plan smells before a run.

Pure analysis of a Plan: no data, no engine, no money moved. Complements
`check-plan` (which verifies payouts against declared assertions) by catching
structural mistakes that don't need data to see — an accelerator with no base
rule, a cap below OTE, a gate above target, decreasing tier rates, missing
assertions. Findings are returned, never raised; `icm lint` renders them.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from icm_engine.models import AcceleratorRule, FlatRateRule, Plan, TieredRule


@dataclass
class LintFinding:
    severity: str  # "error" | "warning" | "info"
    code: str
    message: str


def lint_plan(plan: Plan) -> list[LintFinding]:
    """Return static health-check findings for a plan, most severe first."""
    out: list[LintFinding] = []

    if not plan.rules:
        out.append(LintFinding("error", "no_rules", "Plan has no rules - every deal would pay 0."))

    seen: set[str] = set()
    for r in plan.rules:
        if r.id in seen:
            out.append(LintFinding("error", "duplicate_rule_id", f"Duplicate rule id {r.id!r}."))
        seen.add(r.id)

    has_base = any(isinstance(r, FlatRateRule | TieredRule) for r in plan.rules)

    for r in plan.rules:
        rate = getattr(r, "rate", None)
        if rate is not None and rate == 0:
            out.append(LintFinding(
                "warning", "zero_rate", f"Rule {r.id!r} has rate 0 - it pays nothing."
            ))

        gate = getattr(r, "min_attainment_pct", None)
        if gate is not None and gate > Decimal("1"):
            out.append(LintFinding(
                "warning", "gate_above_target",
                f"Rule {r.id!r} pays nothing below {gate:.0%} attainment - above target. Intended?",
            ))

        if isinstance(r, TieredRule):
            for i in range(1, len(r.tiers)):
                if r.tiers[i].rate < r.tiers[i - 1].rate:
                    out.append(LintFinding(
                        "warning", "decreasing_tier_rate",
                        f"Rule {r.id!r}: tier {i + 1} rate ({r.tiers[i].rate}) is below the previous "
                        f"tier ({r.tiers[i - 1].rate}) - higher attainment pays a lower marginal rate.",
                    ))
            for t in r.tiers:
                if t.rate == 0:
                    out.append(LintFinding(
                        "warning", "zero_tier_rate",
                        f"Rule {r.id!r} has a tier (threshold {t.threshold_pct}) with rate 0.",
                    ))

        if isinstance(r, AcceleratorRule) and not has_base:
            out.append(LintFinding(
                "warning", "accelerator_no_base",
                f"Accelerator rule {r.id!r} has no companion flat_rate/tiered rule - bookings below "
                f"{r.threshold_pct:.0%} of quota pay nothing.",
            ))

    if plan.payout_cap is not None and plan.ote is not None and plan.payout_cap < plan.ote:
        out.append(LintFinding(
            "warning", "cap_below_ote",
            f"payout_cap ({plan.payout_cap}) is below the stated OTE ({plan.ote}) - "
            f"on-target reps would be capped.",
        ))

    if not plan.assertions:
        if plan.ote is not None:
            out.append(LintFinding(
                "info", "no_assertions",
                "Plan declares `ote` but has no assertions - add one ('at 100% attainment, payout == "
                "ote') so check-plan can guard it.",
            ))
        else:
            out.append(LintFinding(
                "info", "no_assertions",
                "Plan has no assertions - add some so check-plan can catch payout mistranscriptions.",
            ))

    order = {"error": 0, "warning": 1, "info": 2}
    out.sort(key=lambda f: order.get(f.severity, 3))
    return out

"""Executable plan invariants — run a plan's declared assertions through the engine.

Each PlanAssertion describes a tiny scenario (one payee at a given quota, a list of
deals) and the total payout it must produce. `check_plan` runs each through the real
engine and reports pass/fail, so a transcription error that silently changes payouts
is caught on every plan change rather than at payroll time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from icm_engine.models import Payee, Plan, Transaction


@dataclass
class AssertionResult:
    name: str
    passed: bool
    expected: Decimal
    actual: Decimal
    detail: str = ""


def check_plan(plan: Plan) -> list[AssertionResult]:
    """Run every assertion declared on `plan` and return the results (in order)."""
    from icm_engine.engine import CommissionEngine

    engine = CommissionEngine()
    results: list[AssertionResult] = []
    for a in plan.assertions:
        payee = Payee(
            id="_assert", name="_assert", quota=a.quota,
            plan_id=plan.plan_id, effective_from=date(2000, 1, 1),
        )
        txns: list[Transaction] = []
        for i, value in enumerate(a.deals):
            if a.base == "margin":
                txns.append(Transaction(
                    id=f"_d{i}", payee_id="_assert", amount=Decimal("0"),
                    period=a.period, margin=value,
                ))
            else:
                txns.append(Transaction(
                    id=f"_d{i}", payee_id="_assert", amount=value, period=a.period,
                ))
        try:
            result = engine.calculate(plan, txns, [payee])
            actual = sum((c.commission_amount for c in result.commissions), Decimal("0"))
        except Exception as e:  # a plan that can't even run is a failed assertion
            results.append(
                AssertionResult(a.name, False, a.expect_total, Decimal("0"), f"error: {e}")
            )
            continue
        passed = abs(actual - a.expect_total) <= a.tolerance
        results.append(AssertionResult(a.name, passed, a.expect_total, actual))
    return results


def all_passed(results: list[AssertionResult]) -> bool:
    return all(r.passed for r in results)

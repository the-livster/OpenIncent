import os
import tempfile
from decimal import Decimal

from icm_engine.models import (
    FlatRateRule,
    Plan,
    PlanAssertion,
    Tier,
    TieredRule,
)
from icm_engine.plan_check import all_passed, check_plan


def _flat_plan(rate: str = "0.05", assertions=None) -> Plan:
    return Plan(
        plan_id="p", name="P", period_type="monthly", currency="USD",
        rules=[FlatRateRule(type="flat_rate", id="r", rate=Decimal(rate))],
        assertions=assertions or [],
    )


class TestPlanCheck:
    def test_no_assertions_returns_empty(self) -> None:
        assert check_plan(_flat_plan()) == []

    def test_passing_assertion(self) -> None:
        plan = _flat_plan("0.05", [PlanAssertion(
            name="5pct at quota", quota=Decimal("100000"),
            deals=[Decimal("100000")], expect_total=Decimal("5000"),
        )])
        results = check_plan(plan)
        assert len(results) == 1
        assert results[0].passed
        assert results[0].actual == Decimal("5000")
        assert all_passed(results)

    def test_failing_assertion_catches_mistranscription(self) -> None:
        # The plan pays 5% (→ 5,000 at quota) but the author believed OTE was 6,000.
        plan = _flat_plan("0.05", [PlanAssertion(
            name="ote_at_quota", quota=Decimal("100000"),
            deals=[Decimal("100000")], expect_total=Decimal("6000"),
        )])
        results = check_plan(plan)
        assert not results[0].passed
        assert results[0].actual == Decimal("5000")
        assert not all_passed(results)

    def test_tiered_boundary_crossing_assertion(self) -> None:
        # quota 100k; 150k of deals → 100k @ 5% + 50k @ 10% = 10,000
        plan = Plan(
            plan_id="p", name="P", period_type="monthly", currency="USD",
            rules=[TieredRule(type="tiered", id="t", tiers=[
                Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
                Tier(threshold_pct=Decimal("2.0"), rate=Decimal("0.10")),
            ])],
            assertions=[PlanAssertion(
                name="crosses tier 1", quota=Decimal("100000"),
                deals=[Decimal("100000"), Decimal("50000")],
                expect_total=Decimal("10000"),
            )],
        )
        results = check_plan(plan)
        assert results[0].passed, results[0]

    def test_margin_assertion(self) -> None:
        plan = Plan(
            plan_id="p", name="P", period_type="monthly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="r", rate=Decimal("0.10"), base="margin")],
            assertions=[PlanAssertion(
                name="gp", quota=Decimal("100000"),
                deals=[Decimal("4000")], expect_total=Decimal("400"), base="margin",
            )],
        )
        results = check_plan(plan)
        assert results[0].passed, results[0]

    def test_yaml_roundtrip_with_assertions(self) -> None:
        from icm_engine.loader import load_plan

        yaml_text = (
            "plan_id: rt\n"
            "name: RT\n"
            "period_type: monthly\n"
            "currency: USD\n"
            'ote: "5000"\n'
            "rules:\n"
            "  - id: r\n"
            "    type: flat_rate\n"
            '    rate: "0.05"\n'
            "assertions:\n"
            "  - name: ote_at_quota\n"
            '    quota: "100000"\n'
            '    deals: ["100000"]\n'
            '    expect_total: "5000"\n'
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_text)
            path = f.name
        try:
            plan = load_plan(path)
            assert plan.ote == Decimal("5000")
            assert len(plan.assertions) == 1
            assert all_passed(check_plan(plan))
        finally:
            os.unlink(path)

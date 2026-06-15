from __future__ import annotations

from decimal import Decimal

from typer.testing import CliRunner

from icm_engine.cli import app
from icm_engine.models import (
    AcceleratorRule,
    FlatRateRule,
    Plan,
    PlanAssertion,
    Tier,
    TieredRule,
)
from icm_engine.plan_lint import lint_plan

runner = CliRunner()


def _plan(**kw) -> Plan:
    base = dict(plan_id="p", name="P", period_type="monthly", currency="USD")
    base.update(kw)
    return Plan(**base)


def _codes(plan: Plan) -> set[str]:
    return {f.code for f in lint_plan(plan)}


def test_clean_plan_has_no_findings() -> None:
    plan = _plan(
        ote=Decimal("1000"),
        rules=[TieredRule(type="tiered", id="r", tiers=[
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.10")),
            Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.15")),
        ])],
        assertions=[PlanAssertion(name="a", quota=Decimal("10000"),
                                  deals=[Decimal("10000")], expect_total=Decimal("1000"))],
    )
    assert lint_plan(plan) == []


def test_no_rules_is_error() -> None:
    findings = lint_plan(_plan(rules=[]))
    assert any(f.code == "no_rules" and f.severity == "error" for f in findings)


def test_duplicate_rule_id_is_error() -> None:
    plan = _plan(rules=[
        FlatRateRule(type="flat_rate", id="dup", rate=Decimal("0.1")),
        FlatRateRule(type="flat_rate", id="dup", rate=Decimal("0.2")),
    ])
    assert "duplicate_rule_id" in _codes(plan)


def test_zero_rate_warns() -> None:
    plan = _plan(rules=[FlatRateRule(type="flat_rate", id="r", rate=Decimal("0"))])
    assert "zero_rate" in _codes(plan)


def test_decreasing_tier_rate_warns() -> None:
    plan = _plan(rules=[TieredRule(type="tiered", id="r", tiers=[
        Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.15")),
        Tier(threshold_pct=Decimal("100.0"), rate=Decimal("0.10")),  # lower than tier 1
    ])])
    assert "decreasing_tier_rate" in _codes(plan)


def test_accelerator_without_base_warns() -> None:
    plan = _plan(rules=[AcceleratorRule(
        type="accelerator", id="a", rate=Decimal("0.1"),
        threshold_pct=Decimal("1.0"), multiplier=Decimal("1.5"),
    )])
    assert "accelerator_no_base" in _codes(plan)


def test_accelerator_with_base_does_not_warn() -> None:
    plan = _plan(rules=[
        FlatRateRule(type="flat_rate", id="base", rate=Decimal("0.08")),
        AcceleratorRule(type="accelerator", id="a", rate=Decimal("0.04"),
                        threshold_pct=Decimal("1.0"), multiplier=Decimal("1.0")),
    ])
    assert "accelerator_no_base" not in _codes(plan)


def test_cap_below_ote_warns() -> None:
    plan = _plan(
        ote=Decimal("5000"), payout_cap=Decimal("3000"),
        rules=[FlatRateRule(type="flat_rate", id="r", rate=Decimal("0.1"))],
    )
    assert "cap_below_ote" in _codes(plan)


def test_gate_above_target_warns() -> None:
    plan = _plan(rules=[FlatRateRule(
        type="flat_rate", id="r", rate=Decimal("0.1"), min_attainment_pct=Decimal("1.5"),
    )])
    assert "gate_above_target" in _codes(plan)


def test_no_assertions_is_info() -> None:
    plan = _plan(rules=[FlatRateRule(type="flat_rate", id="r", rate=Decimal("0.1"))])
    findings = lint_plan(plan)
    assert any(f.code == "no_assertions" and f.severity == "info" for f in findings)


def test_cli_lint_clean_template() -> None:
    result = runner.invoke(app, ["lint", "examples/templates/perm_placement_tiered.yaml"])
    assert result.exit_code == 0, result.stdout


def test_cli_lint_errors_exit_nonzero(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "plan_id: bad\nname: Bad\nperiod_type: monthly\ncurrency: USD\nrules: []\n"
    )
    result = runner.invoke(app, ["lint", str(bad)])
    assert result.exit_code == 1
    assert "no_rules" in result.stdout

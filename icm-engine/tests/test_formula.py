"""Tests for the flexible calc layer: formula parser/evaluator and FormulaRule."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from icm_engine.engine import CommissionEngine
from icm_engine.formula import (
    FormulaEvalError,
    check_formula_fields,
    compile_formula,
)
from icm_engine.models import FormulaRule, Payee, Plan, Transaction
from icm_engine.plan_check import check_plan
from icm_engine.plan_lint import lint_plan


def _txn(**overrides) -> Transaction:
    defaults = dict(
        id="T001",
        payee_id="P001",
        deal_id="D001",
        period="2026-04",
        amount=Decimal("1000"),
        product=None,
        close_date=date(2026, 4, 15),
    )
    return Transaction(**(defaults | overrides))


def _payee(**overrides) -> Payee:
    defaults = dict(
        id="P001",
        name="Alice",
        quota=Decimal("10000"),
        plan_id="PLAN-A",
        effective_from=date(2026, 1, 1),
    )
    return Payee(**(defaults | overrides))


def _formula_plan(formula: str, **rule_overrides) -> Plan:
    rule = dict(type="formula", id="R-001", formula=formula)
    rule.update(rule_overrides)
    return Plan(
        plan_id="PLAN-A",
        name="Formula plan",
        period_type="monthly",
        currency="USD",
        rules=[rule],
    )


# --- parser / evaluator --------------------------------------------------


class TestFormulaEval:
    def _eval(self, source: str, **ctx) -> Decimal:
        return compile_formula(source).evaluate(ctx)

    def test_precedence(self) -> None:
        assert self._eval("2 + 3 * 4") == Decimal("14")
        assert self._eval("(2 + 3) * 4") == Decimal("20")
        assert self._eval("10 - 4 - 3") == Decimal("3")  # left-assoc
        assert self._eval("12 / 4 / 3") == Decimal("1")

    def test_unary_minus(self) -> None:
        assert self._eval("-5 + 8") == Decimal("3")
        assert self._eval("--5") == Decimal("5")
        assert self._eval("2 * -3") == Decimal("-6")

    def test_decimal_exactness(self) -> None:
        # The whole point of Decimal: no float artifacts.
        assert self._eval("0.1 + 0.2") == Decimal("0.3")

    def test_variables(self) -> None:
        assert self._eval("0.05 * amount", amount=Decimal("1000")) == Decimal("50")

    def test_numeric_string_coerced(self) -> None:
        # Metadata columns arrive as strings.
        assert self._eval("2 * headcount", headcount="7") == Decimal("14")

    def test_functions(self) -> None:
        assert self._eval("min(0.06 * 20000, 500)") == Decimal("500")
        assert self._eval("max(1, 2, 3)") == Decimal("3")
        assert self._eval("abs(-4)") == Decimal("4")
        assert self._eval("round(2.345, 2)") == Decimal("2.35")  # half-up
        assert self._eval("round(2.5)") == Decimal("3")
        assert self._eval("floor(2.9)") == Decimal("2")
        assert self._eval("ceil(2.1)") == Decimal("3")

    def test_if_and_comparisons(self) -> None:
        f = "if(attainment_pct >= 1.0, 0.10, 0.05) * amount"
        assert self._eval(f, attainment_pct=Decimal("1.2"), amount=Decimal("100")) == Decimal("10")
        assert self._eval(f, attainment_pct=Decimal("0.8"), amount=Decimal("100")) == Decimal("5")

    def test_if_with_and_or(self) -> None:
        f = "if(amount > 100 and amount < 200 or amount == 500, 1, 0)"
        assert self._eval(f, amount=Decimal("150")) == Decimal("1")
        assert self._eval(f, amount=Decimal("500")) == Decimal("1")
        assert self._eval(f, amount=Decimal("50")) == Decimal("0")

    def test_string_comparison_in_if(self) -> None:
        f = "if(product == 'Enterprise', 0.08, 0.04) * amount"
        assert self._eval(f, product="Enterprise", amount=Decimal("100")) == Decimal("8")
        assert self._eval(f, product="SMB", amount=Decimal("100")) == Decimal("4")

    def test_if_only_evaluates_taken_branch(self) -> None:
        # Guarded division must never raise even when quota is 0.
        f = "if(quota > 0, amount / quota, 0)"
        assert self._eval(f, quota=Decimal("0"), amount=Decimal("5")) == Decimal("0")

    def test_variables_collected(self) -> None:
        c = compile_formula("0.05 * amount + if(attainment_pct > 1, bonus_rate, 0)")
        assert c.variables == frozenset({"amount", "attainment_pct", "bonus_rate"})

    def test_backtick_variable(self) -> None:
        assert self._eval("2 * `Deal Size`", **{"Deal Size": Decimal("10")}) == Decimal("20")


class TestFormulaErrors:
    def test_syntax_error_at_compile(self) -> None:
        for bad in ("0.05 *", "min(", "amount ++ 2", "if(1, 2)", "nope(3)", ""):
            with pytest.raises(ValueError):
                compile_formula(bad)

    def test_unknown_variable_at_eval(self) -> None:
        with pytest.raises(FormulaEvalError, match="Unknown variable 'amnt'"):
            compile_formula("0.05 * amnt").evaluate({"amount": Decimal("1")})

    def test_none_variable_at_eval(self) -> None:
        with pytest.raises(FormulaEvalError, match="no value"):
            compile_formula("0.2 * margin").evaluate({"margin": None})

    def test_division_by_zero(self) -> None:
        with pytest.raises(FormulaEvalError, match="Division by zero"):
            compile_formula("amount / quota").evaluate(
                {"amount": Decimal("1"), "quota": Decimal("0")}
            )

    def test_bare_comparison_result_rejected(self) -> None:
        with pytest.raises(FormulaEvalError, match="condition"):
            compile_formula("amount > 100").evaluate({"amount": Decimal("500")})

    def test_non_numeric_string_in_arithmetic(self) -> None:
        with pytest.raises(FormulaEvalError):
            compile_formula("2 * region").evaluate({"region": "EMEA"})


class TestCheckFormulaFields:
    def test_flags_missing_variables_only(self) -> None:
        txns = [_txn(metadata={"headcount": "3"})]
        assert check_formula_fields("0.05 * amount + headcount * typo_field", txns) == [
            "typo_field"
        ]

    def test_builtins_never_flagged(self) -> None:
        assert check_formula_fields(
            "amount + margin + quota + attainment_pct + bookings", [_txn()]
        ) == []


# --- model validation -----------------------------------------------------


class TestFormulaRuleModel:
    def test_valid_rule_parses(self) -> None:
        plan = _formula_plan("0.05 * amount")
        assert isinstance(plan.rules[0], FormulaRule)

    def test_broken_formula_fails_at_load(self) -> None:
        with pytest.raises(ValidationError, match="Invalid formula"):
            _formula_plan("0.05 * (amount")


# --- engine integration -----------------------------------------------------


class TestFormulaEngine:
    def test_basic_percentage(self) -> None:
        plan = _formula_plan("0.05 * amount")
        result = CommissionEngine().calculate(plan, [_txn()], [_payee()])
        assert len(result.commissions) == 1
        assert result.commissions[0].commission_amount == Decimal("50")
        computed = [e for e in result.ledger if e.event_type == "commission_computed"]
        assert len(computed) == 1
        assert computed[0].inputs["formula"] == "0.05 * amount"

    def test_per_deal_cap(self) -> None:
        plan = _formula_plan("min(0.06 * amount, 500)")
        txns = [_txn(id="T1", amount=Decimal("5000")),
                _txn(id="T2", amount=Decimal("20000"))]
        result = CommissionEngine().calculate(plan, txns, [_payee()])
        by_txn = {c.transaction_id: c.commission_amount for c in result.commissions}
        assert by_txn == {"T1": Decimal("300"), "T2": Decimal("500")}

    def test_attainment_variable(self) -> None:
        # Quota 10000; bookings 12000 → attainment 1.2 → the 10% branch.
        plan = _formula_plan("if(attainment_pct >= 1.0, 0.10, 0.05) * amount")
        txns = [_txn(id="T1", amount=Decimal("4000")),
                _txn(id="T2", amount=Decimal("8000"))]
        result = CommissionEngine().calculate(plan, txns, [_payee()])
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("1200")

    def test_filter_respected(self) -> None:
        plan = _formula_plan("0.10 * amount", filter='product == "Enterprise"')
        txns = [_txn(id="T1", product="Enterprise"),
                _txn(id="T2", product="SMB")]
        result = CommissionEngine().calculate(plan, txns, [_payee()])
        assert [c.transaction_id for c in result.commissions] == ["T1"]
        skipped = [e for e in result.ledger
                   if e.event_type == "rule_skipped" and e.transaction_id == "T2"]
        assert skipped and skipped[0].inputs["reason"] == "filter_excluded"

    def test_margin_variable(self) -> None:
        plan = _formula_plan("0.25 * margin")
        txn = _txn(bill_rate=Decimal("100"), pay_rate=Decimal("60"), units=Decimal("10"))
        result = CommissionEngine().calculate(plan, [txn], [_payee()])
        assert result.commissions[0].commission_amount == Decimal("100")  # 400 GP * 0.25

    def test_missing_margin_skips_row_not_run(self) -> None:
        plan = _formula_plan("0.25 * margin")
        txns = [_txn(id="T1"),  # no margin data
                _txn(id="T2", margin=Decimal("400"))]
        result = CommissionEngine().calculate(plan, txns, [_payee()])
        assert [c.transaction_id for c in result.commissions] == ["T2"]
        skipped = [e for e in result.ledger
                   if e.event_type == "rule_skipped" and e.transaction_id == "T1"]
        assert skipped and skipped[0].inputs["reason"] == "formula_eval_error"

    def test_metadata_variable(self) -> None:
        plan = _formula_plan("50 * headcount")
        txn = _txn(metadata={"headcount": "4"})
        result = CommissionEngine().calculate(plan, [txn], [_payee()])
        assert result.commissions[0].commission_amount == Decimal("200")

    def test_zero_result_emits_no_commission_line(self) -> None:
        plan = _formula_plan("if(amount > 5000, 100, 0)")
        result = CommissionEngine().calculate(plan, [_txn()], [_payee()])
        assert result.commissions == []
        computed = [e for e in result.ledger if e.event_type == "commission_computed"]
        assert computed and computed[0].outputs["commission_amount"] == "0"

    def test_rule_cap_applies(self) -> None:
        plan = _formula_plan("0.10 * amount", cap="500")
        txns = [_txn(id=f"T{i}", amount=Decimal("3000")) for i in range(3)]
        result = CommissionEngine().calculate(plan, txns, [_payee()])
        total = sum(c.commission_amount for c in result.commissions)
        assert total == Decimal("500")

    def test_splits_credit_before_formula(self) -> None:
        plan = _formula_plan("0.10 * amount")
        txn = _txn(credits="P001:0.6;P002:0.4")
        payees = [_payee(), _payee(id="P002", name="Bob")]
        result = CommissionEngine().calculate(plan, [txn], payees)
        by_payee = {c.payee_id: c.commission_amount for c in result.commissions}
        assert by_payee == {"P001": Decimal("60"), "P002": Decimal("40")}

    def test_min_attainment_gate(self) -> None:
        # Quota 10000, bookings 2000 → 20% attainment, below the 50% gate.
        plan = _formula_plan("0.10 * amount", min_attainment_pct="0.5")
        result = CommissionEngine().calculate(
            plan, [_txn(amount=Decimal("2000"))], [_payee()]
        )
        assert result.commissions == []

    def test_negative_formula_result_allowed(self) -> None:
        # Clawback-style formulas may produce negative amounts.
        plan = _formula_plan("-0.02 * amount")
        result = CommissionEngine().calculate(plan, [_txn()], [_payee()])
        assert result.commissions[0].commission_amount == Decimal("-20")


# --- lint / check-plan ------------------------------------------------------


class TestFormulaLintAndCheck:
    def test_constant_formula_lint(self) -> None:
        findings = lint_plan(_formula_plan("100"))
        assert any(f.code == "constant_formula" for f in findings)

    def test_variable_formula_no_lint(self) -> None:
        findings = lint_plan(_formula_plan("0.05 * amount"))
        assert not any(f.code == "constant_formula" for f in findings)

    def test_formula_counts_as_base_for_accelerator(self) -> None:
        plan = Plan(
            plan_id="P", name="P", period_type="monthly", currency="USD",
            rules=[
                dict(type="formula", id="R-001", formula="0.05 * amount"),
                dict(type="accelerator", id="R-002", rate="0.05",
                     threshold_pct="1.0", multiplier="2.0"),
            ],
        )
        assert not any(f.code == "accelerator_no_base" for f in lint_plan(plan))

    def test_template_assertions_pass(self) -> None:
        from pathlib import Path

        import yaml

        template = (
            Path(__file__).parent.parent
            / "examples" / "templates" / "custom_formula_deal_cap.yaml"
        )
        plan = Plan.model_validate(yaml.safe_load(template.read_text()))
        results = check_plan(plan)
        assert results and all(r.passed for r in results), [
            (r.name, str(r.expected), str(r.actual)) for r in results
        ]

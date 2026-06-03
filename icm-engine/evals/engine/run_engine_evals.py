"""Golden-output evaluation suite for the calculation engine.

Runs each case in cases.yaml through the engine and compares actual
commissions against expected values.

Usage:
    uv run python evals/engine/run_engine_evals.py
"""
from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

from icm_engine.engine import CommissionEngine
from icm_engine.models import Commission, Payee, Plan, Transaction

CASES_PATH = Path(__file__).parent / "cases.yaml"


def _load_cases() -> list[dict]:
    with CASES_PATH.open() as f:
        return yaml.safe_load(f)


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _build_plan(raw: dict) -> Plan:
    return Plan.model_validate(raw)


def _build_transactions(raw: list[dict]) -> list[Transaction]:
    txns: list[Transaction] = []
    for r in raw:
        txns.append(Transaction(
            id=r["id"],
            payee_id=r["payee_id"],
            deal_id=r["deal_id"],
            period=r["period"],
            amount=Decimal(str(r["amount"])),
            product=r.get("product"),
            close_date=_parse_date(r["close_date"]),
        ))
    return txns


def _build_payees(raw: list[dict]) -> list[Payee]:
    payees: list[Payee] = []
    for r in raw:
        payees.append(Payee(
            id=r["id"],
            name=r["name"],
            quota=Decimal(str(r["quota"])),
            plan_id=r["plan_id"],
            effective_from=_parse_date(r["effective_from"]),
            effective_to=_parse_date(r["effective_to"]) if r.get("effective_to") else None,
        ))
    return payees


def _commission_key(c: dict | Commission) -> tuple[str, str, str]:
    if isinstance(c, Commission):
        return (c.transaction_id, c.payee_id, c.rule_id)
    return (c["transaction_id"], c["payee_id"], c["rule_id"])


def run_case(case: dict) -> tuple[bool, str]:
    """Run a single eval case. Returns (passed, message)."""
    name = case["name"]
    plan = _build_plan(case["plan"])
    transactions = _build_transactions(case.get("transactions", []))
    payees = _build_payees(case["payees"])

    engine = CommissionEngine()
    result = engine.calculate(plan, transactions, payees)

    expected = case.get("expected_commissions", [])

    # Group actual commissions by (txn_id, payee_id, rule_id)
    actual_by_key: dict[tuple[str, str, str], list[Commission]] = {}
    for c in result.commissions:
        key = _commission_key(c)
        actual_by_key.setdefault(key, []).append(c)

    expected_by_key: dict[tuple[str, str, str], list[dict]] = {}
    for e in expected:
        key = _commission_key(e)
        expected_by_key.setdefault(key, []).append(e)

    errors: list[str] = []

    # Check expected commissions exist with correct amounts
    for key, exp_list in expected_by_key.items():
        act_list = actual_by_key.get(key, [])
        # Sum expected amounts for this key
        exp_total = sum(Decimal(str(e["commission_amount"])) for e in exp_list)
        act_total = sum(c.commission_amount for c in act_list)
        if act_total != exp_total:
            errors.append(
                f"  Key {key}: expected total {exp_total}, got {act_total}"
            )

    # Check for unexpected commissions
    for key in actual_by_key:
        if key not in expected_by_key:
            total = sum(c.commission_amount for c in actual_by_key[key])
            errors.append(f"  Unexpected commission: {key} = {total}")

    # Check for missing expected commissions
    for key in expected_by_key:
        if key not in actual_by_key:
            errors.append(f"  Missing expected commission: {key}")

    if errors:
        return False, f"FAIL: {name}\n" + "\n".join(errors)
    return True, f"PASS: {name}"


def main() -> int:
    cases = _load_cases()
    passed = 0
    failed = 0

    print(f"Running {len(cases)} engine eval cases...\n")

    for case in cases:
        ok, msg = run_case(case)
        print(msg)
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n{'=' * 40}")
    print(f"Results: {passed} passed, {failed} failed, {len(cases)} total")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

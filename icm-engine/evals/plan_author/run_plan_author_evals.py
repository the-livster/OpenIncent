#!/usr/bin/env python3
"""Regression eval suite for plan-from-text generation.

Usage:
    ICM_RUN_EVALS=1 uv run python evals/plan_author/run_plan_author_evals.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from icm_engine.ai.plan_author import generate_plan_from_text  # noqa: E402
from icm_engine.exceptions import MissingAPIKeyError, PlanGenerationError  # noqa: E402

CASES_PATH = Path(__file__).resolve().parent / "cases.yaml"


def load_cases() -> list[dict[str, Any]]:
    with open(CASES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_case(case: dict[str, Any]) -> tuple[bool, str]:
    name = case["name"]
    description = case["description"]
    expected_count = case["expected_rule_count"]
    expected_types = case["expected_rule_types"]
    must_contain = case.get("must_contain_filters", [])

    try:
        plan = generate_plan_from_text(description)
    except (MissingAPIKeyError, PlanGenerationError) as e:
        return False, f"Error: {e}"

    if len(plan.rules) != expected_count:
        return (
            False,
            f"Rule count mismatch: expected {expected_count}, got {len(plan.rules)}",
        )

    actual_types = [r.type for r in plan.rules]
    if actual_types != expected_types:
        return False, f"Rule types mismatch: expected {expected_types}, got {actual_types}"

    filters = [r.filter or "" for r in plan.rules if hasattr(r, "filter")]
    for needle in must_contain:
        if not any(needle in f for f in filters):
            return False, f"Missing filter substring '{needle}' in filters: {filters}"

    return True, "OK"


def main() -> None:
    if os.getenv("ICM_RUN_EVALS") != "1":
        print("ERROR: Set ICM_RUN_EVALS=1 to run the eval suite (makes live API calls).")
        sys.exit(1)

    cases = load_cases()
    print(f"Running {len(cases)} eval case(s)...\n")

    passed = 0
    failed = 0
    results: list[tuple[str, bool, str]] = []

    for case in cases:
        ok, msg = run_case(case)
        status = "PASS" if ok else "FAIL"
        results.append((case["name"], ok, msg))
        print(f"  {status}  {case['name']}: {msg}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n{'='*50}")
    print(f"Summary: {passed} passed, {failed} failed, {len(cases)} total")

    if failed > 0:
        print(f"\nFailed cases:")
        for name, ok, msg in results:
            if not ok:
                print(f"  - {name}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()

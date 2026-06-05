from __future__ import annotations

import os
from decimal import Decimal
from unittest import mock

import pytest
from typer.testing import CliRunner

from icm_engine.ai.plan_author import (
    _MAX_ATTEMPTS,
    _build_prompt,
    _extract_yaml,
    _validate,
    generate_plan_from_text,
)
from icm_engine.cli import app
from icm_engine.exceptions import MissingAPIKeyError, PlanGenerationError
from icm_engine.models import FlatRateRule, Plan

runner = CliRunner()

VALID_YAML = """\
plan_id: test_plan
name: "Test Plan"
period_type: monthly
currency: USD
rules:
  - id: R-001
    type: flat_rate
    rate: "0.05"
"""

VALID_YAML_TIERED = """\
plan_id: tiered_plan
name: "Tiered Plan"
period_type: monthly
currency: USD
rules:
  - id: R-001
    type: tiered
    tiers:
      - threshold_pct: "1.0"
        rate: "0.05"
      - threshold_pct: "100.0"
        rate: "0.10"
  - id: R-002
    type: accelerator
    rate: "0.05"
    threshold_pct: "1.0"
    multiplier: "2.0"
"""

INVALID_YAML = "plan_id: [this, is, not, valid]"


def _mock_response(text: str) -> mock.MagicMock:
    content = mock.MagicMock()
    content.text = text
    resp = mock.MagicMock()
    resp.content = [content]
    return resp


def _mock_client(responses: list[str]) -> mock.MagicMock:
    client = mock.MagicMock()
    client.messages.create.side_effect = [_mock_response(r) for r in responses]
    return client


# --- _extract_yaml --------------------------------------------------------


class TestExtractYaml:
    def test_plain_yaml(self) -> None:
        assert _extract_yaml("plan_id: x\n") == "plan_id: x"

    def test_fenced_yaml(self) -> None:
        assert _extract_yaml("```yaml\nplan_id: x\n```") == "plan_id: x"

    def test_fenced_no_lang(self) -> None:
        assert _extract_yaml("```\nplan_id: x\n```") == "plan_id: x"

    def test_whitespace_stripped(self) -> None:
        assert _extract_yaml("  \n  plan_id: x  \n  ") == "plan_id: x"


# --- _validate -------------------------------------------------------------


class TestValidate:
    def test_valid_plan(self) -> None:
        plan = _validate(VALID_YAML)
        assert plan.plan_id == "test_plan"
        assert plan.period_type == "monthly"
        assert len(plan.rules) == 1
        assert isinstance(plan.rules[0], FlatRateRule)

    def test_invalid_yaml_syntax(self) -> None:
        with pytest.raises(ValueError, match="YAML parse error"):
            _validate("\tbad: [indent")

    def test_not_a_dict(self) -> None:
        with pytest.raises(ValueError, match="must be a mapping"):
            _validate("- item1\n- item2\n")

    def test_missing_required_field(self) -> None:
        with pytest.raises(ValueError):
            _validate("name: No Plan ID\n")


# --- generate_plan_from_text (mocked) --------------------------------------


class TestGeneratePlanFromText:
    def test_success(self) -> None:
        with mock.patch(
            "icm_engine.ai.plan_author.Anthropic",
            return_value=_mock_client([VALID_YAML]),
        ), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            plan = generate_plan_from_text("5% on all deals")
            assert isinstance(plan, Plan)
            assert plan.plan_id == "test_plan"

    def test_retry_on_failure_then_succeeds(self) -> None:
        with mock.patch(
            "icm_engine.ai.plan_author.Anthropic",
            return_value=_mock_client([INVALID_YAML, VALID_YAML]),
        ), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            plan = generate_plan_from_text("5% on all deals")
            assert plan.plan_id == "test_plan"

    def test_three_failures_raises(self) -> None:
        with mock.patch(
            "icm_engine.ai.plan_author.Anthropic",
            return_value=_mock_client([INVALID_YAML] * _MAX_ATTEMPTS),
        ), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            with pytest.raises(PlanGenerationError) as exc:
                generate_plan_from_text("5% on all deals")
            assert exc.value.attempts == _MAX_ATTEMPTS
            assert exc.value.last_yaml == INVALID_YAML
            assert exc.value.last_error

    def test_missing_api_key(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            assert "ICM_LLM_API_KEY" not in os.environ
            assert "ANTHROPIC_API_KEY" not in os.environ
            with pytest.raises(MissingAPIKeyError):
                generate_plan_from_text("5% on all deals")

    def test_passes_plan_id(self) -> None:
        with mock.patch(
            "icm_engine.ai.plan_author.Anthropic",
            return_value=_mock_client([VALID_YAML]),
        ), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            plan = generate_plan_from_text("5% on all deals", plan_id="my_custom_id")
            assert plan.plan_id == "test_plan"

    def test_respects_icm_llm_model_env(self) -> None:
        with mock.patch(
            "icm_engine.ai.plan_author.Anthropic",
            return_value=_mock_client([VALID_YAML]),
        ), mock.patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "sk-test",
            "ICM_LLM_MODEL": "claude-opus-4-7",
        }):
            plan = generate_plan_from_text("5% on all deals")
            assert isinstance(plan, Plan)

    def test_respects_icm_llm_api_key_env(self) -> None:
        with mock.patch(
            "icm_engine.ai.plan_author.Anthropic",
            return_value=_mock_client([VALID_YAML]),
        ), mock.patch.dict(os.environ, {
            "ICM_LLM_API_KEY": "sk-new-key",
        }):
            plan = generate_plan_from_text("5% on all deals")
            assert isinstance(plan, Plan)

    def test_explicit_api_key_overrides_env(self) -> None:
        with mock.patch(
            "icm_engine.ai.plan_author.Anthropic",
            return_value=_mock_client([VALID_YAML]),
        ):
            plan = generate_plan_from_text("5% on all deals", api_key="sk-explicit")
            assert isinstance(plan, Plan)

    def test_openai_compatible_path(self) -> None:
        """When base_url is set, _call_openai_compatible is used instead of Anthropic."""
        with mock.patch(
            "icm_engine.ai.plan_author._call_openai_compatible",
            return_value=VALID_YAML,
        ) as mock_call:
            plan = generate_plan_from_text(
                "5% on all deals",
                api_key="sk-test",
                base_url="https://api.openai.com",
            )
            assert isinstance(plan, Plan)
            mock_call.assert_called_once()
            # Verify user message passed through (second positional arg)
            args, _ = mock_call.call_args
            assert "5% on all deals" in args[1]

    def test_explicit_base_url_overrides_env(self) -> None:
        """Explicit base_url param takes precedence over env ICM_LLM_BASE_URL."""
        with mock.patch(
            "icm_engine.ai.plan_author._call_openai_compatible",
            return_value=VALID_YAML,
        ) as mock_call, mock.patch.dict(os.environ, {
            "ICM_LLM_BASE_URL": "https://ignored.example.com",
        }):
            generate_plan_from_text(
                "5% on all deals",
                api_key="sk-test",
                base_url="https://api.openai.com",
            )
            _, kwargs = mock_call.call_args
            assert "openai.com" in kwargs["base_url"]


# --- Schema in prompt ------------------------------------------------------


class TestPromptSchema:
    def test_prompt_includes_schema(self) -> None:
        system, user = _build_prompt("5% on deals")
        combined = system + user
        assert "FlatRateRule" in combined
        assert "TieredRule" in combined
        assert "AcceleratorRule" in combined
        assert "plan_id" in combined
        assert "period_type" in combined

    def test_prompt_includes_examples(self) -> None:
        system, user = _build_prompt("5% on deals")
        combined = system + user
        assert "Example 1" in combined
        assert "Example 2" in combined
        assert "flat_standard" in combined

    def test_prompt_includes_hard_requirements(self) -> None:
        system, user = _build_prompt("5% on deals")
        combined = system + user
        assert "quoted strings" in combined
        assert "ISO 4217" in combined
        assert "R-NNN" in combined

    def test_prompt_ends_with_output_instruction(self) -> None:
        system, user = _build_prompt("5% on deals")
        combined = system + user
        assert "Output ONLY the YAML" in combined


# --- CLI -------------------------------------------------------------------


class TestCLIPlanFromText:
    def test_success_outputs_yaml(self, tmp_path) -> None:
        with mock.patch(
            "icm_engine.ai.plan_author.generate_plan_from_text",
            return_value=Plan(
                plan_id="gen",
                name="Generated",
                period_type="monthly",
                currency="USD",
                rules=[FlatRateRule(type="flat_rate", id="R-001", rate=Decimal("0.05"))],
            ),
        ):
            result = runner.invoke(
                app,
                ["plan-from-text", "5% on all deals"],
            )
            assert result.exit_code == 0
            assert "R-001" in result.stdout

    def test_success_writes_file(self, tmp_path) -> None:
        out = tmp_path / "plan.yaml"
        with mock.patch(
            "icm_engine.ai.plan_author.generate_plan_from_text",
            return_value=Plan(
                plan_id="gen",
                name="Generated",
                period_type="monthly",
                currency="USD",
                rules=[FlatRateRule(type="flat_rate", id="R-001", rate=Decimal("0.05"))],
            ),
        ):
            result = runner.invoke(
                app,
                ["plan-from-text", "5% on all deals", "--output", str(out)],
            )
            assert result.exit_code == 0
            assert out.exists()
            assert "R-001" in out.read_text()

    def test_missing_api_key_exits(self) -> None:
        with mock.patch(
            "icm_engine.ai.plan_author.generate_plan_from_text",
            side_effect=MissingAPIKeyError(),
        ):
            result = runner.invoke(
                app,
                ["plan-from-text", "5% on all deals"],
            )
            assert result.exit_code != 0

    def test_plan_generation_error_shows_details(self) -> None:
        with mock.patch(
            "icm_engine.ai.plan_author.generate_plan_from_text",
            side_effect=PlanGenerationError(
                last_yaml="bad yaml",
                last_error="missing plan_id",
                attempts=3,
            ),
        ):
            result = runner.invoke(
                app,
                ["plan-from-text", "5% on all deals"],
            )
            assert result.exit_code != 0
            assert "bad yaml" in result.stdout
            assert "missing plan_id" in result.stdout

    def test_cli_passes_api_key_and_base_url(self) -> None:
        with mock.patch(
            "icm_engine.ai.plan_author.generate_plan_from_text",
            return_value=Plan(
                plan_id="gen",
                name="Generated",
                period_type="monthly",
                currency="USD",
                rules=[FlatRateRule(type="flat_rate", id="R-001", rate=Decimal("0.05"))],
            ),
        ) as mock_gen:
            runner.invoke(app, [
                "plan-from-text", "5% on all deals",
                "--api-key", "sk-cli-key",
                "--api-base-url", "https://api.groq.com",
            ])
            mock_gen.assert_called_once()
            _, kwargs = mock_gen.call_args
            assert kwargs["api_key"] == "sk-cli-key"
            assert kwargs["base_url"] == "https://api.groq.com"


# --- Live test (opt-in) ----------------------------------------------------


@pytest.mark.live
def test_live_generate_plan_from_text() -> None:
    if os.getenv("ICM_RUN_LIVE_LLM_TESTS") != "1":
        pytest.skip("ICM_RUN_LIVE_LLM_TESTS=1 not set")

    plan = generate_plan_from_text("5% flat commission on all closed deals")
    assert isinstance(plan, Plan)
    assert len(plan.rules) >= 1

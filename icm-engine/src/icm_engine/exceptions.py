from __future__ import annotations


class PlanGenerationError(Exception):
    """Raised when the LLM fails to produce a valid plan after max attempts."""

    def __init__(self, last_yaml: str, last_error: str, attempts: int) -> None:
        self.last_yaml = last_yaml
        self.last_error = last_error
        self.attempts = attempts
        super().__init__(
            f"Plan generation failed after {attempts} attempts.\n"
            f"Last validation error: {last_error}\n"
            f"Last generated YAML:\n{last_yaml}"
        )


class MissingAPIKeyError(Exception):
    """Raised when no LLM API key is available."""

    def __init__(self) -> None:
        super().__init__(
            "No LLM API key found. Set one of:\n"
            "  ICM_LLM_API_KEY    (for any OpenAI-compatible provider)\n"
            "  ANTHROPIC_API_KEY  (for Anthropic, legacy)\n"
            "Or pass --api-key on the CLI."
        )


class ReversalError(ValueError):
    """Negative lines a tiered or accelerator rule cannot price.

    Those rules pay a deal by where it lands in the payee's attainment, so a
    reversal is only priced correctly from a position that still holds the
    deal it reverses. Anything else would come back at whatever rate the
    current period happens to be at, so the run stops instead.
    """

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("\n".join(f"- {p}" for p in problems))

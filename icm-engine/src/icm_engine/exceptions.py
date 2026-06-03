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
    """Raised when ANTHROPIC_API_KEY is not set."""

    def __init__(self) -> None:
        super().__init__(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Set it with: export ANTHROPIC_API_KEY=sk-ant-..."
        )

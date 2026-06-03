from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


class LedgerEntry:
    """An auditable, immutable record of a single decision point in the engine."""

    def __init__(
        self,
        *,
        transaction_id: str,
        payee_id: str,
        rule_id: str,
        event_type: str,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        human_readable: str = "",
    ) -> None:
        self.event_type = event_type
        self.timestamp = datetime.now(UTC)
        self.transaction_id = transaction_id
        self.payee_id = payee_id
        self.rule_id = rule_id
        self.inputs = inputs or {}
        self.outputs = outputs or {}
        self.human_readable = human_readable

    def to_dict(self) -> dict[str, Any]:
        def _convert(v: Any) -> Any:
            if isinstance(v, Decimal):
                return str(v)
            if isinstance(v, datetime):
                return v.isoformat()
            return v

        return {
            "timestamp": _convert(self.timestamp),
            "transaction_id": self.transaction_id,
            "payee_id": self.payee_id,
            "rule_id": self.rule_id,
            "event_type": self.event_type,
            "inputs": {k: _convert(v) for k, v in self.inputs.items()},
            "outputs": {k: _convert(v) for k, v in self.outputs.items()},
            "human_readable": self.human_readable,
        }

    def __repr__(self) -> str:
        return (
            f"LedgerEntry({self.event_type}, txn={self.transaction_id}, "
            f"rule={self.rule_id})"
        )


def write_ledger_jsonl(entries: list[LedgerEntry], path: str | Path) -> None:
    """Write ledger entries to a JSONL file."""
    p = Path(path)
    with p.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

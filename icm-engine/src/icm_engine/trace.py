"""Dispute-resolution trace — derived entirely from the audit ledger.

Hard rule: ledger-grounded only. Never recompute, estimate, or forecast.
If a value is not in the ledger, it does not appear in the trace.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class TraceStep:
    rule_id: str
    status: str          # "matched" | "skipped"
    reason: str | None   # for skipped: the reason from the ledger entry
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class OrderTrace:
    transaction_id: str
    payee_id: str
    order: dict[str, Any]   # header fields from Transaction if given, else {}
    steps: list[TraceStep]  # one per rule that touched this order, in ledger order
    total: Decimal          # sum of commission_computed outputs
    summary: str            # one plain-language sentence


def build_order_trace(
    transaction_id: str,
    payee_id: str,
    ledger_entries: Any,
    transaction: Any = None,
) -> OrderTrace:
    """Build a read-only trace from ledger entries for a single order+payee.

    Args:
        transaction_id: The transaction to trace.
        payee_id: The payee to trace.
        ledger_entries: Iterable of LedgerEntry objects or dicts (from to_dict()/DB).
        transaction: Optional Transaction object for header context.
    """
    # Normalize to dicts
    entries: list[dict[str, Any]] = []
    for e in ledger_entries:
        if hasattr(e, "to_dict"):
            entries.append(e.to_dict())
        elif isinstance(e, dict):
            entries.append(e)
        else:
            entries.append({"transaction_id": getattr(e, "transaction_id", ""),
                           "payee_id": getattr(e, "payee_id", ""),
                           "rule_id": getattr(e, "rule_id", ""),
                           "event_type": getattr(e, "event_type", ""),
                           "inputs": getattr(e, "inputs", {}),
                           "outputs": getattr(e, "outputs", {}),
                           "human_readable": getattr(e, "human_readable", "")})

    # Filter to this transaction+payee, exclude wildcard entries and meta-entries
    relevant = [
        e for e in entries
        if e.get("transaction_id") == transaction_id
        and e.get("payee_id") == payee_id
        and e.get("transaction_id") != "*"
        and e.get("rule_id") != "*"
    ]

    # Group by rule_id, preserving order
    seen_rules: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in relevant:
        rid = e.get("rule_id", "")
        if rid not in seen_rules:
            seen_rules.append(rid)
        grouped[rid].append(e)

    # Build steps
    steps: list[TraceStep] = []
    total = Decimal("0")

    for rid in seen_rules:
        events = grouped[rid]
        has_commission = any(e.get("event_type") == "commission_computed" for e in events)

        if has_commission:
            reason = None
            for e in events:
                if e.get("event_type") == "commission_computed":
                    outputs = e.get("outputs", {})
                    amount_str = outputs.get("commission_amount", "0")
                    total += Decimal(str(amount_str))
            steps.append(TraceStep(
                rule_id=rid,
                status="matched",
                reason=None,
                events=[_clean_event(e) for e in events],
            ))
        else:
            reason = None
            for e in events:
                if e.get("event_type") == "rule_skipped":
                    reason = e.get("inputs", {}).get("reason", "unknown")
                    break
            steps.append(TraceStep(
                rule_id=rid,
                status="skipped",
                reason=reason,
                events=[_clean_event(e) for e in events],
            ))

    # Build header
    order: dict[str, Any] = {}
    if transaction is not None:
        for field in ("amount", "product", "close_date", "period"):
            val = getattr(transaction, field, None)
            if val is not None:
                order[field] = str(val) if isinstance(val, Decimal) else val

    # Build summary
    currency = "$"  # simple default
    if steps:
        matched = sum(1 for s in steps if s.status == "matched")
        skipped = sum(1 for s in steps if s.status == "skipped")
        summary = (
            f"Transaction {transaction_id} for payee {payee_id}: "
            f"{matched} rule(s) matched, {skipped} skipped. "
            f"Total commission: {currency}{total}"
        )
    else:
        summary = f"No ledger entries found for {transaction_id}/{payee_id}."

    return OrderTrace(
        transaction_id=transaction_id,
        payee_id=payee_id,
        order=order,
        steps=steps,
        total=total,
        summary=summary,
    )


def _clean_event(e: dict[str, Any]) -> dict[str, Any]:
    """Return a minimal event dict with the fields relevant to a trace."""
    return {
        "event_type": e.get("event_type", ""),
        "human_readable": e.get("human_readable", ""),
        "inputs": e.get("inputs", {}),
        "outputs": e.get("outputs", {}),
    }

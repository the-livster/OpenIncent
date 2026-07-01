"""Shared run orchestration used by both CLI and API.

Extracts the duplicated logic around locked-period detection, prior-commission
loading, draw-balance management, engine invocation, and database persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from decimal import Decimal
from typing import Any

from icm_engine.database import Database
from icm_engine.engine import CalculationResult, CommissionEngine
from icm_engine.models import Commission, Payee, Plan, Transaction


@dataclass
class RunContext:
    """All inputs needed to execute a commission run, including locked-period state."""

    plan_library: dict[str, Plan]
    transactions: list[Transaction]
    payees: list[Payee]
    db: Database
    single_plan_mode: bool = False
    effective_period: str | None = None
    allow_recalculate_locked: bool = True
    adjustments: list[Any] | None = None
    mbos: list[Any] | None = None
    using_saved_roster: bool = False

    # Computed during resolve()
    multi_plan: bool = False
    locked_by_plan: dict[str, set[str]] = field(default_factory=dict)
    locked_periods: set[str] = field(default_factory=set)
    locked_relevant: set[str] = field(default_factory=set)
    prior_commissions: list[Commission] = field(default_factory=list)
    prior_by_plan: dict[str, list[Commission]] = field(default_factory=dict)
    prior_draw_balances: dict[str, Decimal] = field(default_factory=dict)

    def resolve(self) -> None:
        """Compute locked-period state, effective period, and prior commissions."""
        self.multi_plan = len(self.plan_library) > 1 or (
            not self.single_plan_mode
            and any(p.plan_id != list(self.plan_library.keys())[0] for p in self.payees)
        )

        # --- Detect locked periods ---
        for pid in self.plan_library:
            period_status = self.db.get_period_status(pid)
            lp = {r["period"] for r in period_status if r.get("locked_calc_id") is not None}
            if lp:
                self.locked_by_plan[pid] = lp
                self.locked_periods |= lp

        txn_periods = {t.period for t in self.transactions}
        self.locked_relevant = txn_periods & self.locked_periods

        if self.locked_relevant:
            if not self.allow_recalculate_locked:
                raise LockedPeriodError(sorted(self.locked_relevant))
            if self.effective_period is None:
                self.effective_period = _date.today().strftime("%Y-%m")

        # --- Load prior commissions for locked periods ---
        if self.locked_relevant:
            for pid in self.plan_library:
                plan_priors: list[Commission] = []
                for period in self.locked_by_plan.get(pid, set()):
                    official = self.db.get_official_calculation(pid, period)
                    if official:
                        lines = self.db.get_commission_lines(official["id"])
                        for line in lines:
                            plan_priors.append(Commission(
                                transaction_id=line["transaction_id"],
                                payee_id=line["payee_id"],
                                period=line["period"],
                                origin_period=line.get("origin_period", ""),
                                rule_id=line["rule_id"],
                                base_amount=Decimal(line["base_amount"]),
                                rate=Decimal(line["rate"]),
                                commission_amount=Decimal(line["commission_amount"]),
                                notes=line.get("notes", ""),
                            ))
                if plan_priors:
                    self.prior_by_plan[pid] = plan_priors
                    self.prior_commissions.extend(plan_priors)

        # --- Load prior draw balances ---
        # The prior balance must be the balance BEFORE this run's first period,
        # not the latest row — the latest already includes any recovery from a
        # previous run of these same periods, so using it would double-recover
        # on every re-run. Fall back to the legacy single-row balance only when
        # no period-stamped history exists (pre-upgrade databases).
        first_period = min(txn_periods) if txn_periods else None
        for p in self.payees:
            plan_id = p.plan_id
            if plan_id:
                bal = None
                if first_period:
                    bal = self.db.get_draw_balance_before(p.id, plan_id, first_period)
                if bal is None:
                    bal = self.db.get_draw_balance(p.id, plan_id)
                if bal != Decimal("0"):
                    self.prior_draw_balances[p.id] = bal


class LockedPeriodError(Exception):
    """Raised when locked periods are present and recalculation is not allowed."""

    def __init__(self, locked_periods: list[str]) -> None:
        self.locked_periods = locked_periods
        super().__init__(
            f"Some periods are locked: {locked_periods}. "
            f"Set allow_recalculate_locked=true to proceed."
        )


def execute(ctx: RunContext) -> CalculationResult:
    """Run the commission engine against the resolved context.

    Does NOT persist — call persist() separately so callers can inspect
    the result before committing.
    """
    engine = CommissionEngine()

    if ctx.multi_plan:
        return engine.calculate_run(
            ctx.plan_library, ctx.transactions, ctx.payees,
            locked_periods=ctx.locked_by_plan if ctx.locked_by_plan else None,
            effective_period=ctx.effective_period if ctx.locked_relevant else None,
            prior_commissions=ctx.prior_by_plan if ctx.prior_by_plan else None,
            prior_draw_balances=ctx.prior_draw_balances if ctx.prior_draw_balances else None,
            adjustments=ctx.adjustments,
            mbos=ctx.mbos,
        )
    else:
        plan_obj = list(ctx.plan_library.values())[0]
        return engine.calculate(
            plan_obj, ctx.transactions, ctx.payees,
            locked_periods=ctx.locked_relevant if ctx.locked_relevant else None,
            effective_period=ctx.effective_period if ctx.locked_relevant else None,
            prior_commissions=ctx.prior_commissions if ctx.prior_commissions else None,
            prior_draw_balances=ctx.prior_draw_balances if ctx.prior_draw_balances else None,
            adjustments=ctx.adjustments,
            mbos=ctx.mbos,
        )


def persist(
    ctx: RunContext,
    result: CalculationResult,
) -> dict[str, str]:
    """Persist commission lines, ledger entries, transactions, and draw balances.

    Returns a dict mapping period_key -> calculation_id.
    """
    payee_plan = {p.id: p.plan_id for p in ctx.payees}
    commissions = [c.model_dump() for c in result.commissions]
    ledger_dicts = [e.to_dict() for e in result.ledger]

    # Group by period
    by_period: dict[str, list[dict[str, Any]]] = {}
    for c_dict in commissions:
        p = c_dict["period"]
        by_period.setdefault(p, []).append(c_dict)

    # Determine which plan each period's lines belong to
    period_plan: dict[str, str] = {}
    for c_dict in commissions:
        p = c_dict["period"]
        pid = payee_plan.get(c_dict["payee_id"], list(ctx.plan_library.keys())[0])
        if p not in period_plan:
            period_plan[p] = pid

    calc_ids: dict[str, str] = {}
    for period_key, comms in sorted(by_period.items()):
        plan_for_period = period_plan.get(period_key, list(ctx.plan_library.keys())[0])
        calc_id = ctx.db.record_calculation(
            plan_for_period,
            period=period_key,
            input_summary={
                "txn_count": len(ctx.transactions),
                "payee_count": len(ctx.payees),
            },
        )
        ctx.db.save_commission_lines(calc_id, comms)
        ctx.db.save_ledger_entries(calc_id, ledger_dicts)
        calc_ids[period_key] = calc_id

    # Persist transactions and link to all calculations
    txn_dicts = [t.model_dump() for t in ctx.transactions]
    ctx.db.save_transactions(txn_dicts)
    all_txn_ids = [t.id for t in ctx.transactions]
    for cid in calc_ids.values():
        ctx.db.link_transactions(cid, all_txn_ids)

    # Persist draw balances. Skip when this run recalculates over locked periods
    # (a draft / what-if): draft recalcs must not overwrite the official
    # recoverable-draw state that finalized runs depend on.
    # The legacy single row keeps the latest balance (display/API); the
    # period-stamped history rows are what make re-runs idempotent.
    if not ctx.locked_relevant:
        by_period = result.draw_balances_by_period
        for payee_id, balance in result.draw_balances.items():
            plan_id = payee_plan.get(payee_id, "")
            if plan_id:
                ctx.db.set_draw_balance(payee_id, plan_id, balance)
                for period, bal in sorted(by_period.get(payee_id, {}).items()):
                    ctx.db.set_draw_balance_asof(payee_id, plan_id, period, bal)

    return calc_ids

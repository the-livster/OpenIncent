from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from icm_engine.filter_parser import compile_filter
from icm_engine.formula import FormulaEvalError, compile_formula
from icm_engine.ledger import LedgerEntry
from icm_engine.models import (
    AcceleratorRule,
    Commission,
    Credit,
    FlatRateRule,
    FormulaRule,
    Payee,
    Plan,
    Tier,
    TieredRule,
    Transaction,
)

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


_MAX_DATE = date(9999, 12, 31)


def _sort_key(txn: Transaction) -> tuple[int, date]:
    """Sort key: undated transactions sort after all dated ones, stable."""
    if txn.close_date is None:
        return (1, _MAX_DATE)
    return (0, txn.close_date)


def _window_key(period: str, period_type: str) -> str:
    """Map a YYYY-MM period to a grouping window key.

    monthly:   "2026-02" → "2026-02"  (unchanged)
    quarterly: months 01-03 → "YYYY-Q1", 04-06 → "Q2", 07-09 → "Q3", 10-12 → "Q4"
    annual:    "2026-03" → "2026" (entire year as one window)

    This is the SEAM for future windowing modes (cumulative, YTD, custom).
    Attainment resets each window (v1 design). Cumulative/year-to-date is a
    possible future mode — route any such change through this function.
    """
    if period_type == "monthly":
        return period
    if period_type == "quarterly":
        year, month = period.split("-")
        m = int(month)
        q = (m - 1) // 3 + 1
        return f"{year}-Q{q}"
    if period_type == "annual":
        return period.split("-")[0]
    return period  # unknown period_type — pass through unchanged


def _window_date_range(window_key: str, period_type: str) -> tuple[date, date]:
    """Return (start_date, end_date_inclusive) for a window key."""
    if period_type == "monthly":
        y, m = window_key.split("-")
        start = date(int(y), int(m), 1)
        if int(m) == 12:
            end = date(int(y), 12, 31)
        else:
            end = date(int(y), int(m) + 1, 1) - timedelta(days=1)
        return start, end
    if period_type == "quarterly":
        y_str, q_str = window_key.split("-Q")
        y, q = int(y_str), int(q_str)
        start_month = (q - 1) * 3 + 1
        start = date(y, start_month, 1)
        end_month = start_month + 2
        if end_month == 12:
            end = date(y, 12, 31)
        else:
            end = date(y, end_month + 1, 1) - timedelta(days=1)
        return start, end
    if period_type == "annual":
        y = int(window_key)
        return date(y, 1, 1), date(y, 12, 31)
    # Unknown — return a zero-length range
    today = date.today()
    return today, today


def _compute_activity_fraction(
    payee: Payee,
    window_key: str,
    period_type: str,
    pro_rating: str,
) -> Decimal:
    """Return the fraction (0.0–1.0) of the window the payee was active.

    full:  always 1.0 (no pro-rating)
    daily: active_days / days_in_period
    zero:  1.0 if active the entire period, 0.0 otherwise
    """
    if pro_rating == "full":
        return Decimal("1")

    win_start, win_end = _window_date_range(window_key, period_type)
    total_days = (win_end - win_start).days + 1  # inclusive

    eff_from = payee.effective_from or win_start
    eff_to = payee.effective_to or win_end

    # Clamp to window
    active_start = max(win_start, eff_from)
    active_end = min(win_end, eff_to)

    if active_start > active_end:
        return Decimal("0")  # not active at all in this window

    active_days = (active_end - active_start).days + 1

    if pro_rating == "zero":
        # Payee must be active the entire period
        return Decimal("1") if active_days >= total_days else Decimal("0")

    # daily
    return Decimal(active_days) / Decimal(total_days)


@dataclass
class _CreditUnit:
    """A resolved credit: one payee's share of a deal, ready for rule evaluation."""
    transaction_id: str
    payee_id: str
    credited_amount: Decimal
    split_pct: Decimal
    kind: str
    period: str
    product: str | None
    close_date: date | None
    metadata: dict[str, Any]
    credited_margin: Decimal | None = None  # margin after splits; None if no margin data
    credited_quota: Decimal | None = None   # attainment value after splits; None → use credited_amount


def _resolve_credits(transactions: list[Transaction]) -> list[_CreditUnit]:
    """Expand transactions with credits into credit units."""
    units: list[_CreditUnit] = []
    for txn in transactions:
        credits = txn.credits
        if not credits:
            credits = [Credit(payee_id=txn.payee_id, split_pct=Decimal("1"), kind="split")]
        mv = txn.margin_value
        qv = txn.quota_value
        for c in credits:
            units.append(_CreditUnit(
                transaction_id=txn.id,
                payee_id=c.payee_id,
                credited_amount=txn.amount * c.split_pct,
                split_pct=c.split_pct,
                kind=c.kind,
                period=txn.period,
                product=txn.product,
                close_date=txn.close_date,
                metadata=txn.metadata,
                credited_margin=(mv * c.split_pct) if mv is not None else None,
                credited_quota=qv * c.split_pct,
            ))
    return units


_HIERARCHY_MAX_DEPTH = 10


def _resolve_hierarchy_credits(
    credits: list[_CreditUnit],
    payee_map: dict[str, Payee],
) -> list[_CreditUnit]:
    """Generate manager overlay credits by walking each payee's reporting chain.

    For each credit unit, if the payee has a manager with a manager_override
    rate > 0, an overlay credit is generated for the manager. The walk
    continues up the chain (manager's manager, etc.) up to _HIERARCHY_MAX_DEPTH.

    The manager's overlay amount = credited_amount × manager_override.
    The overlay is tagged kind="manager_override" with split_pct equal to
    the override rate, so it is additive and does not affect split-total
    validation.

    Cycle detection: if a payee appears twice in a chain, the walk stops.
    """
    result: list[_CreditUnit] = list(credits)

    for cu in credits:
        seen: set[str] = {cu.payee_id}
        current_payee_id = cu.payee_id
        depth = 0

        while depth < _HIERARCHY_MAX_DEPTH:
            payee = payee_map.get(current_payee_id)
            if payee is None:
                break
            manager_id = (payee.manager_id or "").strip()
            if not manager_id:
                break
            override = payee.manager_override
            if override is None or override <= 0:
                break
            if manager_id in seen:
                break  # cycle detected

            manager_amount = cu.credited_amount * override
            manager_margin = (cu.credited_margin * override) if cu.credited_margin is not None else None
            manager_quota = (cu.credited_quota * override) if cu.credited_quota is not None else None
            result.append(_CreditUnit(
                transaction_id=cu.transaction_id,
                payee_id=manager_id,
                credited_amount=manager_amount,
                split_pct=override,
                kind="manager_override",
                period=cu.period,
                product=cu.product,
                close_date=cu.close_date,
                metadata={**cu.metadata, "_hierarchy_depth": str(depth + 1)},
                credited_margin=manager_margin,
                credited_quota=manager_quota,
            ))

            seen.add(manager_id)
            current_payee_id = manager_id
            depth += 1

    return result


def _compute_attainment(
    credits: list[_CreditUnit],
    payee_map: dict[str, Payee],
    period_type: str,
    fractions: dict[tuple[str, str], Decimal] | None = None,
    use_margin: bool = False,
) -> list[AttainmentSummary]:
    """Compute bookings vs quota per (payee, window).

    When payees share a team_id, their bookings and quotas are pooled:
    every team member sees the TEAM's combined bookings and quota, and
    all share the same attainment %. Solo payees (no team_id) use their
    own individual numbers.

    When use_margin is True, bookings accumulate credited MARGIN (gross profit)
    instead of credited amount — used to gate margin-based rules. The quota is
    unchanged (a margin plan declares its quota in GP terms).
    """
    frac = fractions or {}

    # Per-payee bookings and quotas
    bookings: dict[tuple[str, str], Decimal] = {}
    quotas: dict[tuple[str, str], Decimal] = {}
    for cu in credits:
        window = _window_key(cu.period, period_type)
        key = (cu.payee_id, window)
        if use_margin:
            val = cu.credited_margin or Decimal("0")
        else:
            val = cu.credited_quota if cu.credited_quota is not None else cu.credited_amount
        bookings[key] = bookings.get(key, Decimal("0")) + val
        if key not in quotas:
            p = payee_map.get(cu.payee_id)
            af = frac.get(key, Decimal("1"))
            quotas[key] = p.quota_for(window, activity_fraction=af) if p else Decimal("0")

    # Identify teams and collect all (payee, window) pairs
    team_by_payee: dict[str, str] = {}  # payee_id → team_id
    all_windows: set[str] = set()
    for pid in set(b[0] for b in bookings) | set(payee_map.keys()):
        p = payee_map.get(pid)
        if p and (p.team_id or "").strip():
            team_by_payee[pid] = p.team_id.strip()

    # Collect all windows that appear (from bookings, plus compute for zero-booking payees)
    for _, w in bookings:
        all_windows.add(w)

    # Compute quotas for ALL payees across all windows (even those with no bookings)
    for pid, p in payee_map.items():
        for w in all_windows:
            k = (pid, w)
            if k not in quotas:
                af = frac.get(k, Decimal("1"))
                quotas[k] = p.quota_for(w, activity_fraction=af)

    # Compute team-level aggregates
    # Build team membership: (team_id, window) → list of payee_ids
    teams: dict[tuple[str, str], list[str]] = {}
    for pid, tid in team_by_payee.items():
        for w in all_windows:
            teams.setdefault((tid, w), []).append(pid)

    team_bookings: dict[tuple[str, str], Decimal] = {}
    team_quotas: dict[tuple[str, str], Decimal] = {}
    for (tid, window), members in teams.items():
        tb = sum(bookings.get((m, window), Decimal("0")) for m in members)
        tq = sum(quotas.get((m, window), Decimal("0")) for m in members)
        team_bookings[(tid, window)] = tb
        team_quotas[(tid, window)] = tq

    summaries: list[AttainmentSummary] = []
    seen: set[tuple[str, str]] = set()
    for (pid, window) in sorted(bookings.keys()):
        tid = team_by_payee.get(pid)
        if tid:
            # Team payee: use team aggregate
            b = team_bookings.get((tid, window), Decimal("0"))
            q = team_quotas.get((tid, window), Decimal("0"))
            # Only emit one summary per (pid, window)
            if (pid, window) in seen:
                continue
            seen.add((pid, window))
        else:
            b = bookings.get((pid, window), Decimal("0"))
            q = quotas.get((pid, window), Decimal("0"))

        pct = b / q if q != 0 else None
        summaries.append(AttainmentSummary(
            payee_id=pid,
            period=window,
            bookings=b,
            quota=q,
            attainment_pct=pct,
        ))

    # Also emit attainment for payees who had no bookings but have a quota
    for pid, _p in payee_map.items():
        tid = team_by_payee.get(pid)
        if tid:
            for window in {w for (_, w) in bookings}:
                if (pid, window) not in seen:
                    seen.add((pid, window))
                    tb = team_bookings.get((tid, window), Decimal("0"))
                    tq = team_quotas.get((tid, window), Decimal("0"))
                    pct = tb / tq if tq != 0 else None
                    summaries.append(AttainmentSummary(
                        payee_id=pid,
                        period=window,
                        bookings=tb,
                        quota=tq,
                        attainment_pct=pct,
                    ))

    return summaries


def _slice_note(
    piece: Decimal, from_pct: Decimal, to_pct: Decimal, rate: Decimal, is_margin: bool = False,
) -> str:
    """Plain-English, rep-facing explanation of one tiered slice for statements."""
    what = "this deal's gross profit" if is_margin else "this deal"
    return (
        f"{piece} of {what} fell between {from_pct:.0%} and "
        f"{to_pct:.0%} of quota -> {rate:.2%}"
    )


def _to_margin_basis(
    rule: Any, synth_txns: list[Transaction], ledger: list[LedgerEntry],
) -> list[Transaction]:
    """Rebuild synthetic txns for a margin-based rule so `amount` is the credited
    margin — the value tiered/accelerator rules slice and accumulate on.

    Rows with no margin data are dropped and recorded in the ledger rather than
    aborting the whole run.
    """
    out: list[Transaction] = []
    for t in synth_txns:
        if t.margin is None:
            ledger.append(LedgerEntry(
                transaction_id=t.id,
                payee_id=t.payee_id,
                rule_id=rule.id,
                event_type="rule_skipped",
                inputs={"reason": "no_margin_data"},
                human_readable=(
                    f"Transaction {t.id} skipped by rule {rule.id}: "
                    f"base='margin' but no bill_rate/pay_rate/units or margin override"
                ),
            ))
            continue
        out.append(t.model_copy(update={"amount": t.margin}))
    return out


def _make_synthetic_transactions(credits: list[_CreditUnit]) -> list[Transaction]:
    """Convert credit units into synthetic Transactions for rule evaluation.

    Carries both amount and margin onto the synthetic transaction so that
    margin-based rules can use margin as the commission base.
    """
    return [
        Transaction(
            id=cu.transaction_id,
            payee_id=cu.payee_id,
            amount=cu.credited_amount,
            period=cu.period,
            product=cu.product,
            close_date=cu.close_date,
            metadata={**cu.metadata, "_credit_split_pct": str(cu.split_pct), "_credit_kind": cu.kind},
            margin=cu.credited_margin,
        )
        for cu in credits
    ]


def _stamp_credits(commissions: list[Commission], credits: list[_CreditUnit]) -> None:
    """Patch split_pct and kind from credit units onto Commission objects."""
    # Build lookup: (transaction_id, payee_id) -> credit unit
    lookup: dict[tuple[str, str], _CreditUnit] = {}
    for cu in credits:
        lookup[(cu.transaction_id, cu.payee_id)] = cu
    for c in commissions:
        key = (c.transaction_id, c.payee_id)
        if key in lookup:
            cu = lookup[key]
            c.split_pct = cu.split_pct
            c.kind = cu.kind


@dataclass
class AttainmentSummary:
    payee_id: str
    period: str
    bookings: Decimal
    quota: Decimal
    attainment_pct: Decimal | None  # None if quota == 0


@dataclass
class CalculationResult:
    commissions: list[Commission] = field(default_factory=list)
    ledger: list[LedgerEntry] = field(default_factory=list)
    attainment: list[AttainmentSummary] = field(default_factory=list)
    draw_balances: dict[str, Decimal] = field(default_factory=dict)
    # payee_id -> period -> recoverable-draw balance AFTER that period. Lets
    # persistence stamp balances per period, so re-running a period starts
    # from the pre-period balance instead of double-recovering.
    draw_balances_by_period: dict[str, dict[str, Decimal]] = field(default_factory=dict)


@dataclass
class Adjustment:
    """Per (payee, period) delta between prior and current commission totals."""
    payee_id: str
    period: str
    prior_amount: Decimal
    new_amount: Decimal
    delta: Decimal  # new - prior (negative = clawback)


@dataclass
class TrueUpResult:
    current: CalculationResult
    adjustments: list[Adjustment]
    exceptions: list[dict[str, Any]]
    ledger: list[LedgerEntry]


@dataclass
class CommissionDelta:
    """A per-key delta between prior and current commissions."""
    transaction_id: str
    payee_id: str
    rule_id: str
    origin_period: str
    prior_amount: Decimal
    new_amount: Decimal
    delta: Decimal  # new - prior
    is_new: bool    # no prior line
    is_removed: bool  # no current line
    # Fields for constructing a Commission object
    base_amount: Decimal
    rate: Decimal
    kind: str
    split_pct: Decimal


def _diff_commissions(
    prior: list[Commission],
    current: list[Commission],
) -> list[CommissionDelta]:
    """Diff prior vs current commissions, aggregated by (transaction_id, rule_id, payee_id).

    Returns one CommissionDelta per non-zero delta. Aggregates by key because
    tiered rules can produce multiple Commission objects per (transaction, rule, payee).
    """
    def _key(c: Commission) -> tuple[str, str, str]:
        return (c.transaction_id, c.rule_id, c.payee_id)

    # Aggregate totals by key
    prior_totals: dict[tuple[str, str, str], Decimal] = {}
    prior_refs: dict[tuple[str, str, str], Commission] = {}
    for c in prior:
        k = _key(c)
        prior_totals[k] = prior_totals.get(k, Decimal("0")) + c.commission_amount
        prior_refs[k] = c

    current_totals: dict[tuple[str, str, str], Decimal] = {}
    current_refs: dict[tuple[str, str, str], Commission] = {}
    for c in current:
        k = _key(c)
        current_totals[k] = current_totals.get(k, Decimal("0")) + c.commission_amount
        current_refs[k] = c

    all_keys = set(prior_totals.keys()) | set(current_totals.keys())

    deltas: list[CommissionDelta] = []
    for key in sorted(all_keys):
        tid, rid, pid = key
        prior_amt = prior_totals.get(key, Decimal("0"))
        new_amt = current_totals.get(key, Decimal("0"))
        delta = new_amt - prior_amt

        if delta == 0:
            continue

        # Use current as reference, fall back to prior for removed deals
        ref = current_refs.get(key) or prior_refs.get(key)
        if ref is None:
            continue

        deltas.append(CommissionDelta(
            transaction_id=tid,
            payee_id=pid,
            rule_id=rid,
            origin_period=ref.period,
            prior_amount=prior_amt,
            new_amount=new_amt,
            delta=delta,
            is_new=prior_amt == 0,
            is_removed=new_amt == 0,
            base_amount=delta,
            rate=Decimal("1"),
            kind=ref.kind,
            split_pct=ref.split_pct,
        ))

    return deltas


# ------------------------------------------------------------------
# Caps
# ------------------------------------------------------------------


def _apply_cap(
    commissions: list[Commission], cap_amount: Decimal, rule_id: str,
) -> list[Commission]:
    """Apply a per-rule cap. Sums payee-period totals and emits negative
    cap_adjustment lines for any excess.
    """
    by_pp: dict[tuple[str, str], list[Commission]] = {}
    for c in commissions:
        key = (c.payee_id, c.period)
        by_pp.setdefault(key, []).append(c)

    result: list[Commission] = []
    for (pid, period), lines in by_pp.items():
        total = sum(line.commission_amount for line in lines)
        if total > cap_amount:
            excess = total - cap_amount
            result.extend(lines)
            result.append(Commission(
                transaction_id="*",
                payee_id=pid,
                period=period,
                rule_id=rule_id,
                base_amount=-excess,
                rate=Decimal("1"),
                commission_amount=-excess,
                notes=f"cap_adjustment: {total} capped to {cap_amount}",
            ))
        else:
            result.extend(lines)
    return result


def _apply_plan_payout_cap(
    all_commissions: list[Commission],
    payout_cap: Decimal,
    all_ledger: list[LedgerEntry],
) -> list[Commission]:
    """Apply plan-level payout cap per (payee, period), emitting cap_adjustment lines."""
    by_pp: dict[tuple[str, str], list[Commission]] = {}
    for c in all_commissions:
        key = (c.payee_id, c.period)
        by_pp.setdefault(key, []).append(c)

    result: list[Commission] = []
    for (pid, period), lines in by_pp.items():
        total = sum(line.commission_amount for line in lines)
        if payout_cap is not None and total > payout_cap:
            excess = total - payout_cap
            result.extend(lines)
            cap_line = Commission(
                transaction_id="*",
                payee_id=pid,
                period=period,
                rule_id="payout_cap",
                base_amount=-excess,
                rate=Decimal("1"),
                commission_amount=-excess,
                notes=f"plan_cap_adjustment: {total} capped to {payout_cap}",
            )
            result.append(cap_line)
            all_ledger.append(LedgerEntry(
                transaction_id="*",
                payee_id=pid,
                rule_id="payout_cap",
                event_type="cap_applied",
                inputs={"total": str(total), "cap": str(payout_cap)},
                outputs={"cap_adjustment": str(-excess)},
                human_readable=f"Cap applied for {pid} {period}: {total} → {payout_cap}",
            ))
        else:
            result.extend(lines)
    return result


class CommissionEngine:
    def calculate(
        self,
        plan: Plan,
        transactions: list[Transaction],
        payees: list[Payee],
        locked_periods: set[str] | None = None,
        effective_period: str | None = None,
        prior_commissions: list[Commission] | None = None,
        adjustments: list[Any] | None = None,
        prior_draw_balances: dict[str, Decimal] | None = None,
        mbos: list[Any] | None = None,
    ) -> CalculationResult:
        payee_map = {p.id: p for p in payees}
        credits = _resolve_credits(transactions)
        credits = _resolve_hierarchy_credits(credits, payee_map)
        return self._run_plan_pipeline(
            plan, credits, payee_map,
            locked_periods=locked_periods,
            effective_period=effective_period,
            prior_commissions=prior_commissions,
            adjustments=adjustments,
            prior_draw_balances=prior_draw_balances,
            mbos=mbos,
        )

    def calculate_run(
        self,
        plans: dict[str, Plan],
        transactions: list[Transaction],
        payees: list[Payee],
        *,
        locked_periods: dict[str, set[str]] | None = None,
        effective_period: str | None = None,
        prior_commissions: dict[str, list[Commission]] | None = None,
        adjustments: list[Any] | None = None,
        prior_draw_balances: dict[str, Decimal] | None = None,
        mbos: list[Any] | None = None,
    ) -> CalculationResult:
        """Compute commissions for payees on DIFFERENT plans in a SINGLE run.

        Each payee is governed by the plan whose id == payee.plan_id. Credits
        are resolved once globally, then evaluated under each payee's plan.
        """
        payee_map = {p.id: p for p in payees}

        # Validate: every payee's plan_id must exist in the plan library
        for p in payees:
            if p.plan_id not in plans:
                raise ValueError(
                    f"Payee {p.id!r} references plan_id {p.plan_id!r}, "
                    f"which is not in the plan library. "
                    f"Available plans: {sorted(plans.keys())}"
                )

        # Resolve credits once, globally
        credits = _resolve_credits(transactions)

        # Resolve hierarchy: generate manager overlay credits
        credits = _resolve_hierarchy_credits(credits, payee_map)

        # Group credit units by each payee's plan
        plan_credits: dict[str, list[_CreditUnit]] = {pid: [] for pid in plans}
        for cu in credits:
            p = payee_map.get(cu.payee_id)
            if p is None:
                continue
            pid = p.plan_id
            if pid in plan_credits:
                plan_credits[pid].append(cu)

        # Group payees by plan
        plan_payees: dict[str, dict[str, Payee]] = {pid: {} for pid in plans}
        for p in payees:
            if p.plan_id in plan_payees:
                plan_payees[p.plan_id][p.id] = p

        # Filter MBOs and adjustments per plan (by payee)
        plan_mbos: dict[str, list[Any]] = {pid: [] for pid in plans}
        if mbos:
            for mbo in mbos:
                pid = getattr(mbo, "payee_id", "")
                p = payee_map.get(pid)
                if p and p.plan_id in plan_mbos:
                    plan_mbos[p.plan_id].append(mbo)

        plan_adj: dict[str, list[Any]] = {pid: [] for pid in plans}
        if adjustments:
            for adj in adjustments:
                pid = getattr(adj, "payee_id", "")
                p = payee_map.get(pid)
                if p and p.plan_id in plan_adj:
                    plan_adj[p.plan_id].append(adj)

        # Run each plan's pipeline independently
        all_commissions: list[Commission] = []
        all_ledger: list[LedgerEntry] = []
        all_attainment: list[AttainmentSummary] = []
        all_draw_balances: dict[str, Decimal] = {}
        all_draw_by_period: dict[str, dict[str, Decimal]] = {}

        for plan_id in sorted(plans.keys()):
            plan = plans[plan_id]
            plan_result = self._run_plan_pipeline(
                plan,
                plan_credits.get(plan_id, []),
                plan_payees.get(plan_id, {}),
                locked_periods=(locked_periods or {}).get(plan_id),
                effective_period=effective_period,
                prior_commissions=(prior_commissions or {}).get(plan_id),
                adjustments=plan_adj.get(plan_id) or None,
                prior_draw_balances=prior_draw_balances,
                mbos=plan_mbos.get(plan_id) or None,
            )
            all_commissions.extend(plan_result.commissions)
            all_ledger.extend(plan_result.ledger)
            all_attainment.extend(plan_result.attainment)
            all_draw_balances.update(plan_result.draw_balances)
            all_draw_by_period.update(plan_result.draw_balances_by_period)

        # Merge results in deterministic order
        all_commissions.sort(key=lambda c: (c.payee_id, c.rule_id, c.transaction_id))
        all_attainment.sort(key=lambda a: (a.payee_id, a.period))

        return CalculationResult(
            commissions=all_commissions,
            ledger=all_ledger,
            attainment=all_attainment,
            draw_balances=all_draw_balances,
            draw_balances_by_period=all_draw_by_period,
        )

    def _run_plan_pipeline(
        self,
        plan: Plan,
        credits: list[_CreditUnit],
        payee_map: dict[str, Payee],
        *,
        locked_periods: set[str] | None = None,
        effective_period: str | None = None,
        prior_commissions: list[Commission] | None = None,
        adjustments: list[Any] | None = None,
        prior_draw_balances: dict[str, Decimal] | None = None,
        mbos: list[Any] | None = None,
    ) -> CalculationResult:
        """Core pipeline: attainment → rule eval → MBOs → cap → draw → locking → adjustments.

        Operates on pre-resolved credit units and a single plan. Used by both
        calculate() (single-plan) and calculate_run() (multi-plan)."""
        all_commissions: list[Commission] = []
        all_ledger: list[LedgerEntry] = []
        pt = plan.period_type

        # Pre-compute activity fractions for pro-rating
        pro_rating = getattr(plan, "pro_rating", "full") or "full"
        fractions: dict[tuple[str, str], Decimal] = {}
        if pro_rating != "full":
            # Collect unique (payee, window) pairs from credits
            for cu in credits:
                w = _window_key(cu.period, pt)
                key = (cu.payee_id, w)
                if key not in fractions:
                    p = payee_map.get(cu.payee_id)
                    if p is not None:
                        fractions[key] = _compute_activity_fraction(p, w, pt, pro_rating)

        # Compute attainment: bookings per (payee, window) vs quota
        attainment = _compute_attainment(credits, payee_map, pt, fractions)
        for a in attainment:
            all_ledger.append(LedgerEntry(
                transaction_id="*",
                payee_id=a.payee_id,
                rule_id="*",
                event_type="attainment_computed",
                inputs={"bookings": str(a.bookings), "quota": str(a.quota)},
                outputs={"attainment_pct": str(a.attainment_pct) if a.attainment_pct is not None else "N/A"},
                human_readable=(
                    f"{a.payee_id} booked {a.bookings} against "
                    f"{a.quota} quota = {a.attainment_pct * 100:.1f}%"
                    if a.attainment_pct is not None
                    else f"{a.payee_id} booked {a.bookings} (quota=0)"
                ),
            ))

        # Emit credit_allocated ledger entries
        for cu in credits:
            all_ledger.append(LedgerEntry(
                transaction_id=cu.transaction_id,
                payee_id=cu.payee_id,
                rule_id="*",
                event_type="credit_allocated",
                inputs={
                    "split_pct": str(cu.split_pct),
                    "kind": cu.kind,
                    "credited_amount": str(cu.credited_amount),
                },
                human_readable=(
                    f"Credit {cu.kind}: {cu.payee_id} gets "
                    f"{cu.split_pct} of {cu.transaction_id} = {cu.credited_amount}"
                ),
            ))

        # Build team quota override map: if a payee's attainment quota differs
        # from their individual quota_for(), use the attainment (team) quota
        quota_overrides: dict[tuple[str, str], Decimal] = {}
        for a in attainment:
            p = payee_map.get(a.payee_id)
            if p:
                af_val = fractions.get((a.payee_id, a.period), Decimal("1"))
                individual = p.quota_for(a.period, activity_fraction=af_val)
                if individual != a.quota:
                    quota_overrides[(a.payee_id, a.period)] = a.quota

        # Build attainment lookup for threshold gates
        att_by_payee_window: dict[tuple[str, str], Decimal | None] = {}
        for a in attainment:
            att_by_payee_window[(a.payee_id, a.period)] = a.attainment_pct

        # Margin-based attainment lookup — only built when a margin rule gates on it.
        margin_att_by_payee_window: dict[tuple[str, str], Decimal | None] = {}
        if any(
            getattr(r, "base", "amount") == "margin" and getattr(r, "min_attainment_pct", None)
            for r in plan.rules
        ):
            for a in _compute_attainment(credits, payee_map, pt, fractions, use_margin=True):
                margin_att_by_payee_window[(a.payee_id, a.period)] = a.attainment_pct

        for rule in plan.rules:
            # Build synthetic transactions for this rule's evaluation
            synth_txns = _make_synthetic_transactions(credits)

            # Threshold gate: filter out payees below min_attainment_pct.
            # Margin-based rules gate on margin attainment.
            min_att = getattr(rule, "min_attainment_pct", None)
            if min_att is not None and min_att > 0:
                _gate_att = (
                    margin_att_by_payee_window
                    if getattr(rule, "base", "amount") == "margin"
                    else att_by_payee_window
                )
                gated_txns: list[Transaction] = []
                for t in synth_txns:
                    key = (t.payee_id, _window_key(t.period, pt))
                    att_pct = _gate_att.get(key)
                    if att_pct is None or att_pct >= min_att:
                        gated_txns.append(t)
                    else:
                        all_ledger.append(LedgerEntry(
                            transaction_id=t.id,
                            payee_id=t.payee_id,
                            rule_id=rule.id,
                            event_type="rule_skipped",
                            inputs={"filter": rule.filter or "(none)",
                                    "reason": "below_threshold_gate",
                                    "attainment_pct": str(att_pct),
                                    "min_attainment_pct": str(min_att)},
                            human_readable=(
                                f"Transaction {t.id} skipped by rule {rule.id} "
                                f"(attainment {att_pct} below gate {min_att})"
                            ),
                        ))
                synth_txns = gated_txns

            # Margin-based tiered/accelerator rules slice on credited margin:
            # swap each synthetic txn's amount to its margin (dropping no-margin rows).
            if getattr(rule, "base", "amount") == "margin" and isinstance(
                rule, (TieredRule, AcceleratorRule)
            ):
                synth_txns = _to_margin_basis(rule, synth_txns, all_ledger)

            if isinstance(rule, FlatRateRule):
                commissions, ledger = self._calc_flat_rate(
                    rule, synth_txns, payee_map, pt,
                    quota_category=getattr(rule, "quota_category", None),
                )
            elif isinstance(rule, TieredRule):
                commissions, ledger = self._calc_tiered(
                    rule, synth_txns, payee_map, pt,
                    quota_category=getattr(rule, "quota_category", None),
                    fractions=fractions,
                    quota_overrides=quota_overrides,
                )
            elif isinstance(rule, AcceleratorRule):
                commissions, ledger = self._calc_accelerator(
                    rule, synth_txns, payee_map, pt,
                    quota_category=getattr(rule, "quota_category", None),
                    fractions=fractions,
                    quota_overrides=quota_overrides,
                )
            elif isinstance(rule, FormulaRule):
                commissions, ledger = self._calc_formula(
                    rule, synth_txns, payee_map, pt,
                    quota_category=getattr(rule, "quota_category", None),
                    fractions=fractions,
                    quota_overrides=quota_overrides,
                    attainment=attainment,
                )
            else:
                continue

            # Per-rule cap
            rule_cap = getattr(rule, "cap", None)
            if rule_cap is not None and rule_cap >= 0:
                commissions = _apply_cap(commissions, rule_cap, rule.id)

            # Stamp credit metadata onto commissions
            _stamp_credits(commissions, credits)
            all_commissions.extend(commissions)
            all_ledger.extend(ledger)

        # --- MBOs / bonuses (after rules, before caps/draws) ---
        if mbos:
            for mbo in mbos:
                pid = getattr(mbo, "payee_id", "")
                period_val = getattr(mbo, "period", "")
                amt = Decimal(str(getattr(mbo, "amount", "0")))
                label = str(getattr(mbo, "label", ""))
                mbo_id = str(getattr(mbo, "id", ""))
                c = Commission(
                    transaction_id=mbo_id or f"mbo_{pid}_{period_val}",
                    payee_id=pid,
                    period=period_val,
                    rule_id="mbo",
                    base_amount=amt,
                    rate=Decimal("1"),
                    commission_amount=amt,
                    origin_period=period_val,
                    notes=label,
                )
                all_commissions.append(c)
                all_ledger.append(LedgerEntry(
                    transaction_id=c.transaction_id,
                    payee_id=pid,
                    rule_id="mbo",
                    event_type="mbo",
                    inputs={"amount": str(amt), "label": label},
                    outputs={"commission_amount": str(amt)},
                    human_readable=(
                        f"MBO {label} for {pid} {period_val}: {amt}"
                        if label else f"MBO for {pid} {period_val}: {amt}"
                    ),
                ))

        # Plan-level payout cap (per payee, per period)
        if plan.payout_cap is not None:
            all_commissions = _apply_plan_payout_cap(all_commissions, plan.payout_cap, all_ledger)

        # --- Draws / guarantees ---
        draw_balances: dict[str, Decimal] = {}
        draw_by_period: dict[str, dict[str, Decimal]] = {}
        for pid in {c.payee_id for c in all_commissions}:
            p = payee_map.get(pid)
            draw = None
            if p is not None:
                draw = getattr(p, "draw", None)
            if draw is None:
                draw = getattr(plan, "draw", None)
            if draw is None:
                continue

            draw_amt = getattr(draw, "amount", Decimal("0"))
            recoverable = bool(getattr(draw, "recoverable", False))
            prior_bal = (prior_draw_balances or {}).get(pid, Decimal("0"))

            # Sum this payee's post-cap commission per period
            by_period: dict[str, Decimal] = {}
            for c in all_commissions:
                if c.payee_id == pid:
                    by_period[c.period] = by_period.get(c.period, Decimal("0")) + c.commission_amount

            for period, earned in sorted(by_period.items()):
                # Apply activity fraction to draw amount
                af_draw = fractions.get((pid, period), Decimal("1"))
                effective_draw = draw_amt * af_draw
                if effective_draw == 0:
                    continue
                if recoverable:
                    available = max(Decimal("0"), earned - effective_draw)
                    recovered = min(prior_bal, available)
                    payout = max(earned - recovered, effective_draw)
                    new_shortfall = max(Decimal("0"), effective_draw - earned)
                    new_balance = prior_bal - recovered + new_shortfall

                    if recovered > 0:
                        all_commissions.append(Commission(
                            transaction_id="*", payee_id=pid, period=period,
                            rule_id="draw", base_amount=-recovered, rate=Decimal("1"),
                            commission_amount=-recovered,
                            notes=f"draw_recovery: recovered {recovered} toward draw {effective_draw}",
                        ))
                    if earned < effective_draw:
                        topup = effective_draw - earned
                        all_commissions.append(Commission(
                            transaction_id="*", payee_id=pid, period=period,
                            rule_id="draw", base_amount=topup, rate=Decimal("1"),
                            commission_amount=topup,
                            notes=f"draw_topup: floor {effective_draw}, earned {earned}",
                        ))

                    all_ledger.append(LedgerEntry(
                        transaction_id="*", payee_id=pid, rule_id="draw",
                        event_type="draw",
                        inputs={
                            "earned": str(earned), "draw": str(effective_draw),
                            "recovered": str(recovered), "prior_balance": str(prior_bal),
                        },
                        outputs={"payout": str(payout), "new_balance": str(new_balance)},
                        human_readable=(
                            f"Draw for {pid} {period}: earned {earned}, draw {effective_draw}, "
                            f"recovered {recovered}, balance {prior_bal}→{new_balance}"
                        ),
                    ))
                    draw_balances[pid] = new_balance
                    draw_by_period.setdefault(pid, {})[period] = new_balance
                    prior_bal = new_balance  # carry forward for next period
                else:
                    # Non-recoverable: simple floor
                    if earned < effective_draw:
                        topup = effective_draw - earned
                        all_commissions.append(Commission(
                            transaction_id="*", payee_id=pid, period=period,
                            rule_id="draw", base_amount=topup, rate=Decimal("1"),
                            commission_amount=topup,
                            notes=f"draw_topup: guarantee {effective_draw}, earned {earned}",
                        ))
                        all_ledger.append(LedgerEntry(
                            transaction_id="*", payee_id=pid, rule_id="draw",
                            event_type="draw",
                            inputs={"earned": str(earned), "draw": str(effective_draw)},
                            outputs={"topup": str(topup)},
                            human_readable=(
                                f"Draw (non-recoverable) for {pid} {period}: "
                                f"earned {earned}, topped up to {effective_draw}"
                            ),
                        ))

        # Delta-based true-up for locked periods
        if locked_periods and effective_period:
            # Retain only non-locked-period commissions at full amount
            non_locked = [c for c in all_commissions if c.period not in locked_periods]

            if prior_commissions:
                locked_current = [c for c in all_commissions if c.period in locked_periods]
                deltas = _diff_commissions(prior_commissions, locked_current)
            else:
                # Without prior, emit full amounts as new (backward compat / no lock yet)
                deltas = [
                    CommissionDelta(
                        transaction_id=c.transaction_id,
                        payee_id=c.payee_id,
                        rule_id=c.rule_id,
                        origin_period=c.period,
                        prior_amount=Decimal("0"),
                        new_amount=c.commission_amount,
                        delta=c.commission_amount,
                        is_new=True,
                        is_removed=False,
                        base_amount=c.commission_amount,
                        rate=Decimal("1"),
                        kind=c.kind,
                        split_pct=c.split_pct,
                    )
                    for c in all_commissions
                    if c.period in locked_periods
                ]

            true_up_lines: list[Commission] = []
            for d in deltas:
                notes = f"true_up: {d.prior_amount} → {d.new_amount} (delta {d.delta})"
                c = Commission(
                    transaction_id=d.transaction_id,
                    payee_id=d.payee_id,
                    period=effective_period,
                    origin_period=d.origin_period,
                    rule_id=d.rule_id,
                    base_amount=d.base_amount,
                    rate=d.rate,
                    commission_amount=d.delta,
                    kind=d.kind,
                    split_pct=d.split_pct,
                    notes=notes,
                )
                true_up_lines.append(c)
                all_ledger.append(LedgerEntry(
                    transaction_id=c.transaction_id,
                    payee_id=c.payee_id,
                    rule_id=c.rule_id,
                    event_type="true_up",
                    inputs={
                        "origin_period": c.origin_period,
                        "effective_period": effective_period,
                        "prior_amount": str(d.prior_amount),
                        "new_amount": str(d.new_amount),
                        "delta": str(d.delta),
                    },
                    outputs={"commission_amount": str(c.commission_amount)},
                    human_readable=(
                        f"True-up {c.transaction_id} from {c.origin_period} "
                        f"→ {effective_period}: {c.commission_amount} ({c.notes})"
                    ),
                ))

            all_commissions = non_locked + true_up_lines

        # --- Manual adjustments (post-locking, post-caps/draws) ---
        if adjustments:
            for adj in adjustments:
                pid = getattr(adj, "payee_id", "")
                period_val = getattr(adj, "period", "")
                amt = Decimal(str(getattr(adj, "amount", "0")))
                reason = str(getattr(adj, "reason", ""))
                adj_id = str(getattr(adj, "id", ""))
                c = Commission(
                    transaction_id=adj_id or f"adj_{pid}_{period_val}",
                    payee_id=pid,
                    period=period_val,
                    rule_id="manual_adjustment",
                    base_amount=amt,
                    rate=Decimal("1"),
                    commission_amount=amt,
                    notes=reason,
                )
                all_commissions.append(c)
                all_ledger.append(LedgerEntry(
                    transaction_id=c.transaction_id,
                    payee_id=pid,
                    rule_id="manual_adjustment",
                    event_type="manual_adjustment",
                    inputs={"amount": str(amt), "reason": reason},
                    outputs={"commission_amount": str(amt)},
                    human_readable=f"Manual adjustment for {pid} {period_val}: {amt} ({reason})",
                ))

        return CalculationResult(
            commissions=all_commissions, ledger=all_ledger,
            attainment=attainment, draw_balances=draw_balances,
            draw_balances_by_period=draw_by_period,
        )

    def true_up(
        self,
        plan: Plan,
        transactions: list[Transaction],
        payees: list[Payee],
        prior: list[Commission],
    ) -> TrueUpResult:
        """Recompute and diff against prior commissions.

        Returns adjustments (per payee/period), exceptions (only changed deals),
        and true_up ledger entries.
        """
        current = self.calculate(plan, transactions, payees)

        deltas = _diff_commissions(prior, current.commissions)

        # Aggregate adjustments by (payee, period)
        adj_map: dict[tuple[str, str], Decimal] = {}
        exceptions: list[dict[str, Any]] = []

        for d in deltas:
            pid = d.payee_id
            period = d.origin_period
            adj_key = (pid, period)
            adj_map[adj_key] = adj_map.get(adj_key, Decimal("0")) + d.delta

            status: str
            if d.is_new:
                status = "new"
            elif d.is_removed:
                status = "removed"
            else:
                status = "changed"

            exceptions.append({
                "transaction_id": d.transaction_id,
                "payee_id": pid,
                "rule_id": d.rule_id,
                "status": status,
                "prior_amount": str(d.prior_amount),
                "new_amount": str(d.new_amount),
                "delta": str(d.delta),
            })

        # Build sorted adjustments
        adjustments = sorted(
            [
                Adjustment(
                    payee_id=pid,
                    period=period,
                    prior_amount=Decimal("0"),
                    new_amount=Decimal("0"),
                    delta=delta,
                )
                for (pid, period), delta in adj_map.items()
            ],
            key=lambda a: (a.payee_id, a.period),
        )

        # Fill in prior/new totals for each adjustment
        for adj in adjustments:
            prior_total = sum(
                (c.commission_amount for c in prior
                if c.payee_id == adj.payee_id and c.period == adj.period),
                Decimal("0")
            )
            new_total = sum(
                (c.commission_amount for c in current.commissions
                if c.payee_id == adj.payee_id and c.period == adj.period),
                Decimal("0")
            )
            adj.prior_amount = prior_total
            adj.new_amount = new_total

        # True-up ledger entries
        tu_ledger: list[LedgerEntry] = []
        for adj in adjustments:
            tu_ledger.append(LedgerEntry(
                transaction_id="*",
                payee_id=adj.payee_id,
                rule_id="*",
                event_type="true_up",
                inputs={
                    "period": adj.period,
                    "prior_amount": str(adj.prior_amount),
                    "new_amount": str(adj.new_amount),
                },
                outputs={"delta": str(adj.delta)},
                human_readable=(
                    f"True-up {adj.payee_id} {adj.period}: "
                    f"{adj.prior_amount} → {adj.new_amount} "
                    f"(delta {adj.delta})"
                ),
            ))

        return TrueUpResult(
            current=current,
            adjustments=adjustments,
            exceptions=exceptions,
            ledger=tu_ledger,
        )

    # -- flat rate -----------------------------------------------------------

    def _calc_flat_rate(
        self,
        rule: FlatRateRule,
        transactions: list[Transaction],
        payee_map: dict[str, Payee],
        period_type: str = "monthly",
        quota_category: str | None = None,
    ) -> tuple[list[Commission], list[LedgerEntry]]:
        predicate = compile_filter(rule.filter)
        results: list[Commission] = []
        ledger: list[LedgerEntry] = []
        use_margin = rule.base == "margin"
        for txn in transactions:
            if not predicate(txn):
                ledger.append(
                    LedgerEntry(
                        transaction_id=txn.id,
                        payee_id=txn.payee_id,
                        rule_id=rule.id,
                        event_type="rule_skipped",
                        inputs={"filter": rule.filter or "(none)", "reason": "filter_excluded"},
                        human_readable=(
                            f"Transaction {txn.id} skipped by rule {rule.id}"
                        ),
                    )
                )
                continue

            base_amount: Decimal
            if use_margin:
                if txn.margin is None:
                    # No margin data for a margin-based rule: skip this row and
                    # record it in the ledger rather than aborting the whole run.
                    ledger.append(
                        LedgerEntry(
                            transaction_id=txn.id,
                            payee_id=txn.payee_id,
                            rule_id=rule.id,
                            event_type="rule_skipped",
                            inputs={"reason": "no_margin_data"},
                            human_readable=(
                                f"Transaction {txn.id} skipped by rule {rule.id}: "
                                f"base='margin' but no bill_rate/pay_rate/units or margin override"
                            ),
                        )
                    )
                    continue
                base_amount = txn.margin
            else:
                base_amount = txn.amount

            commission_amount = rule.rate * base_amount
            window = _window_key(txn.period, period_type)

            notes: str
            hr: str
            if use_margin:
                notes = f"Margin flat rate {rule.rate} on margin {base_amount}"
                hr = f"Flat rate on margin: {base_amount} * {rule.rate} = {commission_amount}"
            else:
                notes = f"Flat rate {rule.rate} on {txn.amount}"
                hr = f"Flat rate: {txn.amount} * {rule.rate} = {commission_amount}"

            results.append(
                Commission(
                    transaction_id=txn.id,
                    payee_id=txn.payee_id,
                    period=window,
                    rule_id=rule.id,
                    base_amount=base_amount,
                    rate=rule.rate,
                    commission_amount=commission_amount,
                    notes=notes,
                )
            )
            ledger.append(
                LedgerEntry(
                    transaction_id=txn.id,
                    payee_id=txn.payee_id,
                    rule_id=rule.id,
                    event_type="commission_computed",
                    inputs={"amount": str(base_amount), "rate": str(rule.rate)}
                           | ({"base": "margin"} if use_margin else {}),
                    outputs={"commission_amount": str(commission_amount)},
                    human_readable=hr,
                )
            )
        return results, ledger

    # -- tiered --------------------------------------------------------------

    def _calc_tiered(
        self,
        rule: TieredRule,
        transactions: list[Transaction],
        payee_map: dict[str, Payee],
        period_type: str = "monthly",
        quota_category: str | None = None,
        fractions: dict[tuple[str, str], Decimal] | None = None,
        quota_overrides: dict[tuple[str, str], Decimal] | None = None,
    ) -> tuple[list[Commission], list[LedgerEntry]]:
        predicate = compile_filter(rule.filter)
        results: list[Commission] = []
        ledger: list[LedgerEntry] = []

        matching: list[Transaction] = []
        for t in transactions:
            if not predicate(t):
                ledger.append(LedgerEntry(
                    transaction_id=t.id,
                    payee_id=t.payee_id,
                    rule_id=rule.id,
                    event_type="rule_skipped",
                    inputs={"filter": rule.filter or "(none)", "reason": "filter_excluded"},
                    human_readable=f"Transaction {t.id} skipped by rule {rule.id} (filter excluded)",
                ))
            else:
                matching.append(t)

        grouped = self._group_by_payee_period(matching, period_type)

        for (payee_id, window), txn_group in grouped.items():
            payee = payee_map.get(payee_id)
            if payee is None:
                for t in txn_group:
                    ledger.append(LedgerEntry(
                        transaction_id=t.id,
                        payee_id=payee_id,
                        rule_id=rule.id,
                        event_type="rule_skipped",
                        inputs={"filter": rule.filter or "(none)", "reason": "payee_not_found"},
                        human_readable=(
                            f"Transaction {t.id} skipped by rule {rule.id}: "
                            f"payee {payee_id} not found"
                        ),
                    ))
                continue

            ledger.append(
                LedgerEntry(
                    transaction_id="*",
                    payee_id=payee_id,
                    rule_id=rule.id,
                    event_type="rule_evaluated",
                    inputs={
                        "payee_id": payee_id,
                        "txn_count": str(len(txn_group)),
                    },
                    human_readable=(
                        f"Evaluating tiered rule {rule.id} for {payee_id}"
                    ),
                )
            )

            af_tiered = (fractions or {}).get((payee_id, window), Decimal("1"))
            qo = (quota_overrides or {}).get((payee_id, window))
            if qo is not None:
                quota = qo
            else:
                quota = payee.quota_for(window, category=quota_category, activity_fraction=af_tiered)

            if quota == Decimal("0"):
                top_tier = rule.tiers[-1]
                for txn in txn_group:
                    commission = txn.amount * top_tier.rate
                    results.append(
                        Commission(
                            transaction_id=txn.id,
                            payee_id=txn.payee_id,
                            period=window,
                            rule_id=rule.id,
                            base_amount=txn.amount,
                            rate=top_tier.rate,
                            commission_amount=commission,
                            notes=f"No quota set — paid at top tier {top_tier.rate:.2%}",
                        )
                    )
                    ledger.append(
                        LedgerEntry(
                            transaction_id=txn.id,
                            payee_id=txn.payee_id,
                            rule_id=rule.id,
                            event_type="commission_computed",
                            inputs={
                                "amount": str(txn.amount),
                                "rate": str(top_tier.rate),
                                "quota": "0",
                            },
                            outputs={"commission_amount": str(commission)},
                            human_readable=(
                                f"Zero-quota tiered: {txn.amount} * "
                                f"{top_tier.rate} = {commission}"
                            ),
                        )
                    )
                continue

            sorted_txns = sorted(txn_group, key=_sort_key)
            cumulative = Decimal("0")

            for txn in sorted_txns:
                remainder = txn.amount
                _iter = 0
                while remainder > 0:
                    _iter += 1
                    if _iter > 10000:
                        raise RuntimeError(
                            f"Tiered calculation exceeded 10000 iterations for payee "
                            f"{payee_id} — likely a bug in tier thresholds or infinite loop"
                        )
                    current_pct = cumulative / quota
                    tier = self._find_tier(rule.tiers, current_pct)
                    if tier is None:
                        # Attainment at or above all tiers — remainder at highest rate
                        top = rule.tiers[-1]
                        commission = remainder * top.rate
                        top_from = cumulative / quota
                        top_to = (cumulative + remainder) / quota
                        results.append(
                            Commission(
                                transaction_id=txn.id,
                                payee_id=txn.payee_id,
                                period=window,
                                rule_id=rule.id,
                                base_amount=remainder,
                                rate=top.rate,
                                commission_amount=commission,
                                notes=_slice_note(
                                    remainder, top_from, top_to, top.rate,
                                    rule.base == "margin",
                                ),
                            )
                        )
                        ledger.append(
                            LedgerEntry(
                                transaction_id=txn.id,
                                payee_id=txn.payee_id,
                                rule_id=rule.id,
                                event_type="commission_computed",
                                inputs={
                                    "amount": str(remainder),
                                    "rate": str(top.rate),
                                    "cumulative_pct": str(cumulative / quota),
                                    "quota": str(quota),
                                },
                                outputs={"commission_amount": str(commission)},
                                human_readable=(
                                    f"Tiered (top tier {top.threshold_pct}): "
                                    f"{remainder} @ {top.rate} = {commission}"
                                ),
                            )
                        )
                        cumulative += remainder
                        remainder = Decimal("0")
                        break

                    remaining_in_tier = self._remaining_in_tier(
                        rule.tiers, tier, current_pct, quota
                    )
                    piece = min(remainder, remaining_in_tier)
                    commission = piece * tier.rate
                    prev_cum_pct = cumulative / quota
                    cumulative += piece
                    new_cum_pct = cumulative / quota

                    results.append(
                        Commission(
                            transaction_id=txn.id,
                            payee_id=txn.payee_id,
                            period=window,
                            rule_id=rule.id,
                            base_amount=piece,
                            rate=tier.rate,
                            commission_amount=commission,
                            notes=_slice_note(
                                piece, prev_cum_pct, new_cum_pct, tier.rate,
                                rule.base == "margin",
                            ),
                        )
                    )
                    if prev_cum_pct < tier.threshold_pct <= new_cum_pct:
                        ledger.append(
                            LedgerEntry(
                                transaction_id=txn.id,
                                payee_id=txn.payee_id,
                                rule_id=rule.id,
                                event_type="tier_crossed",
                                inputs={
                                    "threshold_pct": str(tier.threshold_pct),
                                    "from_pct": str(prev_cum_pct),
                                    "to_pct": str(new_cum_pct),
                                },
                                human_readable=(
                                    f"Crossed tier {tier.threshold_pct}: "
                                    f"{prev_cum_pct} -> {new_cum_pct}"
                                ),
                            )
                        )
                    ledger.append(
                        LedgerEntry(
                            transaction_id=txn.id,
                            payee_id=txn.payee_id,
                            rule_id=rule.id,
                            event_type="commission_computed",
                            inputs={
                                "amount": str(piece),
                                "rate": str(tier.rate),
                                "cumulative_pct": str(new_cum_pct),
                                "quota": str(quota),
                            },
                            outputs={"commission_amount": str(commission)},
                            human_readable=(
                                f"Tiered: {piece} @ {tier.rate} "
                                f"(at {new_cum_pct:.1%} of quota) = {commission}"
                            ),
                        )
                    )
                    remainder -= piece

        return results, ledger

    @staticmethod
    def _find_tier(tiers: list[Tier], current_pct: Decimal) -> Tier | None:
        for tier in tiers:
            if current_pct < tier.threshold_pct:
                return tier
        return None

    @staticmethod
    def _remaining_in_tier(
        tiers: list[Tier], current_tier: Tier, current_pct: Decimal, quota: Decimal
    ) -> Decimal:
        threshold = current_tier.threshold_pct
        remaining_pct = threshold - current_pct
        return remaining_pct * quota

    # -- accelerator ---------------------------------------------------------

    def _calc_accelerator(
        self,
        rule: AcceleratorRule,
        transactions: list[Transaction],
        payee_map: dict[str, Payee],
        period_type: str = "monthly",
        quota_category: str | None = None,
        fractions: dict[tuple[str, str], Decimal] | None = None,
        quota_overrides: dict[tuple[str, str], Decimal] | None = None,
    ) -> tuple[list[Commission], list[LedgerEntry]]:
        predicate = compile_filter(rule.filter)
        results: list[Commission] = []
        ledger: list[LedgerEntry] = []

        matching: list[Transaction] = []
        for t in transactions:
            if not predicate(t):
                ledger.append(LedgerEntry(
                    transaction_id=t.id,
                    payee_id=t.payee_id,
                    rule_id=rule.id,
                    event_type="rule_skipped",
                    inputs={"filter": rule.filter or "(none)", "reason": "filter_excluded"},
                    human_readable=f"Transaction {t.id} skipped by rule {rule.id} (filter excluded)",
                ))
            else:
                matching.append(t)

        grouped = self._group_by_payee_period(matching, period_type)

        for (payee_id, window), txn_group in grouped.items():
            payee = payee_map.get(payee_id)
            if payee is None:
                for t in txn_group:
                    ledger.append(LedgerEntry(
                        transaction_id=t.id,
                        payee_id=payee_id,
                        rule_id=rule.id,
                        event_type="rule_skipped",
                        inputs={"filter": rule.filter or "(none)", "reason": "payee_not_found"},
                        human_readable=(
                            f"Transaction {t.id} skipped by rule {rule.id}: "
                            f"payee {payee_id} not found"
                        ),
                    ))
                continue
            af_accel = (fractions or {}).get((payee_id, window), Decimal("1"))
            qo_accel = (quota_overrides or {}).get((payee_id, window))
            if qo_accel is not None:
                quota = qo_accel
            else:
                quota = payee.quota_for(window, category=quota_category, activity_fraction=af_accel)
            if quota == Decimal("0"):
                for t in txn_group:
                    ledger.append(LedgerEntry(
                        transaction_id=t.id,
                        payee_id=payee_id,
                        rule_id=rule.id,
                        event_type="rule_skipped",
                        inputs={"filter": rule.filter or "(none)", "reason": "zero_quota"},
                        human_readable=(
                            f"Transaction {t.id} skipped by rule {rule.id}: "
                            f"payee {payee_id} has zero quota"
                        ),
                    ))
                continue

            sorted_txns = sorted(txn_group, key=_sort_key)
            cumulative = Decimal("0")

            for txn in sorted_txns:
                threshold_amount = rule.threshold_pct * quota
                below_threshold = max(Decimal("0"), threshold_amount - cumulative)
                above_threshold = max(Decimal("0"), txn.amount - below_threshold)

                if above_threshold > 0:
                    commission = above_threshold * rule.rate * rule.multiplier
                    results.append(
                        Commission(
                            transaction_id=txn.id,
                            payee_id=txn.payee_id,
                            period=window,
                            rule_id=rule.id,
                            base_amount=above_threshold,
                            rate=rule.rate * rule.multiplier,
                            commission_amount=commission,
                            notes=(
                                f"Accelerator {rule.multiplier}x on "
                                f"{above_threshold} above {rule.threshold_pct}"
                                + (" of quota (gross profit)" if rule.base == "margin" else "")
                            ),
                        )
                    )
                    ledger.append(
                        LedgerEntry(
                            transaction_id=txn.id,
                            payee_id=txn.payee_id,
                            rule_id=rule.id,
                            event_type="commission_computed",
                            inputs={
                                "above_threshold": str(above_threshold),
                                "rate": str(rule.rate),
                                "multiplier": str(rule.multiplier),
                                "cumulative": str(cumulative),
                                "quota": str(quota),
                            },
                            outputs={"commission_amount": str(commission)},
                            human_readable=(
                                f"Accelerator: {above_threshold} * "
                                f"{rule.rate} * {rule.multiplier} = {commission}"
                            ),
                        )
                    )

                cumulative += txn.amount

        return results, ledger

    # -- formula (flexible calc layer) ----------------------------------------

    def _calc_formula(
        self,
        rule: FormulaRule,
        transactions: list[Transaction],
        payee_map: dict[str, Payee],
        period_type: str = "monthly",
        quota_category: str | None = None,
        fractions: dict[tuple[str, str], Decimal] | None = None,
        quota_overrides: dict[tuple[str, str], Decimal] | None = None,
        attainment: list[AttainmentSummary] | None = None,
    ) -> tuple[list[Commission], list[LedgerEntry]]:
        predicate = compile_filter(rule.filter)
        formula = compile_formula(rule.formula)
        results: list[Commission] = []
        ledger: list[LedgerEntry] = []

        # Attainment context per (payee, window). Zero-quota payees get
        # attainment_pct 0 so gating formulas still evaluate.
        att_pct: dict[tuple[str, str], Decimal] = {}
        att_bookings: dict[tuple[str, str], Decimal] = {}
        for a in attainment or []:
            key = (a.payee_id, a.period)
            att_pct[key] = a.attainment_pct if a.attainment_pct is not None else Decimal("0")
            att_bookings[key] = a.bookings

        for txn in transactions:
            if not predicate(txn):
                ledger.append(LedgerEntry(
                    transaction_id=txn.id,
                    payee_id=txn.payee_id,
                    rule_id=rule.id,
                    event_type="rule_skipped",
                    inputs={"filter": rule.filter or "(none)", "reason": "filter_excluded"},
                    human_readable=f"Transaction {txn.id} skipped by rule {rule.id} (filter excluded)",
                ))
                continue

            window = _window_key(txn.period, period_type)
            key = (txn.payee_id, window)
            payee = payee_map.get(txn.payee_id)
            qo = (quota_overrides or {}).get(key)
            if qo is not None:
                quota = qo
            elif payee is not None:
                af = (fractions or {}).get(key, Decimal("1"))
                quota = payee.quota_for(window, category=quota_category, activity_fraction=af)
            else:
                quota = Decimal("0")

            # Metadata first so canonical fields can't be shadowed by a column
            # of the same name.
            context: dict[str, Any] = dict(txn.metadata or {})
            context.update({
                "amount": txn.amount,
                "margin": txn.margin,  # None when absent — referencing it then skips the row
                "product": txn.product,
                "quota": quota,
                "attainment_pct": att_pct.get(key, Decimal("0")),
                "bookings": att_bookings.get(key, Decimal("0")),
            })

            try:
                commission_amount = formula.evaluate(context)
            except FormulaEvalError as e:
                ledger.append(LedgerEntry(
                    transaction_id=txn.id,
                    payee_id=txn.payee_id,
                    rule_id=rule.id,
                    event_type="rule_skipped",
                    inputs={"formula": rule.formula, "reason": "formula_eval_error",
                            "detail": str(e)},
                    human_readable=(
                        f"Transaction {txn.id} skipped by rule {rule.id}: {e}"
                    ),
                ))
                continue

            ledger.append(LedgerEntry(
                transaction_id=txn.id,
                payee_id=txn.payee_id,
                rule_id=rule.id,
                event_type="commission_computed",
                inputs={
                    "formula": rule.formula,
                    "amount": str(txn.amount),
                    "quota": str(quota),
                    "attainment_pct": str(att_pct.get(key, Decimal("0"))),
                },
                outputs={"commission_amount": str(commission_amount)},
                human_readable=(
                    f"Formula: {rule.formula} = {commission_amount}"
                ),
            ))

            if commission_amount == 0:
                continue  # ledger shows the evaluation; no zero line on statements

            results.append(Commission(
                transaction_id=txn.id,
                payee_id=txn.payee_id,
                period=window,
                rule_id=rule.id,
                base_amount=commission_amount,
                rate=Decimal("1"),
                commission_amount=commission_amount,
                notes=f"Custom formula: {rule.formula}",
            ))

        return results, ledger

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _group_by_payee_period(
        transactions: list[Transaction],
        period_type: str = "monthly",
    ) -> dict[tuple[str, str], list[Transaction]]:
        grouped: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
        for txn in transactions:
            key = _window_key(txn.period, period_type)
            grouped[(txn.payee_id, key)].append(txn)
        return grouped


# ------------------------------------------------------------------
# Payee trace — full pipeline breakdown per payee/period
# ------------------------------------------------------------------


def build_payee_trace(
    payee_id: str,
    period: str,
    *,
    commissions: list[Commission],
    ledger: list[LedgerEntry],
    attainment: list[Any],
    plan_name: str = "",
    payout_cap: Decimal | None = None,
    draw_cfg: Any = None,
) -> dict[str, Any]:
    """Build a self-describing pipeline trace for one payee in one period.

    Returns a list of stage dicts that the UI renders in order.
    Each stage has an id, label, kind, inputs, and output.
    """

    # Filter commissions for this payee+period
    my_commissions = [
        c for c in commissions
        if c.payee_id == payee_id and (c.period == period or c.origin_period == period)
    ]

    # Filter ledger for this payee+period
    my_ledger = [
        e for e in ledger
        if e.payee_id == payee_id
        and (e.transaction_id != "*" or e.event_type not in ("credit_allocated", "attainment_computed"))
    ]

    stages: list[dict[str, Any]] = []

    # --- Attainment header ---
    att_info: dict[str, Any] = {}
    for a in attainment:
        if getattr(a, "payee_id", "") == payee_id:
            att_info = {
                "bookings": str(getattr(a, "bookings", "0")),
                "quota": str(getattr(a, "quota", "0")),
                "pct": str(getattr(a, "attainment_pct", "N/A")),
            }
            break

    # --- Stage 1: Rules ---
    rule_lines: dict[str, list[dict[str, Any]]] = {}
    for c in my_commissions:
        if c.rule_id in ("payout_cap", "draw", "manual_adjustment"):
            continue
        rule_lines.setdefault(c.rule_id, []).append({
            "txn_id": c.transaction_id,
            "base": str(c.base_amount),
            "rate": str(c.rate),
            "amount": str(c.commission_amount),
            "notes": c.notes,
        })
    rule_total = sum(
        (Decimal(r["amount"]) for lines in rule_lines.values() for r in lines),
        Decimal("0")
    )
    stages.append({
        "id": "rules", "label": "Rules", "kind": "computation",
        "inputs": {"attainment": att_info},
        "output": {
            "total": str(rule_total),
            "by_rule": {rid: {"lines": lines, "total": str(sum(Decimal(r["amount"]) for r in lines))}
                        for rid, lines in sorted(rule_lines.items())},
        },
    })

    # --- Stage 2: Plan cap ---
    cap_line = next((c for c in my_commissions if c.rule_id == "payout_cap"), None)
    if cap_line is not None or payout_cap is not None:
        cap_val = str(payout_cap) if payout_cap is not None else "N/A"
        cap_adj = str(cap_line.commission_amount) if cap_line else "0"
        post_cap = rule_total + (cap_line.commission_amount if cap_line else Decimal("0"))
        stages.append({
            "id": "plan_cap", "label": "Plan Cap", "kind": "adjustment",
            "inputs": {"earned": str(rule_total), "cap": cap_val},
            "output": {
                "total": str(post_cap),
                "adjustment": cap_adj,
                "note": f"capped: {rule_total} → {post_cap}" if cap_line else "no cap applied",
            },
        })
        running_total = post_cap
    else:
        running_total = rule_total

    # --- Stage 3: Draw ---
    draw_lines = [c for c in my_commissions if c.rule_id == "draw"]
    if draw_lines:
        draw_total = sum(c.commission_amount for c in draw_lines)
        draw_ledger = [e for e in my_ledger if e.event_type == "draw"]
        draw_inputs: dict[str, str] = {}
        if draw_ledger:
            draw_inputs = draw_ledger[0].inputs
        stages.append({
            "id": "draw", "label": "Draw", "kind": "adjustment",
            "inputs": draw_inputs,
            "output": {
                "total": str(running_total + draw_total),
                "adjustment": str(draw_total),
                "note": draw_ledger[0].human_readable if draw_ledger else "",
            },
        })
        running_total = running_total + draw_total

    # --- Stage 4: Cross-period ---
    cross_items: list[dict[str, Any]] = []
    for c in my_commissions:
        if c.origin_period and c.origin_period != c.period and c.rule_id != "draw":
            cross_items.append({
                "type": "true_up",
                "origin": c.origin_period,
                "amount": str(c.commission_amount),
                "note": c.notes,
            })
    # Add draw recovery as cross-period if it references prior balance
    for e in my_ledger:
        if e.event_type == "draw" and e.inputs.get("recovered", "0") != "0":
            cross_items.append({
                "type": "draw_carry",
                "origin": "prior periods",
                "amount": "-" + e.inputs.get("recovered", "0"),
                "note": f"draw recovery from prior balance {e.inputs.get('prior_balance', '?')}",
            })
    if cross_items:
        cross_total = sum(Decimal(it["amount"]) for it in cross_items)
        stages.append({
            "id": "cross_period", "label": "Cross-Period", "kind": "cross_period",
            "items": cross_items,
            "output": {"cross_period_total": str(cross_total)},
        })
        running_total = running_total + cross_total

    # --- Stage 5: Manual adjustments ---
    manual_lines = [c for c in my_commissions if c.rule_id == "manual_adjustment"]
    if manual_lines:
        manual_items = [
            {"type": "manual_adjustment", "amount": str(c.commission_amount),
             "reason": c.notes}
            for c in manual_lines
        ]
        manual_total = sum(c.commission_amount for c in manual_lines)
        stages.append({
            "id": "manual", "label": "Manual Adjustments", "kind": "manual",
            "items": manual_items,
            "output": {"manual_total": str(manual_total)},
        })
        running_total = running_total + manual_total

    # --- Stage 6: Final ---
    stages.append({
        "id": "final", "label": "Payout", "kind": "final",
        "output": {"total": str(running_total), "currency": "USD"},
    })

    return {
        "payee_id": payee_id,
        "period": period,
        "plan_name": plan_name,
        "stages": stages,
    }

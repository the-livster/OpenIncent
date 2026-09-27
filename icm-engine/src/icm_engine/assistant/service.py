"""The commissions service: questions and pay-cycle actions, scoped.

Every figure comes from saved runs, through the same code that builds the
statements, so an answer agrees with the statement to the penny. Nothing here
estimates except `simulate_deal`, which says so and saves nothing.

Actions that change something (calculate, lock, send) are two calls: the
first returns a preview and a confirmation token, the second, with that token,
does it. The token is bound to the preview's exact content, so a changed input,
a stale preview, or a guessed token is refused.
"""

from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from icm_engine.cycle import CycleError, lock_period, prepare_run
from icm_engine.database import Database
from icm_engine.exceptions import PlanDataError
from icm_engine.models import MBO, ManualAdjustment, Plan, Transaction
from icm_engine.reporting import (
    SavedRun,
    export_saved_run,
    load_saved_run,
    payee_plan,
    payout_rows,
    write_saved_statements,
)
from icm_engine.rounding import RoundingMode, parse_rounding_mode, round_money
from icm_engine.run import execute, persist
from icm_engine.statements import statement_summary

# ------------------------------------------------------------------
# Scope and errors
# ------------------------------------------------------------------


@dataclass(frozen=True)
class Scope:
    """Who is asking. An administrator (payee_id None) sees the organisation;
    a payee sees only their own pay and never changes anything."""

    org_id: str = "default"
    payee_id: str | None = None
    allow_changes: bool = True

    @classmethod
    def admin(cls, org_id: str = "default", *, allow_changes: bool = True) -> Scope:
        return cls(org_id=org_id, payee_id=None, allow_changes=allow_changes)

    @classmethod
    def payee(cls, payee_id: str, org_id: str = "default") -> Scope:
        return cls(org_id=org_id, payee_id=payee_id, allow_changes=False)

    @property
    def is_admin(self) -> bool:
        return self.payee_id is None


class AssistantError(Exception):
    """A miss or refusal the person asking (or their assistant) can act on."""


class NotFound(AssistantError):
    pass


class NotAllowed(AssistantError):
    pass


class Refused(AssistantError):
    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        self.problems = list(problems or [])
        detail = "".join(f"\n- {p}" for p in self.problems)
        super().__init__(message + detail)


# ------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------

_MONTHS = {name.lower(): n for n, name in enumerate(calendar.month_abbr) if name}


def normalize_period(text: str | None) -> str | None:
    """'May 2026', '2026-5', 'Q2 2026' -> the engine's keys ('2026-05', '2026-Q2')."""
    if not text:
        return None
    t = text.strip()
    if re.fullmatch(r"\d{4}-\d{2}|\d{4}", t):
        return t
    m = re.fullmatch(r"(\d{4})-[qQ]([1-4])", t)
    if m:
        return f"{m.group(1)}-Q{m.group(2)}"
    m = re.fullmatch(r"(\d{4})[-/ ](\d{1,2})", t)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"([A-Za-z]+)\.?,?\s+(\d{4})", t)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return f"{m.group(2)}-{_MONTHS[m.group(1)[:3].lower()]:02d}"
    m = re.fullmatch(r"[qQ]([1-4])\s+(\d{4})", t)
    if m:
        return f"{m.group(2)}-Q{m.group(1)}"
    return t


def _display(currency: str, amount: str | Decimal) -> str:
    """'GBP 2,480.00': grouped, the decimals exactly as rounded."""
    text = format(Decimal(amount), "f")
    digits = text.lstrip("-")
    whole, dot, frac = digits.partition(".")
    body = f"{currency} {int(whole):,}{dot}{frac}".strip()
    return f"-{body}" if text.startswith("-") and digits.strip("0.") else body


def _percent(fraction: Decimal) -> str:
    """1.26 -> '126%', 0.825 -> '82.5%' (as statements show attainment)."""
    text = format((fraction * 100).quantize(Decimal("0.1")), "f")
    return (text[:-2] if text.endswith(".0") else text) + "%"


def _totals(rows: Iterable[dict[str, str]]) -> dict[str, str]:
    """Payout totals per currency, never mixed."""
    sums: dict[str, Decimal] = {}
    for row in rows:
        sums[row["currency"]] = sums.get(row["currency"], Decimal("0")) + Decimal(row["total"])
    return {cur: _display(cur, total) for cur, total in sorted(sums.items())}


def _file(path: str | None, what: str) -> Path | None:
    if not path:
        return None
    p = Path(path).expanduser()
    if not p.is_file():
        raise NotFound(f"No {what} file at {p}. Give the full path to the file.")
    return p


def _sha256(path: Path | None) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path else ""


def _window_month(period: str) -> str:
    """A transaction month inside a pay window: '2026-Q2' -> '2026-04'."""
    m = re.fullmatch(r"(\d{4})-Q([1-4])", period)
    if m:
        return f"{m.group(1)}-{(int(m.group(2)) - 1) * 3 + 1:02d}"
    if re.fullmatch(r"\d{4}", period):
        return f"{period}-01"
    return period


# ------------------------------------------------------------------
# The service
# ------------------------------------------------------------------


class CommissionsService:
    """Commission questions and pay-cycle actions over one database, for one scope."""

    def __init__(self, db: Database, scope: Scope | None = None, *, export_dir: Path | None = None,
                 runs: dict[str, SavedRun] | None = None) -> None:
        self.db = db
        self.scope = scope or Scope.admin(db.org_id)
        self.export_dir = export_dir or Path.home() / "Downloads"
        # Saved runs as frozen when they ran; services for different people may
        # share one cache, since what each may see is decided on the way out.
        self._runs: dict[str, SavedRun] = runs if runs is not None else {}
        # Per process: a token from one server session is useless in another.
        self._secret = secrets.token_bytes(32)

    # ---------------- saved runs ----------------

    def _run(self, calculation_id: str) -> SavedRun:
        """A saved run as frozen when it ran. Runs never change, so cache them."""
        if calculation_id not in self._runs:
            try:
                self._runs[calculation_id] = load_saved_run(self.db, [calculation_id])
            except LookupError:
                raise NotFound(f"No saved run {calculation_id!r}.") from None
            except ValueError as e:
                raise Refused(str(e)) from None
        return self._runs[calculation_id]

    def _current(self) -> list[dict[str, Any]]:
        """The result that stands for each plan and period: the locked one
        where the period is locked, otherwise the latest calculation."""
        calcs = self.db.list_calculations(limit=100_000)
        by_id = {c["id"]: c for c in calcs}
        current: dict[tuple[str, str], dict[str, Any]] = {}
        for c in calcs:  # newest first
            current.setdefault((c["plan_id"], c["period"]), c)
        for plan_id in {c["plan_id"] for c in calcs}:
            for row in self.db.get_period_status(plan_id):
                locked = row.get("locked_calc_id")
                if locked and locked in by_id:
                    current[(plan_id, row["period"])] = by_id[locked]
        return sorted(current.values(), key=lambda c: (c["period"], c["plan_id"]))

    def _readable(self, records: Iterable[dict[str, Any]]) -> list[tuple[dict[str, Any], SavedRun]]:
        out = []
        for rec in records:
            try:
                out.append((rec, self._run(rec["id"])))
            except AssistantError:
                continue  # an older run without a statement snapshot
        return out

    def _status(self, rec: dict[str, Any]) -> str:
        locked = self.db.get_official_calculation(rec["plan_id"], rec["period"])
        if locked and locked["id"] == rec["id"]:
            return "locked (final)"
        return "draft (can still change)"

    def _rows(self, run: SavedRun, payee_id: str | None = None, period: str | None = None) -> list[dict[str, str]]:
        lines = [c for c in run.lines
                 if (payee_id is None or c.payee_id == payee_id) and (period is None or c.period == period)]
        return payout_rows(lines, run.payees, run.plans, run.snapshot["multi_plan"])

    def _plan_of(self, run: SavedRun, payee_id: str) -> Plan:
        return payee_plan(payee_id, run.payees, run.plans, run.snapshot["multi_plan"])

    # ---------------- who ----------------

    def _people(self) -> dict[str, dict[str, str]]:
        """Everyone the organisation pays: the saved roster, plus anyone paid
        in a current run from an uploaded roster."""
        people: dict[str, dict[str, str]] = {}
        for _rec, run in self._readable(self._current()):
            for p in run.payees:
                people[p.id] = {"payee_id": p.id, "name": p.name, "plan_id": p.plan_id, "email": p.email or ""}
        for row in self.db.list_payees():
            people[row["id"]] = {"payee_id": row["id"], "name": row["name"], "plan_id": row.get("plan_id") or "",
                                 "email": row.get("email") or ""}
        return people

    def _subject(self, payee: str | None) -> str:
        """Whose pay a question is about: the caller's own, for a payee."""
        if not self.scope.is_admin:
            me = self.scope.payee_id or ""
            if payee and payee.strip() not in (me, self._my_name()):
                raise NotAllowed("You can only see your own pay.")
            return me
        if not payee:
            raise Refused("Say whose pay: pass the payee's id or name. find_payees lists everyone.")
        people = self._people()
        ref = payee.strip()
        if ref in people:
            return ref
        exact = [pid for pid, p in people.items() if p["name"].lower() == ref.lower()]
        if len(exact) == 1:
            return exact[0]
        partial = [pid for pid, p in people.items() if ref.lower() in p["name"].lower()]
        if len(partial) == 1:
            return partial[0]
        if not partial:
            raise NotFound(f"No payee matches {ref!r}. find_payees lists everyone.")
        raise Refused(f"{ref!r} matches more than one person. Pass one of these ids:",
                      [f"{pid} ({people[pid]['name']})" for pid in partial[:10]])

    def _my_name(self) -> str:
        me = self.scope.payee_id
        for _rec, run in self._payee_runs(me or ""):
            for p in run.payees:
                if p.id == me:
                    return p.name
        return ""

    def _name(self, payee_id: str, run: SavedRun | None = None) -> str:
        for p in (run.payees if run else []):
            if p.id == payee_id:
                return p.name
        return self._people().get(payee_id, {}).get("name", payee_id)

    def _payee_runs(self, payee_id: str) -> list[tuple[dict[str, Any], SavedRun]]:
        return [(rec, run) for rec, run in self._readable(self._current())
                if any(c.payee_id == payee_id for c in run.lines)]

    def _payee_period(self, payee_id: str, period: str | None) -> tuple[dict[str, Any], SavedRun]:
        runs = self._payee_runs(payee_id)
        if not runs:
            raise NotFound(f"There is no saved pay for {self._name(payee_id)} yet.")
        if not period:
            return max(runs, key=lambda r: (r[0]["period"], r[0]["created_at"]))
        wanted = normalize_period(period)
        hits = [r for r in runs if r[0]["period"] == wanted]
        if not hits:
            have = ", ".join(sorted({rec["period"] for rec, _ in runs}))
            raise NotFound(f"No saved pay for {self._name(payee_id)} in {wanted}. Periods with pay: {have}.")
        return max(hits, key=lambda r: r[0]["created_at"])

    def identify(self, payee: str) -> dict[str, str]:
        """One person, by id or name, as {"payee_id", "name"}."""
        pid = self._subject(payee)
        return {"payee_id": pid, "name": self._name(pid)}

    def whoami(self) -> dict[str, str]:
        """Who this service answers as."""
        if self.scope.is_admin:
            return {"role": "administrator", "organisation": self.scope.org_id}
        me = self.scope.payee_id or ""
        return {"role": "payee", "payee_id": me, "name": self._my_name() or me}

    # ================================================================
    # Questions (every scope)
    # ================================================================

    def overview(self) -> dict[str, Any]:
        """Who is connected, and the latest results they can see."""
        if not self.scope.is_admin:
            me = self.scope.payee_id or ""
            runs = self._payee_runs(me)
            periods = []
            for rec, run in runs:
                for row in self._rows(run, me, rec["period"]):
                    periods.append({"period": row["period"], "total": _display(row["currency"], row["total"]),
                                    "status": self._status(rec)})
            return {
                "connected_as": "payee",
                "payee_id": me,
                "name": self._my_name() or me,
                "plan": self._plan_of(runs[-1][1], me).name if runs else None,
                "periods": periods[-12:],
                "can_run_pay_cycle": False,
            }
        current = self._readable(self._current())
        recent = sorted({rec["period"] for rec, _ in current}, reverse=True)[:6]
        results = []
        for rec, run in current:
            if rec["period"] in recent:
                rows = self._rows(run, period=rec["period"])
                results.append({
                    "plan_id": rec["plan_id"], "period": rec["period"], "status": self._status(rec),
                    "calculation_id": rec["id"], "payees_paid": len({r["payee_id"] for r in rows}),
                    "totals": _totals(rows),
                })
        library = self.db.list_plans()
        return {
            "connected_as": "administrator",
            "organisation": self.scope.org_id,
            "can_run_pay_cycle": self.scope.allow_changes,
            "people": len(self._people()),
            "plans": [{"plan_id": p["id"], "name": p["name"]} for p in library],
            "recent_results": sorted(results, key=lambda r: (r["period"], r["plan_id"]), reverse=True),
        }

    def find_payees(self, query: str | None = None) -> dict[str, Any]:
        """People the organisation pays, by id or name."""
        if not self.scope.is_admin:
            me = self.scope.payee_id or ""
            return {"payees": [{"payee_id": me, "name": self._my_name() or me}]}
        people = sorted(self._people().values(), key=lambda p: p["payee_id"])
        if query:
            q = query.strip().lower()
            people = [p for p in people if q in p["payee_id"].lower() or q in p["name"].lower()]
        return {"count": len(people), "payees": people[:100]}

    def list_runs(self, period: str | None = None, plan_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        """Saved calculations, newest first."""
        records = self.db.list_calculations(plan_id=plan_id, period=normalize_period(period), limit=limit)
        current_ids = {c["id"] for c in self._current()}
        out = []
        for rec, run in self._readable(records):
            base = {
                "calculation_id": rec["id"], "plan_id": rec["plan_id"], "period": rec["period"],
                "version": rec["version"], "calculated_at": rec["created_at"], "status": self._status(rec),
                "current": rec["id"] in current_ids,
            }
            if self.scope.is_admin:
                rows = self._rows(run, period=rec["period"])
                out.append(base | {"payees_paid": len({r["payee_id"] for r in rows}), "totals": _totals(rows)})
            else:
                me = self.scope.payee_id or ""
                rows = self._rows(run, me, rec["period"])
                if rows:
                    out.append(base | {"your_total": _totals(rows)})
        return {"runs": out}

    def run_summary(self, calculation_id: str) -> dict[str, Any]:
        """One saved calculation: its status and what it pays."""
        rec = self.db.get_calculation(calculation_id)
        if rec is None:
            raise NotFound(f"No saved run {calculation_id!r}.")
        run = self._run(calculation_id)
        info = {
            "calculation_id": rec["id"], "plan_id": rec["plan_id"], "period": rec["period"],
            "version": rec["version"], "calculated_at": rec["created_at"], "status": self._status(rec),
        }
        if not self.scope.is_admin:
            me = self.scope.payee_id or ""
            rows = self._rows(run, me, rec["period"])
            if not rows:
                raise NotAllowed("You can only see runs that pay you.")
            return info | {"your_payout": {"total": _display(rows[0]["currency"], rows[0]["total"])}}
        rows = self._rows(run, period=rec["period"])
        return info | {
            "totals": _totals(rows),
            "payouts": [
                {"payee_id": r["payee_id"], "name": r["name"], "total": _display(r["currency"], r["total"])}
                for r in sorted(rows, key=lambda r: Decimal(r["total"]), reverse=True)
            ],
            "commission_lines": len([c for c in run.lines if c.period == rec["period"]]),
        }

    def _summary(self, run: SavedRun, payee_id: str, period: str) -> dict[str, Any]:
        plan = self._plan_of(run, payee_id)
        rounding = plan.rounding
        return statement_summary(
            run.lines, payee_id, period=period, attainment=run.snapshot["attainment"],
            rounding_mode=parse_rounding_mode(rounding.mode) if rounding else RoundingMode.HALF_UP,
            rounding_places=rounding.places if rounding else 2,
            transactions=run.deals,
        )

    def pay(self, payee: str | None = None, period: str | None = None) -> dict[str, Any]:
        """What someone is paid: one period in detail, or every period."""
        pid = self._subject(payee)
        if period is None:
            history = []
            for rec, run in self._payee_runs(pid):
                for row in self._rows(run, pid, rec["period"]):
                    history.append({"period": row["period"], "total": _display(row["currency"], row["total"]),
                                    "status": self._status(rec), "calculation_id": rec["id"]})
            rec, run = self._payee_period(pid, None)
            return {"payee_id": pid, "name": self._name(pid, run), "periods": history,
                    "latest": self._pay_detail(pid, rec, run)}
        rec, run = self._payee_period(pid, period)
        return self._pay_detail(pid, rec, run)

    def _pay_detail(self, pid: str, rec: dict[str, Any], run: SavedRun) -> dict[str, Any]:
        plan = self._plan_of(run, pid)
        summary = self._summary(run, pid, rec["period"])
        rows = self._rows(run, pid, rec["period"])
        total = rows[0]["total"] if rows else "0"
        return {
            "payee_id": pid,
            "name": self._name(pid, run),
            "period": rec["period"],
            "plan": plan.name,
            "currency": plan.currency,
            "total": _display(plan.currency, total),
            "earned": _display(plan.currency, summary["earned"]),
            "adjustments": _display(plan.currency, summary["adjustments"]),
            "breakdown": [
                {"category": b["category"], "amount": _display(plan.currency, b["amount"]),
                 "is_adjustment": b["adjustment"]}
                for b in summary["breakdown"]
            ],
            "attainment": summary["attainment"],
            "deals": sum(1 for d in summary["deals"] if not d["is_adjustment"]),
            "status": self._status(rec),
            "calculation_id": rec["id"],
            "calculated_at": rec["created_at"],
        }

    def explain(self, payee: str | None = None, period: str | None = None) -> dict[str, Any]:
        """Every line of someone's pay for a period, as their statement shows it."""
        pid = self._subject(payee)
        rec, run = self._payee_period(pid, period)
        plan = self._plan_of(run, pid)
        return {
            "name": self._name(pid, run),
            "plan": plan.name,
            "currency": plan.currency,
            "status": self._status(rec),
            "calculation_id": rec["id"],
        } | self._summary(run, pid, rec["period"])

    def trace_deal(self, deal: str, payee: str | None = None, period: str | None = None) -> dict[str, Any]:
        """How one deal was paid, rule by rule, from the audit ledger."""
        from icm_engine.trace import build_order_trace

        pid = self._subject(payee)
        runs = self._payee_runs(pid)
        if period:
            wanted = normalize_period(period)
            runs = [r for r in runs if r[0]["period"] == wanted]
        for rec, run in sorted(runs, key=lambda r: r[0]["period"], reverse=True):
            ids = {c.transaction_id for c in run.lines if c.payee_id == pid}
            by_deal = {d.get("deal_id"): d["id"] for d in run.deals if d["id"] in ids}
            tid = deal if deal in ids else by_deal.get(deal)
            if tid is None:
                continue
            entries = self.db.query_ledger(payee_id=pid, calculation_id=rec["id"], limit=10000)
            trace = build_order_trace(tid, pid, entries)
            # Quote the deal as the statement shows it, in the plan's own currency.
            currency = self._plan_of(run, pid).currency
            on_statement = next((d["amount"] for d in self._summary(run, pid, rec["period"])["deals"]
                                 if d["transaction_id"] == tid), trace.total)
            total = _display(currency, on_statement)
            matched = sum(s.status == "matched" for s in trace.steps)
            skipped = sum(s.status == "skipped" for s in trace.steps)
            return {
                "transaction_id": tid,
                "payee_id": pid,
                "period": rec["period"],
                "calculation_id": rec["id"],
                "steps": [
                    {"rule": s.rule_id, "status": s.status, "reason": s.reason,
                     "events": [e.get("human_readable", "") for e in s.events]}
                    for s in trace.steps
                ],
                "total": total,
                "summary": f"{matched} rule(s) matched, {skipped} skipped. Total commission: {total}.",
            }
        raise NotFound(f"No deal {deal!r} in {self._name(pid)}'s pay"
                       + (f" for {normalize_period(period)}." if period else "."))

    def plan_summary(self, plan_id: str | None = None) -> dict[str, Any]:
        """A plan's rules, as the engine runs them."""
        if not self.scope.is_admin:
            me = self.scope.payee_id or ""
            runs = self._payee_runs(me)
            if not runs:
                raise NotFound("There is no saved pay for you yet, so no plan to show.")
            plan = self._plan_of(runs[-1][1], me)
            if plan_id and plan_id != plan.plan_id:
                raise NotAllowed("You can only see your own plan.")
            return self._describe_plan(plan)
        library, failures = self.db.load_plan_library_detailed()
        for _rec, run in self._readable(self._current()):
            for pid, p in run.plans.items():
                library.setdefault(pid, p)
        if not plan_id:
            return {"plans": [{"plan_id": p.plan_id, "name": p.name} for p in library.values()],
                    "unloadable": failures}
        if plan_id not in library:
            raise NotFound(f"No plan {plan_id!r}. Plans: " + ", ".join(sorted(library)))
        return self._describe_plan(library[plan_id])

    @staticmethod
    def _describe_plan(plan: Plan) -> dict[str, Any]:
        data = plan.model_dump(mode="json", exclude_none=True, exclude={"assertions"})
        return {k: v for k, v in data.items() if v not in ([], {}, "")}

    def simulate_deal(
        self, amount: float | str | Decimal, payee: str | None = None, period: str | None = None,
        product: str | None = None,
    ) -> dict[str, Any]:
        """Re-run a saved period with one extra (hypothetical) deal. Saves nothing."""
        pid = self._subject(payee)
        try:
            value = Decimal(str(amount))
        except InvalidOperation:
            raise Refused(f"{amount!r} is not an amount.") from None
        if value == 0:
            raise Refused("A deal of 0 changes nothing.")
        rec, run = self._payee_period(pid, period)
        inputs = self.db.get_run_inputs(run.snapshot["run_id"])
        if inputs is None:
            raise Refused(
                "This period was calculated before OpenIncent kept each run's inputs, so it can't "
                "be re-run. Recalculate the period, then ask again."
            )
        txns = [Transaction.model_validate(t) for t in inputs["transactions"]]
        adjustments = [ManualAdjustment.model_validate(a) for a in inputs["adjustments"]] or None
        mbos = [MBO.model_validate(m) for m in inputs["mbos"]] or None

        own = [t for t in txns if t.payee_id == pid]
        month = next((t.period for t in reversed(own) if _window_of(t.period, rec["period"])),
                     _window_month(rec["period"]))
        extra = Transaction(id="WHAT-IF", deal_id="What-if deal", payee_id=pid, period=month,
                            amount=value, product=product)

        before = self._recalculate(run, txns, adjustments, mbos)
        after = self._recalculate(run, [*txns, extra], adjustments, mbos)
        currency = self._plan_of(run, pid).currency

        def total(result: Any) -> Decimal:
            rows = payout_rows([c for c in result.commissions if c.payee_id == pid and c.period == rec["period"]],
                               run.payees, run.plans, run.snapshot["multi_plan"])
            return Decimal(rows[0]["total"]) if rows else Decimal("0")

        def attainment(result: Any) -> str | None:
            for a in result.attainment:
                if a.payee_id == pid and a.period == rec["period"] and a.attainment_pct is not None:
                    return _percent(a.attainment_pct)
            return None

        plan = self._plan_of(run, pid)
        mode = parse_rounding_mode(plan.rounding.mode) if plan.rounding else RoundingMode.HALF_UP
        places = plan.rounding.places if plan.rounding else 2
        saved = Decimal(self._rows(run, pid, rec["period"])[0]["total"])
        delta = total(after) - total(before)
        result = {
            "simulation": True,
            "payee_id": pid,
            "period": rec["period"],
            "deal_amount": _display(currency, round_money(value, mode, places)),
            "current_pay": _display(currency, saved),
            "additional_pay": _display(currency, delta),
            "pay_with_deal": _display(currency, saved + delta),
            "attainment_before": attainment(before),
            "attainment_after": attainment(after),
            "how_the_deal_would_pay": [
                {"type": c.rule_id, "amount": _display(currency, round_money(c.commission_amount, mode, places)),
                 "explanation": c.notes}
                for c in after.commissions if c.transaction_id == "WHAT-IF" and c.payee_id == pid
            ],
            "note": "Hypothetical: nothing was saved, and real pay only changes when the deal is in a calculated run.",
        }
        if total(before) != saved:
            result["caveat"] = (
                f"Re-running the saved inputs gives {_display(currency, total(before))}, not the "
                f"{_display(currency, saved)} on the statement (locked-period true-ups or draws carried "
                "between runs are not re-applied). The additional pay is still the deal's effect."
            )
        return result

    @staticmethod
    def _recalculate(run: SavedRun, txns: list[Transaction], adjustments: list[Any] | None,
                     mbos: list[Any] | None) -> Any:
        from icm_engine.engine import CommissionEngine

        engine = CommissionEngine()
        try:
            if run.snapshot["multi_plan"]:
                return engine.calculate_run(run.plans, txns, run.payees, adjustments=adjustments, mbos=mbos)
            plan = next(iter(run.plans.values()))
            return engine.calculate(plan, txns, run.payees, adjustments=adjustments, mbos=mbos)
        except PlanDataError as e:
            raise Refused("The engine can't price that deal.", e.problems) from None

    # ================================================================
    # Pay cycle (administrators with changes allowed)
    # ================================================================

    def _require_changes(self) -> None:
        if not self.scope.is_admin:
            raise NotAllowed("Only an administrator can run the pay cycle.")
        if not self.scope.allow_changes:
            raise NotAllowed("This connection is read-only: the pay cycle can't be run from it.")

    def _token(self, action: str, payload: Any) -> str:
        blob = json.dumps({"action": action, "payload": payload}, sort_keys=True, default=str).encode()
        return hmac.new(self._secret, blob, hashlib.sha256).hexdigest()[:24]

    def _confirmed(self, given: str | None, expected: str) -> bool:
        if not given:
            return False
        if not hmac.compare_digest(given.strip(), expected):
            raise Refused(
                "That confirmation doesn't match this preview: the inputs or the situation changed since, "
                "or it came from another preview. Preview again, show it to the person, and confirm with "
                "the new token."
            )
        return True

    def calculate(
        self, transactions_file: str, payees_file: str | None = None, plan_file: str | None = None,
        adjustments_file: str | None = None, mbos_file: str | None = None,
        effective_period: str | None = None, allow_unknown_payees: bool = False,
        confirm_token: str | None = None,
    ) -> dict[str, Any]:
        """Calculate from files: a preview first, then (confirmed) a saved run."""
        from icm_engine.loader import load_adjustments, load_mbos, load_payees, load_plan, load_transactions

        self._require_changes()
        paths = {
            "transactions": _file(transactions_file, "transactions"),
            "payees": _file(payees_file, "payees"),
            "plan": _file(plan_file, "plan"),
            "adjustments": _file(adjustments_file, "adjustments"),
            "mbos": _file(mbos_file, "MBO"),
        }
        if paths["transactions"] is None:
            raise Refused("A transactions file is required.")
        try:
            txns, _ = load_transactions(paths["transactions"])
            payees = load_payees(paths["payees"])[0] if paths["payees"] else None
            plan = load_plan(paths["plan"]) if paths["plan"] else None
            adjustments = load_adjustments(paths["adjustments"]) if paths["adjustments"] else None
            mbos = load_mbos(paths["mbos"]) if paths["mbos"] else None
        except (ValueError, KeyError, OSError) as e:
            raise Refused(f"Couldn't read the files: {e}") from None
        try:
            prepared = prepare_run(
                self.db, transactions=txns, payees=payees, plan=plan, adjustments=adjustments, mbos=mbos,
                effective_period=normalize_period(effective_period), allow_unknown_payees=allow_unknown_payees,
            )
        except CycleError as e:
            raise Refused(str(e), e.problems) from None
        ctx = prepared.ctx
        try:
            result = execute(ctx)
        except PlanDataError as e:
            raise Refused("The engine refused some deal lines.", e.problems) from None

        try:
            rows = payout_rows(result.commissions, ctx.payees, ctx.plan_library, ctx.multi_plan)
        except ValueError as e:
            raise Refused(str(e)) from None
        by_period = []
        for period in sorted({r["period"] for r in rows}):
            period_rows = [r for r in rows if r["period"] == period]
            by_period.append({"period": period, "payees_paid": len(period_rows), "totals": _totals(period_rows)})
        summary: dict[str, Any] = {
            "periods": by_period,
            "totals": _totals(rows),
            "largest_payouts": [
                {"payee_id": r["payee_id"], "name": r["name"], "period": r["period"],
                 "total": _display(r["currency"], r["total"])}
                for r in sorted(rows, key=lambda r: Decimal(r["total"]), reverse=True)[:10]
            ],
            "plans": [{"plan_id": p.plan_id, "name": p.name} for p in ctx.plan_library.values()],
            "roster": "saved roster" if prepared.using_saved_roster else str(paths["payees"]),
            "warnings": [i.message for i in prepared.issues],
        }
        if ctx.locked_relevant:
            summary["locked_periods"] = sorted(ctx.locked_relevant)
            summary["locked_note"] = (
                "These periods are locked. Their changes become true-ups paid in "
                f"{ctx.effective_period}; the locked results stay as they are."
            )
        fingerprint = {
            "files": {k: _sha256(v) for k, v in paths.items()},
            "options": [normalize_period(effective_period), allow_unknown_payees],
            "rows": rows,
            "locked": sorted(ctx.locked_relevant),
        }
        token = self._token("calculate", fingerprint)
        if not self._confirmed(confirm_token, token):
            return {"status": "preview", "summary": summary, "confirm_token": token,
                    "next_step": ("Nothing is saved yet. Show this to the person; if they agree, call "
                                  "calculate_period again with the same files and this confirm_token.")}
        calc_ids = persist(ctx, result)
        return {"status": "saved", "summary": summary, "calculation_ids": calc_ids,
                "next_step": "Saved as a draft. lock_period makes a period's figures final."}

    def lock(
        self, plan_id: str, period: str, calculation_id: str | None = None, reason: str = "",
        confirm_token: str | None = None,
    ) -> dict[str, Any]:
        """Make a period's figures final: a preview first, then (confirmed) the lock."""
        self._require_changes()
        wanted = normalize_period(period) or period
        if self.db.is_locked(plan_id, wanted):
            raise Refused(f"{plan_id} {wanted} is already locked.")
        if calculation_id is None:
            latest = self.db.list_calculations(plan_id=plan_id, period=wanted, limit=1)
            if not latest:
                raise NotFound(f"No calculation for {plan_id} {wanted}. list_runs shows what exists.")
            calculation_id = latest[0]["id"]
        rec = self.db.get_calculation(calculation_id)
        if rec is None:
            raise NotFound(f"No saved run {calculation_id!r}.")
        if rec["plan_id"] != plan_id or rec["period"] != wanted:
            raise Refused(f"Run {calculation_id} is {rec['plan_id']} {rec['period']}, not {plan_id} {wanted}.")
        rows = self._rows(self._run(calculation_id), period=wanted)
        preview = {
            "plan_id": plan_id, "period": wanted, "calculation_id": calculation_id,
            "version": rec["version"], "calculated_at": rec["created_at"],
            "payees_paid": len(rows), "totals": _totals(rows),
            "effect": ("Locking makes these the final figures for the period. Later changes to its deals "
                       "become true-ups in a later period instead of changing it."),
        }
        token = self._token("lock", [plan_id, wanted, calculation_id, reason])
        if not self._confirmed(confirm_token, token):
            return {"status": "preview", "lock": preview, "confirm_token": token,
                    "next_step": ("Nothing is locked yet. Show this to the person; if they agree, call "
                                  "lock_period again with the same arguments and this confirm_token.")}
        try:
            done = lock_period(self.db, plan_id, wanted, calculation_id, locked_by="assistant (MCP)", reason=reason)
        except CycleError as e:
            raise Refused(str(e), e.problems) from None
        return {"status": "locked"} | preview | {"register_path": done["register_path"]}

    def _run_ids(self, period: str | None, calculation_ids: list[str] | None) -> list[str]:
        if calculation_ids:
            for cid in calculation_ids:
                self._run(cid)  # exists and is readable
            return list(dict.fromkeys(calculation_ids))
        wanted = normalize_period(period)
        if not wanted:
            raise Refused("Say which period (or pass calculation_ids).")
        ids = [rec["id"] for rec, _ in self._readable(self._current()) if rec["period"] == wanted]
        if not ids:
            raise NotFound(f"No saved results for {wanted}.")
        return ids

    def export_statements(
        self, period: str | None = None, calculation_ids: list[str] | None = None,
        formats: list[str] | None = None, folder: str | None = None,
    ) -> dict[str, Any]:
        """Write a period's statements (a ZIP per run) to a folder. Adds files only."""
        self._require_changes()
        fmts = tuple(dict.fromkeys(formats or ["html", "pdf"]))
        unknown = [f for f in fmts if f not in ("html", "pdf", "xlsx")]
        if unknown:
            raise Refused(f"Unknown format(s): {', '.join(unknown)}. Use html, pdf or xlsx.")
        target = Path(folder).expanduser() if folder else self.export_dir
        target.mkdir(parents=True, exist_ok=True)
        groups: dict[str, list[str]] = {}
        for cid in self._run_ids(period, calculation_ids):
            groups.setdefault(self._run(cid).snapshot["run_id"], []).append(cid)
        written = []
        for run_id, ids in groups.items():
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    content = export_saved_run(self.db, ids, fmts, Path(tmp))
                except (LookupError, ValueError) as e:
                    raise Refused(str(e)) from None
            label = normalize_period(period) or "runs"
            path = target / f"commission_statements_{label}_{run_id[:8]}.zip"
            path.write_bytes(content)
            written.append(str(path))
        return {"status": "exported", "files": written, "formats": list(fmts),
                "note": "Each ZIP holds one private statement per person, plus an internal payout summary."}

    def send_statements(
        self, period: str | None = None, calculation_ids: list[str] | None = None,
        formats: list[str] | None = None, eml_folder: str | None = None, confirm_token: str | None = None,
    ) -> dict[str, Any]:
        """Email each person their own statement: a preview first, then (confirmed) the send."""
        from icm_engine.distribute import build_messages, send_via_smtp, write_eml

        self._require_changes()
        fmts = tuple(dict.fromkeys(formats or ["html"]))
        ids = self._run_ids(period, calculation_ids)
        smtp = _smtp_from_env()
        delivery = (f"write .eml files to {Path(eml_folder).expanduser()}" if eml_folder
                    else f"email through {smtp.host}" if smtp else None)

        with tempfile.TemporaryDirectory() as tmp:
            files: list[Any] = []
            people: dict[str, Any] = {}
            totals: dict[str, str] = {}
            for n, cid in enumerate(ids):
                run = self._run(cid)
                rec_period = self.db.get_calculation(cid)["period"]  # type: ignore[index]
                for row in self._rows(run, period=rec_period):
                    folder = Path(tmp) / f"{n:03d}" / row["payee_id"]
                    files += write_saved_statements(run, row["payee_id"], row["period"], fmts, folder)
                    totals[row["payee_id"]] = _display(row["currency"], row["total"])
                people.update({p.id: p for p in run.payees})
            messages, skipped = build_messages(files, list(people.values()), totals=totals)
            recipients = [
                {"payee_id": m.payee_id, "name": people[m.payee_id].name, "to": m.to,
                 "total": totals.get(m.payee_id, ""), "attachments": [a.name for a in m.attachments]}
                for m in messages
            ]
            preview = {
                "recipients": recipients,
                "not_sent": [{"payee_id": s.payee_id, "name": people[s.payee_id].name,
                              "reason": "no email address on the roster"} for s in skipped],
                "delivery": delivery or "not set up",
                "sample": {"subject": messages[0].subject, "body": messages[0].body} if messages else None,
            }
            if not messages:
                raise Refused("Nobody in these results has an email address on the roster.")
            if delivery is None:
                return {"status": "preview", "send": preview, "confirm_token": None,
                        "setup_needed": ("Email isn't set up, so this can't be sent yet. Set ICM_SMTP_HOST, "
                                         "ICM_SMTP_PORT, ICM_SMTP_USER, ICM_SMTP_PASS and ICM_SMTP_FROM for the "
                                         "server, or pass eml_folder to write .eml files to send yourself.")}
            token = self._token("send", [recipients, delivery])
            if not self._confirmed(confirm_token, token):
                return {"status": "preview", "send": preview, "confirm_token": token,
                        "next_step": ("Nothing has been sent. Show this to the person; if they agree, call "
                                      "send_statements again with the same arguments and this confirm_token.")}
            if eml_folder:
                sender = smtp.from_addr if smtp and smtp.from_addr else "commissions@openincent.local"
                paths = write_eml(messages, Path(eml_folder).expanduser(), from_addr=sender)
                return {"status": "written", "files": [str(p) for p in paths], "not_sent": preview["not_sent"]}
            assert smtp is not None
            results = send_via_smtp(messages, smtp)
            return {
                "status": "sent",
                "results": [{"payee_id": r.payee_id, "status": r.status, "reason": r.reason} for r in results],
                "not_sent": preview["not_sent"],
            }


def _window_of(month: str, window: str) -> bool:
    """Whether a transaction month falls inside a pay window key."""
    if month == window:
        return True
    m = re.fullmatch(r"(\d{4})-Q([1-4])", window)
    if m and re.fullmatch(r"\d{4}-\d{2}", month):
        return month[:4] == m.group(1) and (int(month[5:]) - 1) // 3 + 1 == int(m.group(2))
    return bool(re.fullmatch(r"\d{4}", window)) and month.startswith(window + "-")


def _smtp_from_env() -> Any:
    """SMTP settings from ICM_SMTP_* (as `icm distribute --smtp-from-env`), or None."""
    from icm_engine.distribute import SmtpConfig

    host = os.environ.get("ICM_SMTP_HOST", "")
    if not host:
        return None
    return SmtpConfig(
        host=host,
        port=int(os.environ.get("ICM_SMTP_PORT", "587")),
        user=os.environ.get("ICM_SMTP_USER", ""),
        password=os.environ.get("ICM_SMTP_PASS", ""),
        from_addr=os.environ.get("ICM_SMTP_FROM", ""),
        use_tls=os.environ.get("ICM_SMTP_TLS", "1") != "0",
    )

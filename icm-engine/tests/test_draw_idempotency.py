"""Re-running an unlocked period must not compound recoverable-draw recovery.

Regression for the deferred bug: persist() used to write the post-run balance
to a single row, and resolve() reloaded that row as "prior" — so re-running
the SAME unlocked period recovered the draw again on top of its own earlier
recovery. The fix stamps balances per period (draw_balance_history) and loads
the balance as of BEFORE the run's first period.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from icm_engine.database import Database
from icm_engine.models import Draw, FlatRateRule, Payee, Plan, Transaction
from icm_engine.run import RunContext, execute, persist


def _plan() -> Plan:
    return Plan(plan_id="p", name="P", period_type="monthly", currency="USD",
                rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))])


def _payee() -> Payee:
    return Payee(id="D1", name="Drew", quota=Decimal("0"), plan_id="p",
                 effective_from=date(2026, 1, 1),
                 draw=Draw(amount=Decimal("5000"), recoverable=True))


def _txn(tid: str, period: str, amount: str, day: date) -> Transaction:
    return Transaction(id=tid, payee_id="D1", period=period,
                       amount=Decimal(amount), close_date=day)


def _run(db: Database, txns: list[Transaction]):
    ctx = RunContext(
        plan_library={"p": _plan()},
        transactions=txns,
        payees=[_payee()],
        db=db,
        single_plan_mode=True,
    )
    ctx.resolve()
    result = execute(ctx)
    persist(ctx, result)
    return result


def _period_total(result, period: str) -> Decimal:
    return sum(
        (c.commission_amount for c in result.commissions
         if c.payee_id == "D1" and c.period == period),
        Decimal("0"),
    )


class TestDrawRerunIdempotency:
    def test_rerunning_unlocked_period_is_idempotent(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "t.db"))
        db.init()

        # P1: earned 3000 < draw 5000 -> payout floored at 5000, balance 2000
        r1 = _run(db, [_txn("T1", "2026-01", "30000", date(2026, 1, 10))])
        assert r1.draw_balances["D1"] == Decimal("2000")
        assert db.get_draw_balance("D1", "p") == Decimal("2000")

        # P2: earned 6500 -> available 1500, recovers 1500 -> balance 500
        p2 = [_txn("T2", "2026-02", "65000", date(2026, 2, 10))]
        r2 = _run(db, p2)
        assert r2.draw_balances["D1"] == Decimal("500")
        p2_total = _period_total(r2, "2026-02")
        assert p2_total == Decimal("5000")  # 6500 earned - 1500 recovered

        # Re-run P2 with the same file: identical payout, identical balance.
        # (The old bug loaded the post-P2 balance as "prior" and recovered again.)
        r3 = _run(db, p2)
        assert r3.draw_balances["D1"] == Decimal("500")
        assert _period_total(r3, "2026-02") == p2_total
        assert db.get_draw_balance("D1", "p") == Decimal("500")

        # And a third time, for luck — still no drift.
        r4 = _run(db, p2)
        assert r4.draw_balances["D1"] == Decimal("500")
        assert _period_total(r4, "2026-02") == p2_total

    def test_next_period_starts_from_prior_period_balance(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "t.db"))
        db.init()

        _run(db, [_txn("T1", "2026-01", "30000", date(2026, 1, 10))])   # balance 2000
        _run(db, [_txn("T2", "2026-02", "65000", date(2026, 2, 10))])   # balance 500

        # P3: earned 8000 -> available 3000, recovers remaining 500 -> balance 0
        r3 = _run(db, [_txn("T3", "2026-03", "80000", date(2026, 3, 10))])
        assert r3.draw_balances["D1"] == Decimal("0")
        assert _period_total(r3, "2026-03") == Decimal("7500")

    def test_legacy_balance_used_when_no_history(self, tmp_path: Path) -> None:
        """Pre-upgrade databases have only the single-row balance — honor it."""
        db = Database(str(tmp_path / "t.db"))
        db.init()
        db.set_draw_balance("D1", "p", Decimal("1200"))

        ctx = RunContext(
            plan_library={"p": _plan()},
            transactions=[_txn("T1", "2026-02", "65000", date(2026, 2, 10))],
            payees=[_payee()],
            db=db,
            single_plan_mode=True,
        )
        ctx.resolve()
        assert ctx.prior_draw_balances == {"D1": Decimal("1200")}


class TestDrawBalanceHistoryDb:
    def test_no_history_returns_none(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "t.db"))
        db.init()
        assert db.get_draw_balance_before("D1", "p", "2026-02") is None

    def test_history_after_period_means_fresh_start(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "t.db"))
        db.init()
        db.set_draw_balance_asof("D1", "p", "2026-03", Decimal("700"))
        # History exists, but nothing precedes 2026-02 -> 0, not legacy fallback
        assert db.get_draw_balance_before("D1", "p", "2026-02") == Decimal("0")

    def test_latest_period_before_wins(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "t.db"))
        db.init()
        db.set_draw_balance_asof("D1", "p", "2026-01", Decimal("2000"))
        db.set_draw_balance_asof("D1", "p", "2026-02", Decimal("500"))
        db.set_draw_balance_asof("D1", "p", "2026-03", Decimal("0"))
        assert db.get_draw_balance_before("D1", "p", "2026-03") == Decimal("500")
        assert db.get_draw_balance_before("D1", "p", "2026-02") == Decimal("2000")

    def test_asof_upsert_overwrites_same_period(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "t.db"))
        db.init()
        db.set_draw_balance_asof("D1", "p", "2026-01", Decimal("2000"))
        db.set_draw_balance_asof("D1", "p", "2026-01", Decimal("1750"))
        assert db.get_draw_balance_before("D1", "p", "2026-02") == Decimal("1750")

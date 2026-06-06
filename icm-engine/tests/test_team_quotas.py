"""Tests for team/shared quotas via Payee.team_id."""

from datetime import date
from decimal import Decimal

from icm_engine.engine import CommissionEngine
from icm_engine.models import (
    FlatRateRule,
    Payee,
    Plan,
    Tier,
    TieredRule,
    Transaction,
)


def _plan(**kw) -> Plan:
    defaults = dict(plan_id="P1", name="Test", period_type="monthly", currency="USD",
                    rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))])
    return Plan(**(defaults | kw))


def _payee(id: str, quota: str = "10000", team_id: str = "", **kw) -> Payee:
    defaults = dict(id=id, name=id, quota=Decimal(quota), plan_id="P1",
                    effective_from=date(2026, 1, 1), team_id=team_id)
    return Payee(**(defaults | kw))


def _txn(id: str, payee_id: str, amount: str, period: str = "2026-04") -> Transaction:
    return Transaction(id=id, payee_id=payee_id, amount=Decimal(amount),
                       period=period, close_date=date(2026, 4, 15))


class TestTeamQuotas:
    def test_two_person_team_pooled_quota(self) -> None:
        """Alice and Bob share team "A". Combined bookings / combined quota."""
        engine = CommissionEngine()
        plan = _plan()
        payees = [
            _payee("alice", "5000", team_id="team-a"),
            _payee("bob", "5000", team_id="team-a"),
        ]
        txns = [
            _txn("T1", "alice", "6000"),  # alice books 6000
            _txn("T2", "bob", "2000"),    # bob books 2000
        ]
        result = engine.calculate(plan, txns, payees)

        # Team total: 8000 bookings, 10000 quota → 80%
        alice_att = [a for a in result.attainment if a.payee_id == "alice"]
        bob_att = [a for a in result.attainment if a.payee_id == "bob"]

        assert len(alice_att) == 1
        assert alice_att[0].bookings == Decimal("8000")  # team bookings
        assert alice_att[0].quota == Decimal("10000")    # team quota
        assert alice_att[0].attainment_pct == Decimal("0.8")

        assert len(bob_att) == 1
        assert bob_att[0].bookings == Decimal("8000")
        assert bob_att[0].quota == Decimal("10000")
        assert bob_att[0].attainment_pct == Decimal("0.8")

    def test_team_member_with_no_bookings_still_gets_team_attainment(self) -> None:
        """Charlie is on the team but has no deals. Still sees team attainment."""
        engine = CommissionEngine()
        plan = _plan()
        payees = [
            _payee("alice", "5000", team_id="team-a"),
            _payee("charlie", "5000", team_id="team-a"),
        ]
        txns = [_txn("T1", "alice", "10000")]
        result = engine.calculate(plan, txns, payees)

        charlie_att = [a for a in result.attainment if a.payee_id == "charlie"]
        assert len(charlie_att) == 1
        assert charlie_att[0].bookings == Decimal("10000")  # team bookings
        assert charlie_att[0].quota == Decimal("10000")     # team quota

    def test_mixed_team_and_solo(self) -> None:
        """Team of 2 + 1 solo payee. Solo uses own quota, team uses pooled."""
        engine = CommissionEngine()
        plan = _plan()
        payees = [
            _payee("alice", "5000", team_id="team-a"),
            _payee("bob", "5000", team_id="team-a"),
            _payee("carol", "20000"),  # solo
        ]
        txns = [
            _txn("T1", "alice", "4000"),
            _txn("T2", "bob", "4000"),
            _txn("T3", "carol", "15000"),
        ]
        result = engine.calculate(plan, txns, payees)

        # Team: 8000 bookings, 10000 quota → 80%
        alice_att = [a for a in result.attainment if a.payee_id == "alice"][0]
        assert alice_att.bookings == Decimal("8000")
        assert alice_att.attainment_pct == Decimal("0.8")

        # Carol solo: 15000 bookings, 20000 quota → 75%
        carol_att = [a for a in result.attainment if a.payee_id == "carol"][0]
        assert carol_att.bookings == Decimal("15000")
        assert carol_att.quota == Decimal("20000")
        assert carol_att.attainment_pct == Decimal("0.75")

    def test_tiered_rule_uses_team_attainment(self) -> None:
        """Tiered commission rate depends on TEAM attainment, not individual."""
        engine = CommissionEngine()
        plan = Plan(
            plan_id="P1", name="Test", period_type="monthly", currency="USD",
            rules=[TieredRule(
                type="tiered", id="R1",
                tiers=[
                    Tier(threshold_pct=Decimal("0.5"), rate=Decimal("0.05")),
                    Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.10")),
                ],
            )],
        )
        payees = [
            _payee("alice", "5000", team_id="team-a"),
            _payee("bob", "5000", team_id="team-a"),
        ]
        # Team quota = 10000. Alice books 6000 alone → team is at 60%.
        # Tier 1 (up to 50%): 5000 × 0.05 = 250
        # Tier 2 (50-60%): 1000 × 0.10 = 100
        # Total: 350
        txns = [_txn("T1", "alice", "6000")]
        result = engine.calculate(plan, txns, payees)

        alice_total = sum(c.commission_amount for c in result.commissions if c.payee_id == "alice")
        assert alice_total == Decimal("350")

    def test_team_quota_across_multiple_windows(self) -> None:
        """Team quota resets per window. Q1 and Q2 are separate."""
        engine = CommissionEngine()
        plan = Plan(
            plan_id="P1", name="Test", period_type="quarterly", currency="USD",
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
        )
        payees = [
            _payee("alice", "15000", team_id="team-a"),
            _payee("bob", "15000", team_id="team-a"),
        ]
        txns = [
            _txn("T1", "alice", "10000", "2026-01"),  # Q1
            _txn("T2", "bob", "5000", "2026-01"),     # Q1
            _txn("T3", "alice", "20000", "2026-04"),   # Q2
        ]
        result = engine.calculate(plan, txns, payees)

        q1_alice = [a for a in result.attainment if a.payee_id == "alice" and a.period == "2026-Q1"]
        q2_alice = [a for a in result.attainment if a.payee_id == "alice" and a.period == "2026-Q2"]

        # Q1: team 15000 bookings / 30000 quota = 50%
        assert q1_alice[0].bookings == Decimal("15000")
        assert q1_alice[0].quota == Decimal("30000")
        assert q1_alice[0].attainment_pct == Decimal("0.5")

        # Q2: only alice booked 20000, but team quota still 30000 → 66.7%
        assert q2_alice[0].bookings == Decimal("20000")

    def test_team_id_blank_is_solo(self) -> None:
        """Empty team_id behaves as individual (backward compatible)."""
        engine = CommissionEngine()
        plan = _plan()
        payees = [_payee("alice", "10000")]
        txns = [_txn("T1", "alice", "5000")]
        result = engine.calculate(plan, txns, payees)

        att = [a for a in result.attainment if a.payee_id == "alice"][0]
        assert att.bookings == Decimal("5000")
        assert att.quota == Decimal("10000")
        assert att.attainment_pct == Decimal("0.5")

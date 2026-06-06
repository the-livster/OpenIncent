"""Tests for partial-period pro-rating (Plan.pro_rating: full/daily/zero)."""

from datetime import date
from decimal import Decimal

from icm_engine.engine import CommissionEngine, _compute_activity_fraction
from icm_engine.models import Draw, FlatRateRule, Payee, Plan


def _plan(pro_rating: str = "full") -> Plan:
    return Plan(
        plan_id="P1", name="Test", period_type="monthly", currency="USD",
        pro_rating=pro_rating,
        rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
    )


def _payee(id: str, quota: str = "10000", effective_from: date | None = None,
           effective_to: date | None = None) -> Payee:
    return Payee(
        id=id, name=id, quota=Decimal(quota), plan_id="P1",
        effective_from=effective_from or date(2026, 1, 1),
        effective_to=effective_to,
    )


class TestActivityFraction:
    def test_full_mode_always_one(self) -> None:
        p = _payee("A", effective_from=date(2026, 1, 15))
        f = _compute_activity_fraction(p, "2026-01", "monthly", "full")
        assert f == Decimal("1")

    def test_daily_mid_month_start(self) -> None:
        """Started Jan 15 → active 17 of 31 days."""
        p = _payee("A", effective_from=date(2026, 1, 15))
        f = _compute_activity_fraction(p, "2026-01", "monthly", "daily")
        assert f == Decimal(17) / Decimal(31)

    def test_daily_mid_month_end(self) -> None:
        """Left Jan 20 → active 20 of 31 days."""
        p = _payee("A", effective_to=date(2026, 1, 20))
        f = _compute_activity_fraction(p, "2026-01", "monthly", "daily")
        assert f == Decimal(20) / Decimal(31)

    def test_daily_full_month(self) -> None:
        """Active the entire month → fraction = 1."""
        p = _payee("A", effective_from=date(2026, 1, 1))
        f = _compute_activity_fraction(p, "2026-01", "monthly", "daily")
        assert f == Decimal("1")

    def test_daily_not_active_at_all(self) -> None:
        """Effective after the period → fraction = 0."""
        p = _payee("A", effective_from=date(2026, 2, 1))
        f = _compute_activity_fraction(p, "2026-01", "monthly", "daily")
        assert f == Decimal("0")

    def test_daily_quarterly(self) -> None:
        """Started Feb 15 in Q1 (Jan 1 - Mar 31 = 90 days)."""
        p = _payee("A", effective_from=date(2026, 2, 15))
        f = _compute_activity_fraction(p, "2026-Q1", "quarterly", "daily")
        # Jan: 31, Feb 1-14: 14 days inactive. Active from Feb 15 - Mar 31 = 45 days
        # Total days in Q1 2026: 31+28+31 = 90
        assert f == Decimal(45) / Decimal(90)

    def test_daily_annual(self) -> None:
        """Started July 1 in annual plan → half year."""
        p = _payee("A", effective_from=date(2026, 7, 1))
        f = _compute_activity_fraction(p, "2026", "annual", "daily")
        assert f == Decimal(184) / Decimal(365)  # July 1 - Dec 31 = 184 days

    def test_zero_mode_full_period(self) -> None:
        """Active the entire month → 1."""
        p = _payee("A", effective_from=date(2026, 1, 1))
        f = _compute_activity_fraction(p, "2026-01", "monthly", "zero")
        assert f == Decimal("1")

    def test_zero_mode_partial(self) -> None:
        """Started mid-month → 0 (no payout)."""
        p = _payee("A", effective_from=date(2026, 1, 15))
        f = _compute_activity_fraction(p, "2026-01", "monthly", "zero")
        assert f == Decimal("0")


class TestProRatedQuota:
    def test_daily_pro_rated_quota(self) -> None:
        """Quota is pro-rated for mid-month start."""
        engine = CommissionEngine()
        plan = _plan("daily")
        payees = [
            _payee("A", "31000", effective_from=date(2026, 1, 15)),  # 17/31 of month
        ]
        from icm_engine.models import Transaction
        txns = [
            Transaction(id="T1", payee_id="A", amount=Decimal("31000"),
                        period="2026-01", close_date=date(2026, 1, 20)),
        ]
        result = engine.calculate(plan, txns, payees)

        # Attainment: 31000 bookings / (31000 * 17/31) quota ≈ 182.35%
        att = [a for a in result.attainment if a.payee_id == "A"]
        assert len(att) == 1
        expected_quota = Decimal("31000") * Decimal(17) / Decimal(31)
        assert att[0].quota == expected_quota
        assert att[0].bookings == Decimal("31000")

    def test_full_no_pro_rating(self) -> None:
        """Default: full quota regardless of start date."""
        engine = CommissionEngine()
        plan = _plan("full")
        payees = [
            _payee("A", "31000", effective_from=date(2026, 1, 15)),
        ]
        from icm_engine.models import Transaction
        txns = [
            Transaction(id="T1", payee_id="A", amount=Decimal("31000"),
                        period="2026-01", close_date=date(2026, 1, 20)),
        ]
        result = engine.calculate(plan, txns, payees)
        att = [a for a in result.attainment if a.payee_id == "A"]
        assert att[0].quota == Decimal("31000")  # full quota

    def test_zero_no_quota(self) -> None:
        """Zero mode: partial period → quota = 0."""
        engine = CommissionEngine()
        plan = _plan("zero")
        payees = [
            _payee("A", "31000", effective_from=date(2026, 1, 15)),
        ]
        from icm_engine.models import Transaction
        txns = [
            Transaction(id="T1", payee_id="A", amount=Decimal("10000"),
                        period="2026-01", close_date=date(2026, 1, 20)),
        ]
        result = engine.calculate(plan, txns, payees)
        att = [a for a in result.attainment if a.payee_id == "A"]
        assert att[0].quota == Decimal("0")

    def test_zero_full_period_gets_full_quota(self) -> None:
        """Zero mode but active the full month → full quota."""
        engine = CommissionEngine()
        plan = _plan("zero")
        payees = [
            _payee("A", "31000", effective_from=date(2026, 1, 1)),
        ]
        from icm_engine.models import Transaction
        txns = [
            Transaction(id="T1", payee_id="A", amount=Decimal("10000"),
                        period="2026-01", close_date=date(2026, 1, 15)),
        ]
        result = engine.calculate(plan, txns, payees)
        att = [a for a in result.attainment if a.payee_id == "A"]
        assert att[0].quota == Decimal("31000")


class TestProRatedDraw:
    def test_daily_pro_rated_draw(self) -> None:
        """Draw minimum is pro-rated for mid-month start."""
        engine = CommissionEngine()
        plan = Plan(
            plan_id="P1", name="Test", period_type="monthly", currency="USD",
            pro_rating="daily",
            draw=Draw(amount=Decimal("3000"), recoverable=False),
            rules=[FlatRateRule(type="flat_rate", id="R1", rate=Decimal("0.10"))],
        )
        payees = [
            _payee("A", effective_from=date(2026, 1, 15)),  # 17/31 of month
        ]
        from icm_engine.models import Transaction
        txns = [
            Transaction(id="T1", payee_id="A", amount=Decimal("0"),
                        period="2026-01", close_date=date(2026, 1, 20)),
        ]
        result = engine.calculate(plan, txns, payees)
        # Earned: 0. Draw should be 3000 * 17/31 ≈ 1645.16
        draw_lines = [c for c in result.commissions if c.rule_id == "draw"]
        assert len(draw_lines) == 1
        expected = Decimal("3000") * Decimal(17) / Decimal(31)
        assert abs(draw_lines[0].commission_amount - expected) < Decimal("0.01")

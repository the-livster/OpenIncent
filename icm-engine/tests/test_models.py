from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from icm_engine.models import Commission, Payee, Period, Transaction


class TestPeriod:
    def test_parse(self) -> None:
        p = Period("2026-04")
        assert p.year == 2026
        assert p.month == 4
        assert p.value == "2026-04"

    def test_start_date(self) -> None:
        assert Period("2026-01").start_date == date(2026, 1, 1)

    def test_end_date(self) -> None:
        assert Period("2026-01").end_date == date(2026, 1, 31)
        assert Period("2026-12").end_date == date(2026, 12, 31)
        assert Period("2026-02").end_date == date(2026, 2, 28)

    def test_ordering(self) -> None:
        a, b, c = Period("2025-12"), Period("2026-01"), Period("2026-06")
        assert a < b < c
        assert c > b > a
        assert a <= Period("2025-12")
        assert a >= Period("2025-12")

    def test_equality(self) -> None:
        assert Period("2026-04") == Period("2026-04")
        assert Period("2026-04") == "2026-04"
        assert Period("2026-04") != "2026-05"
        assert Period("2026-04") != 42

    def test_hashable(self) -> None:
        d = {Period("2026-04"): "april"}
        assert d[Period("2026-04")] == "april"

    def test_str_repr(self) -> None:
        p = Period("2026-04")
        assert str(p) == "2026-04"
        assert repr(p) == "Period('2026-04')"


class TestTransaction:
    def test_valid(self) -> None:
        t = Transaction(
            id="T001",
            payee_id="P001",
            deal_id="D001",
            period="2026-04",
            amount=Decimal("1500.00"),
            close_date=date(2026, 4, 15),
        )
        assert t.id == "T001"
        assert t.amount == Decimal("1500.00")
        assert t.product is None
        assert t.metadata == {}

    def test_decimal_precision_preserved(self) -> None:
        t = Transaction(
            id="T001",
            payee_id="P001",
            deal_id="D001",
            period="2026-04",
            amount=Decimal("999.99"),
            close_date=date(2026, 4, 15),
        )
        data = t.model_dump()
        assert data["amount"] == Decimal("999.99")

    def test_json_round_trip(self) -> None:
        t = Transaction(
            id="T001",
            payee_id="P001",
            deal_id="D001",
            period="2026-04",
            amount=Decimal("1500.00"),
            product="Enterprise",
            close_date=date(2026, 4, 15),
            metadata={"region": "EMEA"},
        )
        json_str = t.model_dump_json()
        restored = Transaction.model_validate_json(json_str)
        assert restored.id == t.id
        assert restored.amount == t.amount
        assert restored.product == "Enterprise"
        assert restored.metadata == {"region": "EMEA"}

    def test_negative_amount_allowed(self) -> None:
        """Negative amounts are allowed for refunds/true-ups."""
        t = Transaction(
            id="T001", payee_id="P001", deal_id="D001",
            amount=Decimal("-1.00"),
            close_date=date(2026, 4, 15),
        )
        assert t.amount == Decimal("-1.00")

    def test_bad_period_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Transaction(
                id="T001",
                payee_id="P001",
                deal_id="D001",
                period="not-a-period",
                amount=Decimal("100"),
                close_date=date(2026, 4, 15),
            )

    def test_missing_required_field(self) -> None:
        with pytest.raises(ValidationError):
            Transaction(id="T001")  # type: ignore[call-arg]

    def test_period_derived_from_close_date(self) -> None:
        """period is derived from close_date when not provided."""
        t = Transaction(
            id="T1", payee_id="P1", amount=Decimal("100"),
            close_date=date(2026, 5, 10),
        )
        assert t.period == "2026-05"

    def test_period_derived_from_close_date_string(self) -> None:
        """period is derived from close_date string when not provided."""
        t = Transaction(
            id="T1", payee_id="P1", amount=Decimal("100"),
            close_date="2026-05-10",  # type: ignore[arg-type]
        )
        assert t.period == "2026-05"

    def test_missing_period_and_close_date_raises(self) -> None:
        """ValueError when neither period nor close_date is provided."""
        with pytest.raises(ValidationError, match="period or close_date"):
            Transaction(id="T1", payee_id="P1", amount=Decimal("100"))

    def test_explicit_period_overrides_close_date(self) -> None:
        """Explicit period is always used, even when close_date is present."""
        t = Transaction(
            id="T1", payee_id="P1", amount=Decimal("100"),
            period="2026-07", close_date=date(2026, 5, 10),
        )
        assert t.period == "2026-07"


class TestPayee:
    def test_valid(self) -> None:
        p = Payee(
            id="P001",
            name="Alice",
            quota=Decimal("100000.00"),
            plan_id="PLAN-A",
            effective_from=date(2026, 1, 1),
        )
        assert p.effective_to is None

    def test_negative_quota_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Payee(
                id="P001",
                name="Alice",
                quota=Decimal("-1"),
                plan_id="PLAN-A",
                effective_from=date(2026, 1, 1),
            )


class TestCommission:
    def test_valid(self) -> None:
        c = Commission(
            transaction_id="T001",
            payee_id="P001",
            period="2026-04",
            rule_id="flat_5pct",
            base_amount=Decimal("1500.00"),
            rate=Decimal("0.05"),
            commission_amount=Decimal("75.00"),
            notes="Flat 5% on deal",
        )
        assert c.commission_amount == Decimal("75.00")

    def test_decimal_precision(self) -> None:
        c = Commission(
            transaction_id="T001",
            payee_id="P001",
            period="2026-04",
            rule_id="test",
            base_amount=Decimal("333.33"),
            rate=Decimal("0.1234"),
            commission_amount=Decimal("41.132922"),
            notes="",
        )
        data = c.model_dump()
        assert data["base_amount"] == Decimal("333.33")
        assert data["commission_amount"] == Decimal("41.132922")


class TestParseCreditsSpec:
    """Unit tests for the credits-cell parser (G7)."""

    def test_blank_returns_none(self) -> None:
        from icm_engine.models import parse_credits_spec
        assert parse_credits_spec("") is None
        assert parse_credits_spec("   ") is None

    def test_compact_two_way(self) -> None:
        from icm_engine.models import parse_credits_spec
        credits = parse_credits_spec("P1:0.6;P2:0.4")
        assert credits is not None
        assert [(c.payee_id, c.split_pct) for c in credits] == [
            ("P1", Decimal("0.6")), ("P2", Decimal("0.4")),
        ]

    def test_percent_and_whitespace(self) -> None:
        from icm_engine.models import parse_credits_spec
        credits = parse_credits_spec(" P1 : 60% ; P2 : 40% ")
        assert credits is not None
        assert credits[0].split_pct == Decimal("0.6")
        assert credits[1].split_pct == Decimal("0.4")

    def test_overlay_kind(self) -> None:
        from icm_engine.models import parse_credits_spec
        credits = parse_credits_spec("P1:1.0;M1:0.05@overlay")
        assert credits is not None
        assert credits[1].kind == "overlay"
        assert credits[1].split_pct == Decimal("0.05")

    def test_json_array(self) -> None:
        from icm_engine.models import parse_credits_spec
        credits = parse_credits_spec(
            '[{"payee_id": "P1", "split_pct": "0.7"},'
            ' {"payee_id": "P2", "split_pct": "0.3"}]'
        )
        assert credits is not None
        assert credits[0].split_pct == Decimal("0.7")

    def test_invalid_share_is_value_error(self) -> None:
        from icm_engine.models import parse_credits_spec
        with pytest.raises(ValueError, match="Invalid credits share"):
            parse_credits_spec("P1:abc")

    def test_missing_colon_is_value_error(self) -> None:
        from icm_engine.models import parse_credits_spec
        with pytest.raises(ValueError, match="Invalid credits entry"):
            parse_credits_spec("P1=0.6")

    def test_invalid_json_is_value_error(self) -> None:
        from icm_engine.models import parse_credits_spec
        with pytest.raises(ValueError, match="Invalid credits JSON"):
            parse_credits_spec("[{bad json")

    def test_transaction_accepts_credits_string(self) -> None:
        """The Transaction before-validator parses a raw credits string."""
        t = Transaction(
            id="T1", payee_id="P1", amount=Decimal("100"), period="2026-06",
            credits="P1:0.5;P2:0.5",  # type: ignore[arg-type]
        )
        assert t.credits is not None and len(t.credits) == 2
        assert t.credits[0].split_pct == Decimal("0.5")


class TestRoundingPolicy:
    """Plan-level rounding policy parsing (display-boundary configuration)."""

    def test_plan_without_rounding_defaults_none(self) -> None:
        from icm_engine.models import Plan
        plan = Plan(plan_id="p", name="P", period_type="monthly", currency="USD")
        assert plan.rounding is None

    def test_plan_with_rounding_policy(self) -> None:
        from icm_engine.models import Plan
        plan = Plan.model_validate({
            "plan_id": "p", "name": "P", "period_type": "monthly", "currency": "USD",
            "rounding": {"mode": "half-even", "places": 0},
        })
        assert plan.rounding is not None
        assert plan.rounding.mode == "half-even"
        assert plan.rounding.places == 0

    def test_rounding_policy_defaults(self) -> None:
        from icm_engine.models import RoundingPolicy
        rp = RoundingPolicy()
        assert rp.mode == "half-up"
        assert rp.places == 2

    def test_rounding_policy_rejects_bad_mode(self) -> None:
        from icm_engine.models import RoundingPolicy
        with pytest.raises(ValueError):
            RoundingPolicy(mode="sideways")  # type: ignore[arg-type]

    def test_rounding_policy_rejects_bad_places(self) -> None:
        from icm_engine.models import RoundingPolicy
        with pytest.raises(ValueError):
            RoundingPolicy(places=99)

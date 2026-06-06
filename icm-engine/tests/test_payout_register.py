from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from icm_engine.models import Commission, Payee, Plan
from icm_engine.payout_register import (
    _round,
    generate_payout_register,
    register_path,
    write_register,
)


def test_round_half_up() -> None:
    assert _round(Decimal("64.595")) == Decimal("64.60")
    assert _round(Decimal("64.594")) == Decimal("64.59")
    assert _round(Decimal("100.005")) == Decimal("100.01")
    assert _round(Decimal("0.001")) == Decimal("0.00")
    assert _round(Decimal("0.009")) == Decimal("0.01")
    assert _round(Decimal("100.00")) == Decimal("100.00")


def test_generate_payout_register_basic() -> None:
    plan = Plan(plan_id="P1", name="Test Plan", period_type="monthly", currency="USD")
    payees = [
        Payee(id="A", name="Alice", quota=Decimal("10000"), plan_id="P1",
              effective_from=date(2026, 1, 1)),
        Payee(id="B", name="Bob", quota=Decimal("20000"), plan_id="P1",
              effective_from=date(2026, 1, 1)),
    ]
    commissions = [
        Commission(transaction_id="T1", payee_id="A", period="2026-04", rule_id="R1",
                   base_amount=Decimal("5000"), rate=Decimal("0.1"),
                   commission_amount=Decimal("500")),
        Commission(transaction_id="T2", payee_id="A", period="2026-04", rule_id="R2",
                   base_amount=Decimal("3000"), rate=Decimal("0.05"),
                   commission_amount=Decimal("150")),
        Commission(transaction_id="T3", payee_id="B", period="2026-04", rule_id="R1",
                   base_amount=Decimal("8000"), rate=Decimal("0.1"),
                   commission_amount=Decimal("800")),
        # Different period — excluded from register
        Commission(transaction_id="T4", payee_id="A", period="2026-05", rule_id="R1",
                   base_amount=Decimal("1000"), rate=Decimal("0.1"),
                   commission_amount=Decimal("100")),
    ]

    register = generate_payout_register(commissions, payees, plan, "2026-04", version=1)

    assert register.plan_id == "P1"
    assert register.plan_name == "Test Plan"
    assert register.period == "2026-04"
    assert register.currency == "USD"
    assert register.version == 1

    # Two payees
    assert len(register.lines) == 2
    alice = next(r for r in register.lines if r.payee_id == "A")
    bob = next(r for r in register.lines if r.payee_id == "B")

    assert alice.payee_name == "Alice"
    assert alice.gross_commission == Decimal("650")  # 500 + 150
    assert alice.rounded_payout == _round(Decimal("650"))

    assert bob.payee_name == "Bob"
    assert bob.gross_commission == Decimal("800")
    assert bob.rounded_payout == _round(Decimal("800"))

    # Total
    assert register.total_payout == _round(Decimal("1450"))


def test_rounding_preserves_precision() -> None:
    """Verify gross_commission stays exact, only rounded_payout uses cents."""
    plan = Plan(plan_id="P1", name="Test", period_type="monthly", currency="USD")
    payees = [
        Payee(id="A", name="Alice", quota=Decimal("10000"), plan_id="P1",
              effective_from=date(2026, 1, 1)),
    ]
    commissions = [
        Commission(transaction_id="T1", payee_id="A", period="2026-04", rule_id="R1",
                   base_amount=Decimal("3333.33"), rate=Decimal("0.055"),
                   commission_amount=Decimal("183.33315")),
    ]
    register = generate_payout_register(commissions, payees, plan, "2026-04", version=1)
    line = register.lines[0]
    # Gross stays exact
    assert line.gross_commission == Decimal("183.33315")
    # Rounded halves up
    assert line.rounded_payout == Decimal("183.33")


def test_register_rows_format() -> None:
    plan = Plan(plan_id="P1", name="Test", period_type="monthly", currency="CAD")
    payees = [
        Payee(id="X", name="Xavier", quota=Decimal("5000"), plan_id="P1",
              effective_from=date(2026, 1, 1)),
    ]
    commissions = [
        Commission(transaction_id="T1", payee_id="X", period="2026-04", rule_id="R1",
                   base_amount=Decimal("1000"), rate=Decimal("0.1"),
                   commission_amount=Decimal("100")),
    ]
    register = generate_payout_register(commissions, payees, plan, "2026-04", version=2)
    rows = register.to_rows()

    # One data row + totals row
    assert len(rows) == 2
    data_row = rows[0]
    assert data_row["Payee ID"] == "X"
    assert data_row["Payee Name"] == "Xavier"
    assert data_row["Currency"] == "CAD"
    assert data_row["Payout"] == "100.00"

    totals_row = rows[1]
    assert totals_row["Payee Name"] == "TOTAL"
    assert totals_row["Payout"] == "100.00"


def test_write_register_creates_xlsx() -> None:
    plan = Plan(plan_id="P1", name="Test", period_type="monthly", currency="USD")
    payees = [
        Payee(id="A", name="Alice", quota=Decimal("10000"), plan_id="P1",
              effective_from=date(2026, 1, 1)),
    ]
    commissions = [
        Commission(transaction_id="T1", payee_id="A", period="2026-04", rule_id="R1",
                   base_amount=Decimal("5000"), rate=Decimal("0.1"),
                   commission_amount=Decimal("500")),
    ]
    register = generate_payout_register(commissions, payees, plan, "2026-04", version=1)

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "register.xlsx"
        write_register(register, commissions, path)
        assert path.exists()
        assert path.stat().st_size > 0


def test_register_path_naming() -> None:
    path = register_path(Path("/data"), "plan-A", "2026-04", 3)
    assert path.name == "payout_register_plan-A_2026-04_v3.xlsx"
    assert path.parent == Path("/data")

    # Special characters in plan_id are sanitized
    path2 = register_path(Path("/data"), "plan/A", "2026/04", 1)
    assert "/" not in str(path2.name)
    assert "\\" not in str(path2.name)


def test_empty_period_returns_empty_register() -> None:
    """Register for a period with no commissions should have zero lines."""
    plan = Plan(plan_id="P1", name="Test", period_type="monthly", currency="USD")
    payees = [
        Payee(id="A", name="Alice", quota=Decimal("10000"), plan_id="P1",
              effective_from=date(2026, 1, 1)),
    ]
    commissions = [
        Commission(transaction_id="T1", payee_id="A", period="2026-05", rule_id="R1",
                   base_amount=Decimal("100"), rate=Decimal("0.1"),
                   commission_amount=Decimal("10")),
    ]
    register = generate_payout_register(commissions, payees, plan, "2026-04", version=1)
    assert len(register.lines) == 0
    assert register.total_payout == Decimal("0.00")


def test_payee_with_no_name_falls_back_to_id() -> None:
    plan = Plan(plan_id="P1", name="Test", period_type="monthly", currency="USD")
    # Bob is in commissions but not in payee list
    payees = [
        Payee(id="A", name="Alice", quota=Decimal("10000"), plan_id="P1",
              effective_from=date(2026, 1, 1)),
    ]
    commissions = [
        Commission(transaction_id="T1", payee_id="B", period="2026-04", rule_id="R1",
                   base_amount=Decimal("100"), rate=Decimal("0.1"),
                   commission_amount=Decimal("10")),
    ]
    register = generate_payout_register(commissions, payees, plan, "2026-04", version=1)
    line = register.lines[0]
    assert line.payee_id == "B"
    assert line.payee_name == "B"  # falls back to ID

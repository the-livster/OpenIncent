"""Rule composition: a rule whose base is what an earlier rule paid.

"A kicker worth 20% of base commission" is the shape this exists for. Without
it, the only way to express one is to restate the base rule's whole rate table
inside a formula and keep the two in sync by hand.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from icm_engine.engine import CommissionEngine
from icm_engine.models import FlatRateRule, Payee, Plan, Tier, TieredRule, Transaction


def _flat(rid: str, rate: str = "0.10", **kw: object) -> FlatRateRule:
    return FlatRateRule(type="flat_rate", id=rid, rate=Decimal(rate), **kw)  # type: ignore[arg-type]


def _plan(*rules: object) -> Plan:
    return Plan(
        plan_id="p", name="P", currency="USD", period_type="monthly",
        rules=list(rules),  # type: ignore[arg-type]
    )


def _run(plan: Plan, txns: list[Transaction]) -> dict[tuple[str, str], Decimal]:
    payees = [Payee(id="P1", name="Rep", quota=Decimal("10000"), plan_id="p")]
    res = CommissionEngine().calculate(plan, txns, payees)
    # Tiered rules emit one line per slice, so sum rather than overwrite.
    out: dict[tuple[str, str], Decimal] = {}
    for c in res.commissions:
        k = (c.transaction_id, c.rule_id)
        out[k] = out.get(k, Decimal("0")) + c.commission_amount
    return out


def _txn(tid: str, amount: str = "10000", product: str | None = None) -> Transaction:
    return Transaction(
        id=tid, payee_id="P1", period="2026-05", amount=Decimal(amount), product=product,
    )


class TestComposition:
    def test_kicker_pays_a_share_of_the_base_rule(self) -> None:
        out = _run(
            _plan(_flat("BASE"), _flat("KICKER", "0.20", on_rule="BASE")),
            [_txn("T1")],
        )
        assert out[("T1", "BASE")] == Decimal("1000.00")
        assert out[("T1", "KICKER")] == Decimal("200.0000")  # 20% of 1000, not of 10000

    def test_composed_rule_can_be_filtered_independently(self) -> None:
        out = _run(
            _plan(
                _flat("BASE"),
                _flat("KICKER", "0.20", on_rule="BASE", filter='product == "Enterprise"'),
            ),
            [_txn("T1", product="Enterprise"), _txn("T2", product="SMB")],
        )
        assert out[("T1", "KICKER")] == Decimal("200.0000")
        assert ("T2", "KICKER") not in out

    def test_reads_the_capped_payout_not_the_uncapped_one(self) -> None:
        # BASE would pay 1,000 but is capped at 400; the kicker pays 20% of 400.
        out = _run(
            _plan(_flat("BASE", cap=Decimal("400")), _flat("KICKER", "0.20", on_rule="BASE")),
            [_txn("T1")],
        )
        # A cap does not rewrite the deal's line; it adds a negative "*"
        # adjustment for the payee-period. BASE therefore nets to 400.
        assert out[("T1", "BASE")] == Decimal("1000.00")
        assert out[("*", "BASE")] == Decimal("-600.00")
        # The kicker is 20% of the 400 actually paid, not of the 1,000 gross.
        assert out[("T1", "KICKER")] == Decimal("80.000")

    def test_a_deal_the_base_rule_ignored_earns_no_kicker(self) -> None:
        out = _run(
            _plan(
                _flat("BASE", filter='product == "Enterprise"'),
                _flat("KICKER", "0.20", on_rule="BASE"),
            ),
            [_txn("T1", product="SMB")],
        )
        assert out == {}

    def test_chained_composition(self) -> None:
        out = _run(
            _plan(
                _flat("A"),
                _flat("B", "0.50", on_rule="A"),
                _flat("C", "0.50", on_rule="B"),
            ),
            [_txn("T1")],
        )
        assert out[("T1", "A")] == Decimal("1000.00")
        assert out[("T1", "B")] == Decimal("500.000")
        assert out[("T1", "C")] == Decimal("250.0000")

    def test_composed_on_a_tiered_rule(self) -> None:
        base = TieredRule(type="tiered", id="BASE", tiers=[
            Tier(threshold_pct=Decimal("1.0"), rate=Decimal("0.05")),
            Tier(threshold_pct=Decimal("1.5"), rate=Decimal("0.10")),
        ])
        out = _run(_plan(base, _flat("KICKER", "0.20", on_rule="BASE")), [_txn("T1", "12000")])
        # 10,000 @ 5% + 2,000 @ 10% = 700; kicker is 20% of the whole 700.
        assert sum(v for (_, rid), v in out.items() if rid == "BASE") == Decimal("700.000")
        assert sum(v for (_, rid), v in out.items() if rid == "KICKER") == Decimal("140.00000")


class TestCompositionValidation:
    """Evaluation is one pass in plan order, so a reference must point back."""

    @pytest.mark.parametrize(
        ("label", "rules"),
        [
            ("forward", [_flat("A", on_rule="B"), _flat("B")]),
            ("unknown", [_flat("A"), _flat("B", on_rule="NOPE")]),
        ],
    )
    def test_reference_must_point_at_an_earlier_rule(
        self, label: str, rules: list[FlatRateRule]
    ) -> None:
        with pytest.raises(ValueError, match="not a rule defined before it"):
            _plan(*rules)

    def test_self_reference_rejected(self) -> None:
        with pytest.raises(ValueError, match="pointing at itself"):
            _plan(_flat("A", on_rule="A"))

    def test_on_rule_and_margin_basis_are_mutually_exclusive(self) -> None:
        with pytest.raises(ValueError, match="cannot both apply"):
            _plan(_flat("A"), _flat("B", on_rule="A", base="margin"))

    def test_plain_plans_are_unaffected(self) -> None:
        assert _plan(_flat("A"), _flat("B")).rules[1].on_rule is None

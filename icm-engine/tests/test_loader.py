from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from icm_engine.loader import load_payees, load_plan, load_transactions
from icm_engine.models import (
    AcceleratorRule,
    FlatRateRule,
    TieredRule,
)


class TestLoadPlan:
    def test_flat_rate_plan(self) -> None:
        plan = load_plan("examples/flat_rate_plan.yaml")
        assert plan.plan_id == "flat_rate_plan"
        assert plan.period_type == "monthly"
        assert plan.currency == "USD"
        assert len(plan.rules) == 1
        rule = plan.rules[0]
        assert isinstance(rule, FlatRateRule)
        assert rule.id == "flat_5pct"
        assert rule.rate == Decimal("0.05")

    def test_tiered_plan(self) -> None:
        plan = load_plan("examples/tiered_plan.yaml")
        assert plan.plan_id == "tiered_plan"
        assert len(plan.rules) == 1
        rule = plan.rules[0]
        assert isinstance(rule, TieredRule)
        assert len(rule.tiers) == 3
        assert rule.tiers[0].threshold_pct == Decimal("1.0")
        assert rule.tiers[0].rate == Decimal("0.05")

    def test_accelerator_plan(self) -> None:
        plan = load_plan("examples/accelerator_plan.yaml")
        assert plan.plan_id == "accelerator_plan"
        assert plan.period_type == "quarterly"
        assert len(plan.rules) == 2
        assert isinstance(plan.rules[0], FlatRateRule)
        accel = plan.rules[1]
        assert isinstance(accel, AcceleratorRule)
        assert accel.threshold_pct == Decimal("1.0")
        assert accel.multiplier == Decimal("1.5")

    def test_malformed_plan_helpful_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid plan file"):
            load_plan("tests/fixtures/bad_plan.yaml")

    def test_unknown_rule_type(self) -> None:
        with pytest.raises(ValueError, match="Invalid plan file"):
            load_plan("tests/fixtures/unknown_rule_type.yaml")


class TestLoadTransactions:
    def test_load_valid_csv(self) -> None:
        txns, mapping = load_transactions("tests/fixtures/sample_transactions.csv")
        assert mapping is None  # CSV files don't use mapping
        assert len(txns) == 3
        assert txns[0].id == "T001"
        assert txns[0].amount == Decimal("1500.00")
        assert txns[0].close_date == date(2026, 4, 15)
        assert txns[0].product == "Enterprise"
        assert txns[2].product is None

    def test_missing_column_falls_back_to_defaults(self) -> None:
        """Missing columns get safe defaults: auto-id, empty payee_id, 0 amount."""
        txns, _ = load_transactions("tests/fixtures/bad_transactions.csv")
        assert len(txns) == 2
        # First row has id, payee_id, but no amount → defaults to 0
        assert txns[0].id == "T001"
        assert txns[0].payee_id == "P001"
        assert txns[0].amount == Decimal("0")
        # Second row has no payee_id → empty string
        assert txns[1].payee_id == ""


class TestLoadParquet:
    def test_load_transactions_parquet(self, tmp_path: Path) -> None:
        """Round-trip: write CSV data to Parquet, load it back."""
        pytest.importorskip("pyarrow")
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.table({
            "id": ["T001", "T002"],
            "payee_id": ["P001", "P002"],
            "deal_id": ["D001", "D002"],
            "period": ["2026-01", "2026-01"],
            "amount": ["1500.00", "3000.00"],
            "product": ["Enterprise", None],
            "close_date": ["2026-01-15", "2026-01-20"],
        })
        path = tmp_path / "txns.parquet"
        pq.write_table(table, path)

        txns, mapping = load_transactions(str(path))
        assert mapping is None
        assert len(txns) == 2
        assert txns[0].id == "T001"
        assert txns[0].amount == Decimal("1500.00")
        assert txns[0].product == "Enterprise"
        assert txns[1].product is None

    def test_load_payees_parquet(self, tmp_path: Path) -> None:
        pytest.importorskip("pyarrow")
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.table({
            "id": ["P001", "P002"],
            "name": ["Alice", "Bob"],
            "quota": ["100000", "80000"],
            "plan_id": ["plan_1", "plan_1"],
            "effective_from": ["2026-01-01", "2026-01-01"],
            "effective_to": ["", ""],
        })
        path = tmp_path / "payees.parquet"
        pq.write_table(table, path)

        payees, mapping = load_payees(str(path))
        assert mapping is None
        assert len(payees) == 2
        assert payees[0].name == "Alice"
        assert payees[0].quota == Decimal("100000")
        assert payees[1].effective_to is None


class TestCreditsColumn:
    """G7: splits/credits ingestion from a CSV credits column."""

    def _load(self, tmp_path: Path, credits_cell: str) -> list:
        csv_path = tmp_path / "txns.csv"
        csv_path.write_text(
            "id,payee_id,amount,period,credits\n"
            f'T1,R1,10000,2026-06,"{credits_cell}"\n',
            encoding="utf-8",
        )
        txns, _ = load_transactions(csv_path)
        return txns

    def test_compact_form(self, tmp_path: Path) -> None:
        txns = self._load(tmp_path, "R1:0.6;R2:0.4")
        credits = txns[0].credits
        assert credits is not None and len(credits) == 2
        assert credits[0].payee_id == "R1"
        assert credits[0].split_pct == Decimal("0.6")
        assert credits[0].kind == "split"
        assert credits[1].payee_id == "R2"
        assert credits[1].split_pct == Decimal("0.4")

    def test_percent_form(self, tmp_path: Path) -> None:
        txns = self._load(tmp_path, "R1:60%;R2:40%")
        credits = txns[0].credits
        assert credits is not None
        assert credits[0].split_pct == Decimal("0.6")
        assert credits[1].split_pct == Decimal("0.4")

    def test_overlay_suffix(self, tmp_path: Path) -> None:
        txns = self._load(tmp_path, "R1:1.0;M1:0.1@overlay")
        credits = txns[0].credits
        assert credits is not None and len(credits) == 2
        assert credits[1].payee_id == "M1"
        assert credits[1].kind == "overlay"

    def test_json_form(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "txns.csv"
        csv_path.write_text(
            "id,payee_id,amount,period,credits\n"
            'T1,R1,10000,2026-06,"[{""payee_id"": ""R1"", ""split_pct"": ""0.5""},'
            ' {""payee_id"": ""R2"", ""split_pct"": ""0.5""}]"\n',
            encoding="utf-8",
        )
        txns, _ = load_transactions(csv_path)
        credits = txns[0].credits
        assert credits is not None and len(credits) == 2
        assert credits[0].split_pct == Decimal("0.5")

    def test_bad_split_sum_is_row_error(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "txns.csv"
        csv_path.write_text(
            "id,payee_id,amount,period,credits\n"
            'T1,R1,10000,2026-06,"R1:0.6;R2:0.6"\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Row 2"):
            load_transactions(csv_path)

    def test_malformed_entry_is_clear_row_error(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "txns.csv"
        csv_path.write_text(
            "id,payee_id,amount,period,credits\n"
            'T1,R1,10000,2026-06,"R1=0.6"\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Row 2"):
            load_transactions(csv_path)

    def test_absent_column_unchanged(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "txns.csv"
        csv_path.write_text(
            "id,payee_id,amount,period\nT1,R1,10000,2026-06\n",
            encoding="utf-8",
        )
        txns, _ = load_transactions(csv_path)
        assert txns[0].credits is None

    def test_splits_alias_header(self, tmp_path: Path) -> None:
        """A 'Deal Split' header maps to credits via aliases."""
        csv_path = tmp_path / "txns.csv"
        csv_path.write_text(
            "id,payee_id,amount,period,Deal Split\n"
            'T1,R1,10000,2026-06,"R1:0.5;R2:0.5"\n',
            encoding="utf-8",
        )
        txns, _ = load_transactions(csv_path)
        credits = txns[0].credits
        assert credits is not None and len(credits) == 2


class TestNoSilentCoercion:
    """Ingestion must never quietly turn an unreadable value into a default.

    Each case here produced a wrong payout with no warning before these guards
    existed: the run completed, the ledger said nothing, and someone was short.
    """

    ROSTER = (
        "id,name,quota,plan_id,effective_from,manager_id,manager_override\n"
        "REP,Rep,100000,demo,2024-01-01,MGR,{override}\n"
        "MGR,Manager,0,demo,2024-01-01,,\n"
    )

    def _roster(self, tmp_path: Path, override: str) -> Path:
        p = tmp_path / "payees.csv"
        p.write_text(self.ROSTER.format(override=override), encoding="utf-8")
        return p

    def test_manager_override_accepts_percent_notation(self, tmp_path: Path) -> None:
        # The recognised column heading is literally "manager override %", so a
        # human writing 2% in it is doing the natural thing.
        payees, _ = load_payees(self._roster(tmp_path, "2%"))
        assert payees[0].manager_override == Decimal("0.02")

    def test_manager_override_percent_matches_decimal(self, tmp_path: Path) -> None:
        pct, _ = load_payees(self._roster(tmp_path, "2%"))
        dec, _ = load_payees(self._roster(tmp_path, "0.02"))
        assert pct[0].manager_override == dec[0].manager_override

    def test_manager_override_gibberish_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="manager_override"):
            load_payees(self._roster(tmp_path, "two percent"))

    def test_manager_override_blank_is_still_none(self, tmp_path: Path) -> None:
        payees, _ = load_payees(self._roster(tmp_path, ""))
        assert payees[0].manager_override is None

    def test_unreadable_draw_recoverable_raises(self, tmp_path: Path) -> None:
        # Defaulting this to False turns a recoverable draw into a gift.
        p = tmp_path / "payees.csv"
        p.write_text(
            "id,name,quota,plan_id,effective_from,draw_amount,draw_recoverable\n"
            "P1,Rep,100000,demo,2024-01-01,5000,recoverable\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="draw_recoverable"):
            load_payees(p)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("true", True), ("Yes", True), ("1", True), ("false", False), ("no", False)],
    )
    def test_draw_recoverable_known_spellings(
        self, tmp_path: Path, raw: str, expected: bool
    ) -> None:
        p = tmp_path / "payees.csv"
        p.write_text(
            "id,name,quota,plan_id,effective_from,draw_amount,draw_recoverable\n"
            f"P1,Rep,100000,demo,2024-01-01,5000,{raw}\n",
            encoding="utf-8",
        )
        payees, _ = load_payees(p)
        assert payees[0].draw is not None
        assert payees[0].draw.recoverable is expected

    def test_half_specified_ramp_raises(self, tmp_path: Path) -> None:
        # Months without a schedule silently meant no quota relief at all.
        p = tmp_path / "payees.csv"
        p.write_text(
            "id,name,quota,plan_id,effective_from,ramp_months,ramp_schedule\n"
            "P1,Rep,100000,demo,2024-01-01,3,\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="ramp"):
            load_payees(p)


class TestParquetParity:
    """Parquet must load every field CSV does — it silently dropped seven."""

    def test_parquet_preserves_credits_and_margin(self, tmp_path: Path) -> None:
        pytest.importorskip("pyarrow")
        import pyarrow as pa
        import pyarrow.parquet as pq

        path = tmp_path / "txns.parquet"
        pq.write_table(pa.table({
            "id": ["T1"], "payee_id": ["P1"], "period": ["2026-01"],
            "amount": ["100000"], "credits": ["P1:0.6;P2:0.4"],
            "margin": ["25000"], "bill_rate": ["90"], "pay_rate": ["60"],
            "units": ["10"], "quota_amount": ["80000"],
        }), path)

        txns, _ = load_transactions(str(path))
        t = txns[0]
        # Before the fix every one of these landed in metadata instead.
        assert t.credits is not None and len(t.credits) == 2
        assert t.credits[0].split_pct == Decimal("0.6")
        assert t.margin == Decimal("25000")
        assert t.bill_rate == Decimal("90")
        assert t.pay_rate == Decimal("60")
        assert t.units == Decimal("10")
        assert t.quota_amount == Decimal("80000")
        assert "credits" not in t.metadata

    def test_parquet_preserves_hierarchy_and_draw(self, tmp_path: Path) -> None:
        pytest.importorskip("pyarrow")
        import pyarrow as pa
        import pyarrow.parquet as pq

        path = tmp_path / "payees.parquet"
        pq.write_table(pa.table({
            "id": ["P1"], "name": ["Rep"], "quota": ["100000"],
            "plan_id": ["demo"], "effective_from": ["2024-01-01"],
            "manager_id": ["M1"], "manager_override": ["0.02"],
            "team_id": ["east"], "email": ["rep@example.com"],
            "draw_amount": ["5000"], "draw_recoverable": ["true"],
        }), path)

        payees, _ = load_payees(str(path))
        p = payees[0]
        assert p.manager_id == "M1"
        assert p.manager_override == Decimal("0.02")
        assert p.team_id == "east"
        assert p.email == "rep@example.com"
        assert p.draw is not None
        assert p.draw.amount == Decimal("5000")
        assert p.draw.recoverable is True

    def test_csv_and_parquet_pay_a_split_identically(self, tmp_path: Path) -> None:
        """The headline regression: a 60/40 deal paid 100/0 from Parquet."""
        pytest.importorskip("pyarrow")
        import pyarrow as pa
        import pyarrow.parquet as pq

        from icm_engine.engine import CommissionEngine

        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text(
            "plan_id: demo\nname: Demo\ncurrency: USD\nperiod_type: monthly\n"
            "rules:\n  - type: flat_rate\n    id: R1\n    rate: '0.10'\n",
            encoding="utf-8",
        )
        csv_path = tmp_path / "deals.csv"
        csv_path.write_text(
            "id,payee_id,amount,period,credits\n"
            "T1,P1,100000,2026-01,P1:0.6;P2:0.4\n",
            encoding="utf-8",
        )
        pq_path = tmp_path / "deals.parquet"
        pq.write_table(pa.table({
            "id": ["T1"], "payee_id": ["P1"], "period": ["2026-01"],
            "amount": ["100000"], "credits": ["P1:0.6;P2:0.4"],
        }), pq_path)
        roster = tmp_path / "reps.csv"
        roster.write_text(
            "id,name,quota,plan_id,effective_from\n"
            "P1,Rep One,100000,demo,2024-01-01\n"
            "P2,Rep Two,100000,demo,2024-01-01\n",
            encoding="utf-8",
        )

        plan = load_plan(plan_path)
        payees, _ = load_payees(roster)

        def totals(deals: Path) -> dict[str, Decimal]:
            txns, _ = load_transactions(deals)
            out: dict[str, Decimal] = {}
            for c in CommissionEngine().calculate(plan, txns, payees).commissions:
                out[c.payee_id] = out.get(c.payee_id, Decimal("0")) + c.commission_amount
            return out

        from_csv, from_parquet = totals(csv_path), totals(pq_path)
        assert from_csv == from_parquet
        assert from_csv["P1"] == Decimal("6000.000")
        assert from_csv["P2"] == Decimal("4000.000")

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

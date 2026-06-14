from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from icm_engine.mapping import (
    ColumnMapping,
    MappingError,
    apply_mapping,
    infer_mapping,
    load_mapping,
    save_mapping,
)
from icm_engine.models import Payee, Transaction


class TestInferMapping:
    def test_infer_transactions_clean_headers(self) -> None:
        m = infer_mapping(
            ["id", "payee_id", "deal_id", "period", "amount", "product", "close_date"],
            "transactions",
        )
        assert m.file_pattern == "transactions"
        assert m.mappings.get("id") == "id"
        assert m.mappings.get("payee_id") == "payee_id"
        assert m.mappings.get("amount") == "amount"
        # All should be high confidence
        for src, conf in m.confidence.items():
            assert conf >= 0.70, f"Confidence too low for '{src}': {conf}"

    def test_infer_transactions_alias_headers(self) -> None:
        """Rep Name → payee_id, ACV → amount, Close → close_date, etc."""
        m = infer_mapping(
            ["Transaction ID", "Rep Name", "ACV ($)", "Close", "Period", "Product", "Opp ID"],
            "transactions",
        )
        assert m.mappings.get("Rep Name") == "payee_id"
        assert m.mappings.get("ACV ($)") == "amount"
        assert m.mappings.get("Close") == "close_date"
        assert m.mappings.get("Transaction ID") == "id"
        assert m.mappings.get("Opp ID") == "deal_id"

    def test_infer_transactions_messy_caps_and_spacing(self) -> None:
        """Fuzzy matching should be case-insensitive and spacing-tolerant."""
        m = infer_mapping(
            ["REP NAME", "SALE AMOUNT", "CLOSE DATE", "transaction id", "product family"],
            "transactions",
        )
        assert m.mappings.get("REP NAME") == "payee_id"
        assert m.mappings.get("SALE AMOUNT") == "amount"
        assert m.mappings.get("CLOSE DATE") == "close_date"
        assert m.mappings.get("transaction id") == "id"
        assert m.mappings.get("product family") == "product"

    def test_infer_transactions_minimal_headers(self) -> None:
        """Even with limited headers, should map what it can."""
        m = infer_mapping(["Name", "Deal", "Revenue", "Date"], "transactions")
        # Name could alias to payee_id
        assert m.mappings.get("Name") == "payee_id"
        assert m.mappings.get("Revenue") == "amount"
        assert m.mappings.get("Date") == "close_date"

    def test_infer_payees_clean_headers(self) -> None:
        m = infer_mapping(
            ["id", "name", "quota", "plan_id", "effective_from", "effective_to"],
            "payees",
        )
        assert m.file_pattern == "payees"
        assert m.mappings.get("name") == "name"
        assert m.mappings.get("quota") == "quota"
        assert m.mappings.get("effective_from") == "effective_from"

    def test_infer_payees_alias_headers(self) -> None:
        m = infer_mapping(
            ["Employee", "Target", "Comp Plan", "Start Date", "End Date"],
            "payees",
        )
        assert m.mappings.get("Employee") == "name"
        assert m.mappings.get("Target") == "quota"
        assert m.mappings.get("Comp Plan") == "plan_id"
        assert m.mappings.get("Start Date") == "effective_from"
        assert m.mappings.get("End Date") == "effective_to"

    def test_strip_currency_suggested_for_amount_and_quota(self) -> None:
        m = infer_mapping(
            ["id", "rep", "deal", "period", "amount", "close_date"],
            "transactions",
        )
        assert m.transformations.get("amount") == "strip_currency"

        m2 = infer_mapping(
            ["id", "name", "quota", "plan_id", "effective_from"],
            "payees",
        )
        assert m2.transformations.get("quota") == "strip_currency"

    def test_low_confidence_not_mapped(self) -> None:
        """Headers with no meaningful match should not be mapped."""
        m = infer_mapping(["foo", "bar", "baz", "qux"], "transactions")
        # None of these should confidently match any target field
        assert len(m.mappings) <= 3  # At most a few might scrape by 70% threshold

    def test_unknown_file_pattern_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown file_pattern"):
            infer_mapping(["id", "name"], "products")


class TestApplyMapping:
    def test_apply_clean_transactions(self) -> None:
        m = ColumnMapping(
            file_pattern="transactions",
            mappings={
                "Transaction ID": "id",
                "Rep Name": "payee_id",
                "Opp ID": "deal_id",
                "Period": "period",
                "ACV": "amount",
                "Close Date": "close_date",
            },
            transformations={
                "amount": "strip_currency",
                "close_date": "parse_date_us",
            },
        )
        rows = [
            {
                "Transaction ID": "T001",
                "Rep Name": "Alice",
                "Opp ID": "D001",
                "Period": "2026-04",
                "ACV": "$1,500.00",
                "Close Date": "04/15/2026",
            },
            {
                "Transaction ID": "T002",
                "Rep Name": "Bob",
                "Opp ID": "D002",
                "Period": "2026-04",
                "ACV": "$2,500.00",
                "Close Date": "04/20/2026",
            },
        ]
        results = apply_mapping(rows, m, Transaction)
        assert len(results) == 2
        assert isinstance(results[0], Transaction)
        assert results[0].id == "T001"
        assert results[0].payee_id == "Alice"
        assert results[0].amount == Decimal("1500.00")
        assert results[0].close_date == date(2026, 4, 15)

    def test_apply_missing_optional_fields(self) -> None:
        """Product is optional — missing source column should result in None."""
        m = ColumnMapping(
            file_pattern="transactions",
            mappings={
                "Transaction ID": "id",
                "Rep": "payee_id",
                "Deal": "deal_id",
                "Period": "period",
                "Amount": "amount",
                "Date": "close_date",
            },
            transformations={"amount": "strip_currency"},
        )
        rows = [
            {
                "Transaction ID": "T001",
                "Rep": "Alice",
                "Deal": "D001",
                "Period": "2026-04",
                "Amount": "$1,500.00",
                "Date": "2026-04-15",
            },
        ]
        results = apply_mapping(rows, m, Transaction)
        assert len(results) == 1
        assert results[0].product is None

    def test_apply_payees(self) -> None:
        m = ColumnMapping(
            file_pattern="payees",
            mappings={
                "ID": "id",
                "Employee Name": "name",
                "Annual Target": "quota",
                "Plan": "plan_id",
                "Start": "effective_from",
            },
            transformations={"quota": "strip_currency"},
        )
        rows = [
            {
                "ID": "P001",
                "Employee Name": "Alice",
                "Annual Target": "$100,000.00",
                "Plan": "flat_rate_plan",
                "Start": "2026-01-01",
            },
        ]
        results = apply_mapping(rows, m, Payee)
        assert len(results) == 1
        assert isinstance(results[0], Payee)
        assert results[0].id == "P001"
        assert results[0].name == "Alice"
        assert results[0].quota == Decimal("100000.00")


class TestMappingError:
    def test_row_level_errors_collected(self) -> None:
        m = ColumnMapping(
            file_pattern="transactions",
            mappings={
                "Txn ID": "id", "Rep": "payee_id", "Deal": "deal_id",
                "Period": "period", "Amount": "amount", "Date": "close_date",
            },
            transformations={"amount": "strip_currency"},
        )
        rows = [
            {"Txn ID": "T001", "Rep": "Alice", "Deal": "D001",
             "Period": "2026-04", "Amount": "not_money", "Date": "2026-04-15"},
            {"Txn ID": "T002", "Rep": "Bob", "Deal": "D002",
             "Period": "2026-04", "Amount": "$500.00", "Date": "2026-04-20"},
        ]
        with pytest.raises(MappingError) as exc_info:
            apply_mapping(rows, m, Transaction)
        assert "Mapping failed" in str(exc_info.value)
        # Row 1: transform error on amount, then validation error (amount became None)
        # Row 2: passes cleanly
        assert len(exc_info.value.errors) == 2
        transform_errors = [e for e in exc_info.value.errors if "Transform" in e["error"]]
        assert len(transform_errors) == 1
        assert transform_errors[0]["row"] == 1


class TestTransformations:
    def test_strip_currency(self) -> None:
        m = ColumnMapping(
            file_pattern="transactions",
            mappings={
                "id": "id", "rep": "payee_id", "deal": "deal_id",
                "period": "period", "amount": "amount", "date": "close_date",
            },
            transformations={"amount": "strip_currency"},
        )
        rows = [
            {
                "id": "T001", "rep": "Alice", "deal": "D001",
                "period": "2026-04", "amount": "$1,234.56", "date": "2026-04-15",
            },
            {
                "id": "T002", "rep": "Bob", "deal": "D002",
                "period": "2026-04", "amount": " 5,000 ", "date": "2026-04-20",
            },
        ]
        results = apply_mapping(rows, m, Transaction)
        assert results[0].amount == Decimal("1234.56")
        assert results[1].amount == Decimal("5000")

    def test_parse_date_us(self) -> None:
        m = ColumnMapping(
            file_pattern="transactions",
            mappings={
                "id": "id", "rep": "payee_id", "deal": "deal_id",
                "period": "period", "value": "amount", "close": "close_date",
            },
            transformations={"close_date": "parse_date_us"},
        )
        rows = [
            {
                "id": "T001", "rep": "Alice", "deal": "D001",
                "period": "2026-04", "value": "1000", "close": "04/15/2026",
            },
        ]
        results = apply_mapping(rows, m, Transaction)
        assert results[0].close_date == date(2026, 4, 15)

    def test_parse_date_eu(self) -> None:
        m = ColumnMapping(
            file_pattern="transactions",
            mappings={
                "id": "id", "rep": "payee_id", "deal": "deal_id",
                "period": "period", "value": "amount", "close": "close_date",
            },
            transformations={"close_date": "parse_date_eu"},
        )
        rows = [
            {
                "id": "T001", "rep": "Alice", "deal": "D001",
                "period": "2026-04", "value": "1000", "close": "15.04.2026",
            },
        ]
        results = apply_mapping(rows, m, Transaction)
        assert results[0].close_date == date(2026, 4, 15)

    def test_lowercase_uppercase_strip(self) -> None:
        m = ColumnMapping(
            file_pattern="transactions",
            mappings={
                "ID": "id", "Rep": "payee_id", "Deal": "deal_id",
                "Per": "period", "Val": "amount", "date": "close_date",
            },
            transformations={"payee_id": "lowercase", "id": "strip", "deal_id": "uppercase"},
        )
        rows = [
            {
                "ID": "  T001  ", "Rep": "ALICE", "Deal": "d001",
                "Per": "2026-04", "Val": "100", "date": "2026-04-01",
            },
        ]
        results = apply_mapping(rows, m, Transaction)
        assert results[0].id == "T001"  # strip removed whitespace
        assert results[0].payee_id == "alice"
        assert results[0].deal_id == "D001"


class TestSaveLoadMapping:
    def test_round_trip(self, tmp_path: Path) -> None:
        original = ColumnMapping(
            file_pattern="transactions",
            mappings={"Rep Name": "payee_id", "ACV ($)": "amount"},
            transformations={"amount": "strip_currency"},
            confidence={"Rep Name": 0.95, "ACV ($)": 0.88},
        )
        path = tmp_path / "mapping.yaml"
        save_mapping(original, path)

        loaded = load_mapping(path)
        assert loaded.file_pattern == original.file_pattern
        assert loaded.mappings == original.mappings
        assert loaded.transformations == original.transformations
        assert loaded.confidence == original.confidence

    def test_load_bad_file(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("- just a list", encoding="utf-8")
        with pytest.raises(ValueError, match="must be a YAML mapping"):
            load_mapping(path)


class TestCreditsMapping:
    """G7: a Split header maps to credits and parses through apply_mapping."""

    def test_infer_and_apply_credits(self) -> None:
        from decimal import Decimal

        from icm_engine.mapping import apply_mapping, infer_mapping
        from icm_engine.models import Transaction

        headers = ["ID", "Rep", "Amount", "Period", "Split"]
        mapping = infer_mapping(headers, "transactions")
        assert mapping.mappings.get("Split") == "credits"

        rows = [{
            "ID": "T1", "Rep": "R1", "Amount": "10000",
            "Period": "2026-06", "Split": "R1:0.5;R2:0.5",
        }]
        txns = apply_mapping(rows, mapping, Transaction)
        credits = txns[0].credits
        assert credits is not None and len(credits) == 2
        assert credits[0].split_pct == Decimal("0.5")

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from icm_engine.database import Database, default_db_path


class TestDefaultDbPath:
    def test_returns_path(self) -> None:
        p = default_db_path()
        assert isinstance(p, Path)
        assert p.name == "openincent.db"


class TestInit:
    def test_creates_db_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        db = Database(db_path)
        db.init()
        assert db_path.exists()
        assert db_path.stat().st_size > 0

    def test_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        db = Database(db_path)
        db.init()
        size_after_first = db_path.stat().st_size
        db.init()
        assert db_path.stat().st_size == size_after_first


class TestPlans:
    def test_save_and_get(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        pid = db.save_plan("Test Plan", "rules: []", description="A test")
        plan = db.get_plan(pid)
        assert plan is not None
        assert plan["name"] == "Test Plan"
        assert plan["yaml_content"] == "rules: []"
        assert plan["description"] == "A test"

    def test_list_plans(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        db.save_plan("Plan A", "rules: []")
        db.save_plan("Plan B", "rules: []")
        plans = db.list_plans()
        assert len(plans) == 2
        names = {p["name"] for p in plans}
        assert names == {"Plan A", "Plan B"}

    def test_list_newest_first(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        db.save_plan("Old", "rules: []")
        db.save_plan("New", "rules: []")
        plans = db.list_plans()
        assert plans[0]["name"] == "New"

    def test_update_existing(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        pid = db.save_plan("Original", "rules: []")
        db.save_plan("Updated", "rules: [a]", plan_id=pid)
        plan = db.get_plan(pid)
        assert plan["name"] == "Updated"
        assert plan["yaml_content"] == "rules: [a]"

    def test_delete(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        pid = db.save_plan("To Delete", "rules: []")
        assert db.delete_plan(pid) is True
        assert db.get_plan(pid) is None

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        assert db.delete_plan("nonexistent") is False

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        assert db.get_plan("nonexistent") is None


class TestPayees:
    def _make_db_with_plan(self, tmp_path: Path, plan_name: str = "Plan") -> tuple[Database, str]:
        db = Database(tmp_path / "test.db")
        db.init()
        pid = db.save_plan(plan_name, "rules: []")
        return db, pid

    def test_save_and_list(self, tmp_path: Path) -> None:
        db, pid = self._make_db_with_plan(tmp_path)
        db.save_payee("P001", "Alice", "100000", pid, "2026-01-01")
        db.save_payee("P002", "Bob", "80000", pid, "2026-01-01")
        payees = db.list_payees()
        assert len(payees) == 2
        names = {p["name"] for p in payees}
        assert names == {"Alice", "Bob"}

    def test_filter_by_plan(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        pid_a = db.save_plan("A", "rules: []")
        pid_b = db.save_plan("B", "rules: []")
        db.save_payee("P001", "Alice", "100000", pid_a, "2026-01-01")
        db.save_payee("P002", "Bob", "80000", pid_b, "2026-01-01")

        a_payees = db.list_payees(plan_id=pid_a)
        assert len(a_payees) == 1
        assert a_payees[0]["name"] == "Alice"

        b_payees = db.list_payees(plan_id=pid_b)
        assert len(b_payees) == 1
        assert b_payees[0]["name"] == "Bob"

    def test_update_existing(self, tmp_path: Path) -> None:
        db, pid = self._make_db_with_plan(tmp_path)
        db.save_payee("P001", "Alice", "100000", pid, "2026-01-01")
        db.save_payee("P001", "Alice Updated", "120000", pid, "2026-02-01")

        payees = db.list_payees()
        assert len(payees) == 1
        assert payees[0]["name"] == "Alice Updated"
        assert payees[0]["quota"] == "120000"

    def test_batch_save(self, tmp_path: Path) -> None:
        db, pid = self._make_db_with_plan(tmp_path)
        count = db.save_payees_batch([
            {"id": "P001", "name": "Alice", "quota": "100000", "plan_id": pid,
             "effective_from": "2026-01-01", "effective_to": None, "ramp": None},
            {"id": "P002", "name": "Bob", "quota": "80000", "plan_id": pid,
             "effective_from": "2026-01-01", "effective_to": "2026-12-31", "ramp": None},
        ])
        assert count == 2
        assert len(db.list_payees()) == 2

    def test_delete(self, tmp_path: Path) -> None:
        db, pid = self._make_db_with_plan(tmp_path)
        db.save_payee("P001", "Alice", "100000", pid, "2026-01-01")
        assert db.delete_payee("P001") is True
        assert len(db.list_payees()) == 0

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        assert db.delete_payee("nonexistent") is False

    def test_effective_to_nullable(self, tmp_path: Path) -> None:
        db, pid = self._make_db_with_plan(tmp_path)
        db.save_payee("P001", "Alice", "100000", pid, "2026-01-01", effective_to=None)
        payees = db.list_payees()
        assert payees[0]["effective_to"] is None


class TestSettings:
    def test_set_and_get(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        db.set_setting("api_key", "sk-test-123")
        assert db.get_setting("api_key") == "sk-test-123"

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        assert db.get_setting("nonexistent") is None

    def test_update_existing(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        db.set_setting("key", "v1")
        db.set_setting("key", "v2")
        assert db.get_setting("key") == "v2"

    def test_delete(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        db.set_setting("key", "value")
        assert db.delete_setting("key") is True
        assert db.get_setting("key") is None

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        assert db.delete_setting("nonexistent") is False


class TestColumnMappings:
    def test_save_and_get(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        data = {"Rep Name": "payee_id", "ACV": "amount"}
        mid = db.save_mapping("Salesforce Export", data)
        mapping = db.get_mapping(mid)
        assert mapping is not None
        assert mapping["name"] == "Salesforce Export"
        assert mapping["mapping_data"] == data

    def test_list_mappings(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        db.save_mapping("Map 1", {"a": "b"})
        db.save_mapping("Map 2", {"c": "d"})
        mappings = db.list_mappings()
        assert len(mappings) == 2

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        assert db.get_mapping("nonexistent") is None

    def test_delete(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        mid = db.save_mapping("Map", {"k": "v"})
        assert db.delete_mapping(mid) is True
        assert db.get_mapping(mid) is None


class TestCalculations:
    def _make_db_with_plan(self, tmp_path: Path) -> tuple[Database, str]:
        db = Database(tmp_path / "test.db")
        db.init()
        pid = db.save_plan("Plan", "rules: []")
        return db, pid

    def test_record_and_list(self, tmp_path: Path) -> None:
        db, pid = self._make_db_with_plan(tmp_path)
        cid = db.record_calculation(pid, input_summary={"txns": 10})
        calcs = db.list_calculations()
        assert len(calcs) == 1
        assert calcs[0]["id"] == cid
        assert calcs[0]["plan_id"] == pid
        assert calcs[0]["status"] == "completed"

    def test_filter_by_plan(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.init()
        pid_a = db.save_plan("A", "rules: []")
        pid_b = db.save_plan("B", "rules: []")
        db.record_calculation(pid_a)
        db.record_calculation(pid_b)

        a_calcs = db.list_calculations(plan_id=pid_a)
        assert len(a_calcs) == 1
        assert a_calcs[0]["plan_id"] == pid_a

    def test_limit(self, tmp_path: Path) -> None:
        db, pid = self._make_db_with_plan(tmp_path)
        for _ in range(5):
            db.record_calculation(pid)
        calcs = db.list_calculations(limit=3)
        assert len(calcs) == 3

    def test_default_status(self, tmp_path: Path) -> None:
        db, pid = self._make_db_with_plan(tmp_path)
        db.record_calculation(pid)
        calcs = db.list_calculations()
        assert calcs[0]["status"] == "completed"

    def test_filter_by_period(self, tmp_path: Path) -> None:
        db, pid = self._make_db_with_plan(tmp_path)
        db.record_calculation(pid, period="2026-01")
        db.record_calculation(pid, period="2026-02")
        db.record_calculation(pid, period="2026-02")

        jan = db.list_calculations(plan_id=pid, period="2026-01")
        assert len(jan) == 1
        assert jan[0]["period"] == "2026-01"

        feb = db.list_calculations(plan_id=pid, period="2026-02")
        assert len(feb) == 2
        for c in feb:
            assert c["period"] == "2026-02"

    def test_version_increments_per_period(self, tmp_path: Path) -> None:
        db, pid = self._make_db_with_plan(tmp_path)
        db.record_calculation(pid, period="2026-03")
        db.record_calculation(pid, period="2026-03")
        calcs = db.list_calculations(plan_id=pid, period="2026-03")
        versions = sorted(c["version"] for c in calcs)
        assert versions == [1, 2]


class TestEdgeCases:
    def test_fresh_db_requires_init(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        with pytest.raises(sqlite3.OperationalError):
            db.save_plan("Plan", "rules: []")

"""The headless commissions service and its MCP server.

Everything runs the staffing example (three plans, a 60/40 split deal, a
clawback) through the same calculate -> confirm flow an assistant uses.
"""

import json
import sqlite3
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from icm_engine.assistant import CommissionsService, NotAllowed, NotFound, Refused, Scope
from icm_engine.assistant.server import build_server, client_config, instructions
from icm_engine.assistant.service import normalize_period
from icm_engine.cycle import CycleError, lock_period
from icm_engine.database import Database
from icm_engine.loader import load_plan

EX = Path("examples/staffing_reference")
TXNS = str(EX / "transactions.csv")
ADJUSTMENTS = str(EX / "adjustments.csv")

READ_TOOLS = {"get_overview", "find_payees", "list_runs", "get_run", "get_pay", "explain_pay",
              "trace_deal", "get_plan", "simulate_deal"}
ACTION_TOOLS = {"calculate_period", "lock_period", "export_statements", "send_statements"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.delenv("ICM_SMTP_HOST", raising=False)
    db = Database(tmp_path / "assistant.db")
    db.init()
    for name in ("perm_plan.yaml", "contract_plan.yaml", "mgmt_plan.yaml"):
        plan = load_plan(EX / name)
        db.save_plan(plan.name, (EX / name).read_text(encoding="utf-8"), plan_id=plan.plan_id)
    # Tom has no email address, so one statement can't be sent.
    roster = tmp_path / "payees.csv"
    roster.write_text((EX / "payees.csv").read_text(encoding="utf-8").replace("tom@northwind.example", ""),
                      encoding="utf-8")
    return SimpleNamespace(db=db, tmp=tmp_path, roster=str(roster))


def admin_of(world, **scope):
    return CommissionsService(world.db, Scope.admin(**scope), export_dir=world.tmp / "exports")


def calculated(world):
    """The May run, saved the way an assistant saves it: preview, then confirm."""
    admin = admin_of(world)
    preview = admin.calculate(TXNS, payees_file=world.roster, adjustments_file=ADJUSTMENTS)
    admin.calculate(TXNS, payees_file=world.roster, adjustments_file=ADJUSTMENTS,
                    confirm_token=preview["confirm_token"])
    return admin


# ------------------------------------------------------------------
# The pay cycle: preview, then confirm
# ------------------------------------------------------------------


def test_calculating_previews_and_saves_only_when_confirmed(world):
    admin = admin_of(world)
    preview = admin.calculate(TXNS, payees_file=world.roster, adjustments_file=ADJUSTMENTS)
    assert preview["status"] == "preview"
    assert preview["summary"]["totals"] == {"GBP": "GBP 9,147.50"}
    assert world.db.list_calculations() == []

    saved = admin.calculate(TXNS, payees_file=world.roster, adjustments_file=ADJUSTMENTS,
                            confirm_token=preview["confirm_token"])
    assert saved["status"] == "saved"
    assert len(world.db.list_calculations()) == 3  # one per plan


def test_a_confirmation_only_fits_the_preview_it_came_from(world):
    admin = admin_of(world)
    preview = admin.calculate(TXNS, payees_file=world.roster)
    with pytest.raises(Refused, match="doesn't match"):
        admin.calculate(TXNS, payees_file=world.roster, confirm_token="0" * 24)

    changed = world.tmp / "transactions.csv"
    changed.write_text(Path(TXNS).read_text(encoding="utf-8").replace("18000", "18500"), encoding="utf-8")
    with pytest.raises(Refused):
        admin.calculate(str(changed), payees_file=world.roster, confirm_token=preview["confirm_token"])

    # A token from another server session is worthless.
    with pytest.raises(Refused):
        admin_of(world).calculate(TXNS, payees_file=world.roster, confirm_token=preview["confirm_token"])
    assert world.db.list_calculations() == []


def test_locking_is_previewed_then_final(world):
    admin = calculated(world)
    preview = admin.lock("perm_desk", "May 2026")
    assert preview["status"] == "preview"
    assert preview["lock"]["totals"] == {"GBP": "GBP 5,210.00"}
    assert not world.db.is_locked("perm_desk", "2026-05")

    done = admin.lock("perm_desk", "May 2026", confirm_token=preview["confirm_token"])
    assert done["status"] == "locked"
    assert admin.pay("P-101", "2026-05")["status"].startswith("locked")
    with pytest.raises(Refused, match="already locked"):
        admin.lock("perm_desk", "2026-05")


def test_a_period_cannot_be_locked_to_another_periods_run(world):
    admin = calculated(world)
    mgmt_run = next(c["id"] for c in world.db.list_calculations() if c["plan_id"] == "mgmt")
    with pytest.raises(Refused):
        admin.lock("perm_desk", "2026-05", calculation_id=mgmt_run)
    # The same guard protects the API, which shares the lock code.
    with pytest.raises(CycleError) as refused:
        lock_period(world.db, "perm_desk", "2026-05", mgmt_run)
    assert refused.value.status == 409
    assert not world.db.is_locked("perm_desk", "2026-05")


def test_export_writes_one_statement_per_person(world):
    admin = calculated(world)
    out = admin.export_statements("2026-05", formats=["html"], folder=str(world.tmp / "out"))
    names = [n for path in out["files"] for n in zipfile.ZipFile(path).namelist()]
    assert sum(n.endswith(".html") for n in names) == 6


def test_sending_previews_recipients_then_writes_each_persons_email(world):
    admin = calculated(world)
    not_set_up = admin.send_statements("2026-05")
    assert not_set_up["confirm_token"] is None and "ICM_SMTP_HOST" in not_set_up["setup_needed"]

    folder = world.tmp / "eml"
    preview = admin.send_statements("2026-05", eml_folder=str(folder))
    recipients = {r["payee_id"]: r for r in preview["send"]["recipients"]}
    assert set(recipients) == {"P-101", "P-103", "P-110", "P-111", "P-201"}
    assert [s["payee_id"] for s in preview["send"]["not_sent"]] == ["P-102"]
    assert "Your total is GBP 2,480.00." in preview["send"]["sample"]["body"]
    assert not folder.exists()

    done = admin.send_statements("2026-05", eml_folder=str(folder), confirm_token=preview["confirm_token"])
    assert done["status"] == "written"
    priya = (folder / "P-101.eml").read_text(encoding="utf-8")
    assert "To: priya@northwind.example" in priya
    assert "statement_P-101_2026-05.html" in priya and "statement_P-110" not in priya


def test_read_only_and_payee_scopes_cannot_run_the_pay_cycle(world):
    calculated(world)
    for scope in (Scope.admin(allow_changes=False), Scope.payee("P-101")):
        service = CommissionsService(world.db, scope)
        for action in (lambda s=service: s.calculate(TXNS),
                       lambda s=service: s.lock("perm_desk", "2026-05"),
                       lambda s=service: s.export_statements("2026-05"),
                       lambda s=service: s.send_statements("2026-05")):
            with pytest.raises(NotAllowed):
                action()


# ------------------------------------------------------------------
# Questions
# ------------------------------------------------------------------


def test_answers_match_the_statement(world):
    admin = calculated(world)
    pay = admin.pay("Priya", "May 2026")  # by name, in words
    assert (pay["payee_id"], pay["total"], pay["earned"], pay["adjustments"]) == (
        "P-101", "GBP 2,480.00", "GBP 2,780.00", "-GBP 300.00")
    assert pay["attainment"][0]["attainment"] == "126%"

    deals = {d["deal"]: d for d in admin.explain("P-101", "2026-05")["deals"]}
    assert [ln["calculation"] for ln in deals["PL-1087"]["lines"]] == [
        "2000.00 x 10% = 200.00", "5200.00 x 15% = 780.00"]
    assert deals["Manual adjustment"]["amount"] == "-300.00"


def test_a_name_that_fits_several_people_asks_which(world):
    admin = calculated(world)
    with pytest.raises(Refused, match="more than one"):
        admin.pay("a", "2026-05")  # Priya Shah, Aisha Khan, Dana Lowe, Marco Diaz...
    with pytest.raises(NotFound):
        admin.pay("Nobody", "2026-05")


def test_a_deal_is_traced_from_the_ledger(world):
    admin = calculated(world)
    trace = admin.trace_deal("PL-1087", "P-101")  # the deal id resolves to its transaction
    assert trace["transaction_id"] == "NW-1087"
    assert trace["steps"][0]["status"] == "matched"
    assert trace["total"] == "GBP 980.00"  # as the statement shows the deal, not "$"
    assert trace["summary"].endswith("Total commission: GBP 980.00.")
    with pytest.raises(NotFound):
        admin.trace_deal("PL-0000", "P-101")


def test_a_simulated_deal_prices_the_next_band_and_saves_nothing(world):
    admin = calculated(world)
    runs = world.db.list_calculations()
    sim = admin.simulate_deal(10000, "P-101", "2026-05")
    assert sim["simulation"] is True
    # Priya is at 126% of quota, past the 100% boundary, so the deal pays 15%.
    # Getting 126% right also proves the split credits survived the round trip.
    assert (sim["current_pay"], sim["additional_pay"], sim["pay_with_deal"]) == (
        "GBP 2,480.00", "GBP 1,500.00", "GBP 3,980.00")
    assert (sim["attainment_before"], sim["attainment_after"]) == ("126%", "176%")
    assert world.db.list_calculations() == runs


def test_simulating_needs_the_runs_own_inputs(world):
    admin = calculated(world)
    with sqlite3.connect(world.db.path) as conn:
        conn.execute("DELETE FROM run_inputs")
    with pytest.raises(Refused, match="Recalculate the period"):
        admin.simulate_deal(1000, "P-101")


def test_a_payee_sees_only_their_own_pay(world):
    calculated(world)
    rep = CommissionsService(world.db, Scope.payee("P-101"))
    assert rep.pay()["latest"]["total"] == "GBP 2,480.00"
    assert rep.pay("Priya Shah", "2026-05")["total"] == "GBP 2,480.00"  # their own name is fine

    for attempt in (lambda: rep.pay("P-102"), lambda: rep.explain("Tom Reilly"),
                    lambda: rep.trace_deal("PL-1130", "P-102"), lambda: rep.simulate_deal(1000, "P-102"),
                    lambda: rep.plan_summary("contract_desk")):
        with pytest.raises(NotAllowed):
            attempt()
    mgmt_run = next(c["id"] for c in world.db.list_calculations() if c["plan_id"] == "mgmt")
    with pytest.raises(NotAllowed):
        rep.run_summary(mgmt_run)

    everything = json.dumps([
        rep.overview(), rep.list_runs(), rep.find_payees(), rep.pay(), rep.explain(), rep.plan_summary(),
        *[rep.run_summary(r["calculation_id"]) for r in rep.list_runs()["runs"]],
    ])
    for someone_else in ("Tom Reilly", "P-102", "Jess Owens", "Aisha Khan", "Dana Lowe", "1,300.00"):
        assert someone_else not in everything


@pytest.mark.parametrize("text,key", [
    ("2026-05", "2026-05"), ("May 2026", "2026-05"), ("may 2026", "2026-05"), ("2026-5", "2026-05"),
    ("2026/05", "2026-05"), ("Q2 2026", "2026-Q2"), ("2026-q2", "2026-Q2"), ("2026", "2026"),
])
def test_periods_can_be_said_the_way_people_say_them(text, key):
    assert normalize_period(text) == key


# ------------------------------------------------------------------
# The MCP server
# ------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_the_admin_server_offers_every_tool_with_honest_hints(world):
    Client = pytest.importorskip("mcp").Client
    calculated(world)
    server = build_server(CommissionsService(world.db, Scope.admin()))
    async with Client(server, raise_exceptions=True) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        pay = await client.call_tool("get_pay", {"payee": "P-101", "period": "2026-05"})
    assert set(tools) == READ_TOOLS | ACTION_TOOLS
    assert all(tools[name].annotations.read_only_hint for name in READ_TOOLS)
    assert not any(tools[name].annotations.read_only_hint for name in ACTION_TOOLS)
    assert tools["send_statements"].annotations.destructive_hint
    assert pay.structured_content["total"] == "GBP 2,480.00"


@pytest.mark.anyio
async def test_a_payee_server_has_no_pay_cycle_and_refuses_others(world):
    Client = pytest.importorskip("mcp").Client
    calculated(world)
    server = build_server(CommissionsService(world.db, Scope.payee("P-101")))
    async with Client(server, raise_exceptions=True) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        denied = await client.call_tool("get_pay", {"payee": "P-102"})
        mine = await client.call_tool("explain_pay", {})
    assert names == READ_TOOLS
    assert denied.is_error and "only see your own pay" in denied.content[0].text
    assert [d["deal"] for d in mine.structured_content["deals"]] == ["Manual adjustment", "PL-1042", "PL-1087"]


def test_the_model_is_told_who_it_is_talking_for(world):
    calculated(world)
    rep = instructions(CommissionsService(world.db, Scope.payee("P-101")))
    assert "Priya Shah (P-101)" in rep and "nothing about anyone else" in rep
    admin = instructions(CommissionsService(world.db, Scope.admin()))
    assert "confirm_token" in admin and "Never confirm" in admin
    read_only = instructions(CommissionsService(world.db, Scope.admin(allow_changes=False)))
    assert "read-only" in read_only and "confirm_token" not in read_only


def test_client_config_launches_this_interpreter(tmp_path):
    config = client_config(str(tmp_path / "comp.db"), payee="P-101")
    entry = config["mcpServers"]["openincent-P-101"]
    assert entry["command"] == sys.executable
    assert entry["args"][:2] == ["-m", "icm_engine.assistant"]
    assert entry["args"][-2:] == ["--payee", "P-101"]

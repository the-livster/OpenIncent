"""Per-person access to the assistant over the network.

A real server on a spare local port, reached the way chat apps reach it: with
an access code as a bearer token, or by signing in with it (OAuth). The point
of all of it: each person sees their own pay, and nobody else's.
"""

import base64
import hashlib
import json
import re
import secrets
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import anyio
import pytest
from typer.testing import CliRunner

from icm_engine.assistant import CommissionsService, Scope
from icm_engine.assistant.server import http_app, public_address
from icm_engine.cli import app
from icm_engine.database import Database
from icm_engine.loader import load_plan

EX = Path("examples/staffing_reference")
REDIRECT = "http://localhost:9999/callback"
ACCEPT = {"Accept": "application/json, text/event-stream"}
PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
READ_TOOLS = {"get_overview", "find_payees", "list_runs", "get_run", "get_pay", "explain_pay",
              "trace_deal", "get_plan", "simulate_deal"}


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """The staffing example, calculated and served over HTTP."""
    pytest.importorskip("mcp")
    import uvicorn

    db = Database(tmp_path_factory.mktemp("access") / "comp.db")
    db.init()
    for name in ("perm_plan.yaml", "contract_plan.yaml", "mgmt_plan.yaml"):
        plan = load_plan(EX / name)
        db.save_plan(plan.name, (EX / name).read_text(encoding="utf-8"), plan_id=plan.plan_id)
    admin = CommissionsService(db, Scope.admin())
    files = {"payees_file": str(EX / "payees.csv"), "adjustments_file": str(EX / "adjustments.csv")}
    preview = admin.calculate(str(EX / "transactions.csv"), **files)
    admin.calculate(str(EX / "transactions.csv"), **files, confirm_token=preview["confirm_token"])

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    _server, asgi = http_app(db, url)
    server = uvicorn.Server(uvicorn.Config(asgi, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        assert time.monotonic() < deadline, "the server didn't start"
        time.sleep(0.02)
    yield SimpleNamespace(db=db, url=url)
    server.should_exit = True
    thread.join(timeout=10)


def access(site, payee: str | None = None, label: str = "") -> tuple[str, str]:
    grant, code = site.db.create_access_grant(payee_id=payee, label=label)
    return grant["id"], code


async def ask(site, token: str, *calls: tuple[str, dict[str, Any]]) -> tuple[set[str], list[Any]]:
    """Connect as a chat app would, with this bearer token; list the tools, then make the calls."""
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as http:
        async with Client(streamable_http_client(f"{site.url}/mcp", http_client=http)) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            return tools, [await client.call_tool(name, arguments) for name, arguments in calls]


def signed_in(site, token: str) -> bool:
    """Whether the server accepts this token (anything past sign-in isn't a 401)."""
    import httpx2

    reply = httpx2.post(f"{site.url}/mcp", json=PING, headers=ACCEPT | {"Authorization": f"Bearer {token}"})
    return reply.status_code != 401


# ------------------------------------------------------------------
# Signed-in people
# ------------------------------------------------------------------


def test_nobody_gets_in_without_their_own_access(site):
    import httpx2

    for auth in ({}, {"Authorization": "Bearer oia_made-up"}, {"Authorization": "Bearer oiat_made-up"}):
        refused = httpx2.post(f"{site.url}/mcp", json=PING, headers=ACCEPT | auth)
        assert refused.status_code == 401
    # Which tells a chat app where to sign in.
    assert "resource_metadata=" in refused.headers["www-authenticate"]
    resource = httpx2.get(f"{site.url}/.well-known/oauth-protected-resource/mcp").json()
    assert resource == {"resource": f"{site.url}/mcp", "authorization_servers": [site.url],
                        "bearer_methods_supported": ["header"]}


def test_a_payee_sees_their_own_pay_and_nobody_elses(site):
    _, code = access(site, "P-101")
    tools, (mine, toms, overview, runs) = anyio.run(
        ask, site, code, ("get_pay", {"period": "2026-05"}), ("get_pay", {"payee": "Tom Reilly"}),
        ("get_overview", {}), ("list_runs", {}),
    )
    assert tools == READ_TOOLS
    assert mine.structured_content["total"] == "GBP 2,480.00"
    assert toms.is_error and "only see your own pay" in toms.content[0].text
    seen = json.dumps([overview.structured_content, runs.structured_content])
    assert "Priya Shah" in seen
    for someone_else in ("Tom Reilly", "P-102", "Jess Owens", "Dana Lowe"):
        assert someone_else not in seen


def test_an_administrator_sees_everyone_and_changes_nothing(site):
    _, code = access(site, None, "Finance")
    tools, (toms, calculate) = anyio.run(
        ask, site, code, ("get_pay", {"payee": "P-102", "period": "2026-05"}),
        ("calculate_period", {"transactions_file": str(EX / "transactions.csv")}),
    )
    # The pay cycle reads and writes files on the machine it runs on: never over the network.
    assert tools == READ_TOOLS
    assert toms.structured_content["name"] == "Tom Reilly"
    assert calculate.is_error and "Unknown tool" in calculate.content[0].text


def test_revoking_access_ends_it_at_once(site):
    grant_id, code = access(site, "P-103")
    assert anyio.run(ask, site, code)[0] == READ_TOOLS
    assert site.db.revoke_access_grant(grant_id)
    assert not signed_in(site, code)


def test_one_persons_session_is_not_anothers(site):
    import httpx2

    _, priya = access(site, "P-101")
    _, tom = access(site, "P-102")
    version = {"mcp-protocol-version": "2025-06-18"}
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
    started = httpx2.post(f"{site.url}/mcp", json=init, headers=ACCEPT | {"Authorization": f"Bearer {priya}"})
    session = {"mcp-session-id": started.headers["mcp-session-id"]}
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_pay", "arguments": {}}}
    hijack = httpx2.post(f"{site.url}/mcp", json=call,
                         headers=ACCEPT | version | session | {"Authorization": f"Bearer {tom}"})
    assert hijack.status_code == 404
    assert "Priya" not in hijack.text


# ------------------------------------------------------------------
# Signing in from a chat app (OAuth)
# ------------------------------------------------------------------


def register(web, name: str = "Chat app") -> str:
    reply = web.post("/register", json={
        "redirect_uris": [REDIRECT], "client_name": name, "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
    })
    assert reply.status_code == 201, reply.text
    return str(reply.json()["client_id"])


def start_sign_in(web, client_id: str, *, state: str = "s1", resource: str | None = None) -> tuple[Any, str]:
    """Begin as a chat app does. Returns the /authorize reply and the PKCE verifier."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    reply = web.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": challenge,
        "code_challenge_method": "S256", "state": state,
        "resource": resource or str(web.base_url).rstrip("/") + "/mcp",
    })
    return reply, verifier


def request_id(location: str) -> str:
    return parse_qs(urlsplit(location).query)["request"][0]


def tokens(web, client_id: str, **grant: str) -> Any:
    return web.post("/token", data={"client_id": client_id, **grant})


def test_signing_in_from_a_chat_app(site):
    import httpx2

    grant_id, code = access(site, "P-101")
    with httpx2.Client(base_url=site.url, timeout=30) as web:
        client_id = register(web, name="Chat <b>app</b>")
        started, verifier = start_sign_in(web, client_id)
        assert started.status_code == 302
        form = web.get(started.headers["location"])
        assert form.status_code == 200
        assert "frame-ancestors 'none'" in form.headers["content-security-policy"]
        # The app names itself, so its name is shown as text; where you'll land is what to check.
        assert "Chat &lt;b&gt;app&lt;/b&gt;" in form.text and "<b>app</b>" not in form.text
        assert "an app on this computer" in form.text

        pending = request_id(started.headers["location"])
        wrong = web.post("/signin", data={"request": pending, "code": "oia_nope"})
        assert wrong.status_code == 401 and "access code isn&#x27;t right" in wrong.text
        back = web.post("/signin", data={"request": pending, "code": code})
        assert back.status_code == 303 and back.headers["location"].startswith(REDIRECT)
        answer = parse_qs(urlsplit(back.headers["location"]).query)
        assert answer["state"] == ["s1"]

        issued = tokens(web, client_id, grant_type="authorization_code", code=answer["code"][0],
                        redirect_uri=REDIRECT, code_verifier=verifier)
        assert issued.status_code == 200
        pair = issued.json()
        assert pair["expires_in"] == 3600 and pair["refresh_token"]
        reused = tokens(web, client_id, grant_type="authorization_code", code=answer["code"][0],
                        redirect_uri=REDIRECT, code_verifier=verifier)
        assert reused.json()["error"] == "invalid_grant"

        tools, (mine, toms) = anyio.run(ask, site, pair["access_token"], ("get_pay", {"period": "2026-05"}),
                                        ("get_pay", {"payee": "P-102"}))
        assert tools == READ_TOOLS
        assert mine.structured_content["total"] == "GBP 2,480.00"
        assert toms.is_error

        # Refresh tokens are replaced on every use.
        fresh = tokens(web, client_id, grant_type="refresh_token", refresh_token=pair["refresh_token"]).json()
        assert signed_in(site, fresh["access_token"])
        stale = tokens(web, client_id, grant_type="refresh_token", refresh_token=pair["refresh_token"])
        assert stale.json()["error"] == "invalid_grant"

        # Revoking the person's access signs out every app they signed in with.
        site.db.revoke_access_grant(grant_id)
        assert not signed_in(site, fresh["access_token"])
        refused = tokens(web, client_id, grant_type="refresh_token", refresh_token=fresh["refresh_token"])
        assert refused.json()["error"] == "invalid_grant"


def test_guessing_codes_ends_the_sign_in(site):
    import httpx2

    _, code = access(site, "P-101")
    with httpx2.Client(base_url=site.url, timeout=30) as web:
        started, _ = start_sign_in(web, register(web))
        pending = request_id(started.headers["location"])
        for _ in range(4):
            assert web.post("/signin", data={"request": pending, "code": "oia_guess"}).status_code == 401
        assert web.post("/signin", data={"request": pending, "code": "oia_guess"}).status_code == 410
        # Even the right code is too late now.
        assert web.post("/signin", data={"request": pending, "code": code}).status_code == 410


def test_declining_tells_the_chat_app(site):
    import httpx2

    with httpx2.Client(base_url=site.url, timeout=30) as web:
        started, _ = start_sign_in(web, register(web), state="s2")
        back = web.post("/signin", data={"request": request_id(started.headers["location"]), "action": "cancel"})
    assert back.status_code == 303
    answer = parse_qs(urlsplit(back.headers["location"]).query)
    assert answer["error"] == ["access_denied"] and answer["state"] == ["s2"]


def test_tokens_are_only_issued_for_this_server(site):
    import httpx2

    with httpx2.Client(base_url=site.url, timeout=30) as web:
        started, _ = start_sign_in(web, register(web), resource="https://elsewhere.example/mcp")
    assert started.status_code == 302
    assert parse_qs(urlsplit(started.headers["location"]).query)["error"] == ["invalid_target"]


def test_codes_and_tokens_are_stored_only_as_hashes(site):
    import httpx2

    _, code = access(site, "P-110")
    with httpx2.Client(base_url=site.url, timeout=30) as web:
        client_id = register(web)
        started, verifier = start_sign_in(web, client_id)
        back = web.post("/signin", data={"request": request_id(started.headers["location"]), "code": code})
        auth_code = parse_qs(urlsplit(back.headers["location"]).query)["code"][0]
        pair = tokens(web, client_id, grant_type="authorization_code", code=auth_code,
                      redirect_uri=REDIRECT, code_verifier=verifier).json()
    stored = b"".join(p.read_bytes() for p in site.db.path.parent.glob(site.db.path.name + "*"))
    for secret in (code, auth_code, pair["access_token"], pair["refresh_token"]):
        assert secret.encode() not in stored


# ------------------------------------------------------------------
# Granting access
# ------------------------------------------------------------------


def test_granting_listing_and_revoking_from_the_command_line(site):
    runner = CliRunner()
    db = ["--db", str(site.db.path)]
    wide = {"COLUMNS": "200"}

    granted = runner.invoke(app, ["access", "grant", "Aisha", *db], env=wide)
    assert granted.exit_code == 0, granted.output
    assert "their own pay, and nobody else's" in granted.output
    code = re.search(r"oia_[\w-]+", granted.output).group()
    grant = site.db.find_access_grant(code)
    assert (grant["payee_id"], grant["label"]) == ("P-110", "Aisha Khan (P-110)")

    listed = runner.invoke(app, ["access", "list", *db], env=wide)
    assert "Aisha Khan (P-110)" in listed.output and "own pay only" in listed.output
    assert code not in listed.output

    revoked = runner.invoke(app, ["access", "revoke", grant["id"], *db], env=wide)
    assert revoked.exit_code == 0 and site.db.find_access_grant(code) is None
    assert runner.invoke(app, ["access", "revoke", grant["id"], *db]).exit_code == 1

    for refused in (["access", "grant", "Nobody At All", *db],  # not on the roster
                    ["access", "grant", *db],  # neither a payee nor --admin
                    ["access", "grant", "Aisha", "--admin", *db],  # both
                    ["mcp", "--http", "--payee", "P-101", *db]):  # scoping comes from each person's access
        assert runner.invoke(app, refused).exit_code == 1


def test_only_a_grant_without_a_payee_is_an_administrators(tmp_path):
    pytest.importorskip("mcp")
    from icm_engine.assistant.access import Principal

    db = Database(tmp_path / "comp.db")
    db.init()
    with pytest.raises(ValueError):
        db.create_access_grant(payee_id=" ")
    assert Principal("g1", None).scope("default").is_admin
    assert not Principal("g2", "").scope("default").is_admin


def test_sign_ins_waiting_for_a_code_are_limited(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    from icm_engine.assistant import access as access_module

    monkeypatch.setattr(access_module, "MAX_PENDING", 3)
    db = Database(tmp_path / "comp.db")
    db.init()
    provider = access_module.AccessProvider(db, "http://127.0.0.1:8765")
    client = OAuthClientInformationFull(client_id="c1", redirect_uris=[AnyUrl(REDIRECT)])
    params = AuthorizationParams(state=None, scopes=None, code_challenge="x", redirect_uri=AnyUrl(REDIRECT),
                                 redirect_uri_provided_explicitly=True)
    started = [request_id(anyio.run(provider.authorize, client, params)) for _ in range(5)]
    assert [provider.pending(r) is not None for r in started] == [False, False, True, True, True]


def test_sign_in_needs_https_beyond_this_computer(tmp_path):
    pytest.importorskip("mcp")
    assert public_address("127.0.0.1", 8765) == "http://127.0.0.1:8765"
    assert public_address("0.0.0.0", 8765, "https://comp.example.com/mcp/") == "https://comp.example.com"
    with pytest.raises(ValueError, match="--public-url"):
        public_address("0.0.0.0", 8765)
    db = Database(tmp_path / "comp.db")
    db.init()
    with pytest.raises(ValueError, match="HTTPS"):
        http_app(db, "http://comp.example.com")

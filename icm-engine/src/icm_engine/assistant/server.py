"""OpenIncent as an MCP server, for Claude Desktop, Claude Code or any MCP client.

Two ways to serve it:

- `serve` (`icm mcp`): over stdio, for one person on the machine that holds the
  database. Whoever can open that file can read all of it, so this is for
  administrators; `--payee` only previews what one person would see.
- `serve_http` (`icm mcp --http`): a web service beside the database, for
  everyone else. Each person signs in with their own access (see `access.py`)
  and every tool call is answered for them alone. It is read-only: the
  pay-cycle tools take files on the machine they run on, so they stay local.

A local administrator also gets the pay-cycle tools unless read-only; a payee
never does. The MCP SDK is imported only inside functions here, so the engine
works without the `mcp` extra.
"""

import json
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field

from icm_engine import __version__
from icm_engine.assistant.service import AssistantError, CommissionsService, Scope
from icm_engine.database import Database, default_db_path

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

Payee = Annotated[str | None, Field(
    description="The payee's id or name, e.g. 'P-101' or 'Priya'. Leave empty when connected as a payee.",
)]
Period = Annotated[str | None, Field(
    description="Pay period, e.g. '2026-05', 'May 2026' or '2026-Q2'. Leave empty for the latest.",
)]
Confirm = Annotated[str | None, Field(
    description=("Leave empty to get a preview. Only after the person has seen that preview and clearly "
                 "agreed, pass the confirm_token it returned."),
)]
Format = Literal["html", "pdf", "xlsx"]

_COMMON = (
    "OpenIncent calculates sales commission. Figures come from saved runs and match the statements "
    "people receive to the penny: quote them, never recalculate them yourself. A period is either a "
    "draft (it can still change) or locked (final); say which. Amounts are in the plan's currency; "
    "never add different currencies together. explain_pay answers 'why was I paid this', and "
    "trace_deal walks one deal through the rules for disputes. simulate_deal is hypothetical: say so "
    "whenever you quote it, and never present it as pay."
)
_CHANGES = (
    "calculate_period, lock_period and send_statements change things. Call them once to get a "
    "preview, show the person that preview, and call again with its confirm_token only after they "
    "clearly agree. Never confirm on your own initiative, and never reuse a token for different "
    "arguments."
)


def instructions(service: CommissionsService) -> str:
    """What the model is told about this connection before it calls anything."""
    who = service.whoami()
    if who["role"] == "payee":
        return (
            f"You are connected as {who['name']} ({who['payee_id']}). You can see this person's own pay, "
            "deals, attainment and plan, and nothing about anyone else. " + _COMMON
        )
    if not service.scope.allow_changes:
        return (f"You are connected as an administrator of '{who['organisation']}', read-only: you can "
                "answer questions about anyone's pay but cannot run the pay cycle. " + _COMMON)
    return f"You are connected as an administrator of '{who['organisation']}'. " + _COMMON + " " + _CHANGES


def shared_instructions(organisation: str) -> str:
    """For a server shared by everyone with access: it answers for whoever signed in."""
    return (
        f"This is OpenIncent for '{organisation}'. It answers for whoever signed in: call get_overview "
        "first to learn who that is. A payee sees only their own pay; never guess at, estimate or compare "
        "it with anyone else's. Nothing can be changed from here. " + _COMMON
    )


def _new_server(instructions_text: str, **options: Any) -> "MCPServer":
    from mcp.server.mcpserver import MCPServer

    return MCPServer(
        "openincent",
        title="OpenIncent",
        description="Ask about commissions, and run the pay cycle.",
        instructions=instructions_text,
        website_url="https://openincent.com",
        version=__version__,
        log_level="WARNING",
        **options,
    )


def build_server(service: CommissionsService) -> Any:
    """The MCP server for one person, with a fixed scope (an `mcp.server.mcpserver.MCPServer`)."""
    mcp = _new_server(instructions(service))
    _add_tools(mcp, lambda: service, with_changes=service.scope.is_admin and service.scope.allow_changes)
    return mcp


def _add_tools(mcp: "MCPServer", resolve: Callable[[], CommissionsService], *, with_changes: bool) -> None:
    """Register the tools. Each call asks `resolve` for the service of whoever is calling."""
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    read = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    def call(method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return dict(getattr(resolve(), method)(*args, **kwargs))
        except AssistantError as e:
            # The model reads this and can recover: a better id, a period that exists.
            raise ToolError(str(e)) from e

    @mcp.tool(title="Overview", annotations=read)
    def get_overview() -> dict[str, Any]:
        """Start here: who you are connected as, the plans, and the latest pay periods with their
        totals and whether each is final."""
        return call("overview")

    @mcp.tool(title="Find payees", annotations=read)
    def find_payees(
        query: Annotated[str | None, Field(description="Part of a name or id. Leave empty to list everyone.")] = None,
    ) -> dict[str, Any]:
        """Look people up to get the payee id the other tools take."""
        return call("find_payees", query)

    @mcp.tool(title="List runs", annotations=read)
    def list_runs(
        period: Period = None,
        plan_id: Annotated[str | None, Field(description="Only this plan's runs.")] = None,
        limit: Annotated[int, Field(ge=1, le=200, description="How many, newest first.")] = 20,
    ) -> dict[str, Any]:
        """Saved calculations, newest first, with status and totals. A period can have several
        versions; the one marked current is the one that counts."""
        return call("list_runs", period, plan_id, limit)

    @mcp.tool(title="Run details", annotations=read)
    def get_run(
        calculation_id: Annotated[str, Field(description="A calculation_id from list_runs.")],
    ) -> dict[str, Any]:
        """One saved calculation: what it pays each person, and in total."""
        return call("run_summary", calculation_id)

    @mcp.tool(title="Pay", annotations=read)
    def get_pay(payee: Payee = None, period: Period = None) -> dict[str, Any]:
        """What someone is paid. With a period: total, earned vs adjustments, a breakdown by kind,
        quota attainment and status. Without one: every period's total, plus the latest in detail."""
        return call("pay", payee, period)

    @mcp.tool(title="Explain pay", annotations=read)
    def explain_pay(payee: Payee = None, period: Period = None) -> dict[str, Any]:
        """Every deal and adjustment behind someone's pay for a period, as their statement shows it:
        base, rate, the calculation for each tier band a deal crossed, and the plain-English reason.
        Use it to answer 'why was I paid this?'."""
        return call("explain", payee, period)

    @mcp.tool(title="Trace a deal", annotations=read)
    def trace_deal(
        deal: Annotated[str, Field(description="The deal id or transaction id, e.g. 'PL-1087'.")],
        payee: Payee = None,
        period: Period = None,
    ) -> dict[str, Any]:
        """How one deal was paid, rule by rule, straight from the audit ledger: which rules matched
        or were skipped, and why. For disputes."""
        return call("trace_deal", deal, payee, period)

    @mcp.tool(title="Plan rules", annotations=read)
    def get_plan(
        plan_id: Annotated[str | None, Field(
            description="A plan id. Leave empty to list plans, or (as a payee) to see your own plan.",
        )] = None,
    ) -> dict[str, Any]:
        """A commission plan's rules as the engine runs them: rates, tiers, accelerators, caps, draws."""
        return call("plan_summary", plan_id)

    @mcp.tool(title="Simulate a deal", annotations=read)
    def simulate_deal(
        amount: Annotated[float, Field(
            description="The deal's value in the plan's currency; negative for a cancellation.")],
        payee: Payee = None,
        period: Period = None,
        product: Annotated[str | None, Field(description="The deal's product, for rules that depend on it.")] = None,
    ) -> dict[str, Any]:
        """What if: re-run a saved period with one extra deal and see how pay and attainment would
        change. Hypothetical: nothing is saved. Say so when you quote it."""
        return call("simulate_deal", amount, payee, period, product)

    if not with_changes:
        return

    @mcp.tool(title="Calculate a period", annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False))
    def calculate_period(
        transactions_file: Annotated[str, Field(description="Full path to the period's deals (CSV, XLSX or Parquet).")],
        payees_file: Annotated[str | None, Field(
            description="Full path to a roster file. Leave empty to use the saved roster.")] = None,
        plan_file: Annotated[str | None, Field(
            description=("Full path to one plan (YAML) that pays everyone. Leave empty to pay each person "
                         "on their own plan from the saved library."))] = None,
        adjustments_file: Annotated[str | None, Field(description="Full path to manual adjustments (CSV).")] = None,
        mbos_file: Annotated[str | None, Field(description="Full path to MBO / bonus payouts (CSV).")] = None,
        effective_period: Period = None,
        allow_unknown_payees: Annotated[bool, Field(
            description="Pay deal ids that are not on the roster, as separate people. Only if the person says so.",
        )] = False,
        confirm_token: Confirm = None,
    ) -> dict[str, Any]:
        """Calculate commissions from files. The first call previews totals and warnings and saves
        nothing. Show that preview; only call again with its confirm_token once the person agrees.
        The saved run stays a draft until the period is locked."""
        return call("calculate", transactions_file, payees_file, plan_file, adjustments_file,
                    mbos_file, effective_period, allow_unknown_payees, confirm_token)

    @mcp.tool(title="Lock a period", annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False))
    def lock_period(
        plan_id: Annotated[str, Field(description="The plan whose period to lock.")],
        period: Annotated[str, Field(description="The period to lock, e.g. '2026-05'.")],
        calculation_id: Annotated[str | None, Field(
            description="Lock to this run. Leave empty for the latest calculation of the period.")] = None,
        reason: Annotated[str, Field(description="Why, for the audit trail.")] = "",
        confirm_token: Confirm = None,
    ) -> dict[str, Any]:
        """Make a period's figures final, and write the payout register. Later changes to its deals
        become true-ups in a later period. Preview first; confirm only after the person agrees."""
        return call("lock", plan_id, period, calculation_id, reason, confirm_token)

    @mcp.tool(title="Export statements", annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False))
    def export_statements(
        period: Period = None,
        calculation_ids: Annotated[list[str] | None, Field(
            description="Specific runs instead of a period's current results.")] = None,
        formats: Annotated[list[Format] | None, Field(
            description="Statement formats; html and pdf by default.")] = None,
        folder: Annotated[str | None, Field(
            description="Where to write the ZIP; the Downloads folder by default.")] = None,
    ) -> dict[str, Any]:
        """Write a period's statements to a folder: a ZIP with one private statement per person,
        plus an internal payout summary. Adds files; changes nothing else."""
        return call("export_statements", period, calculation_ids, list(formats or []) or None, folder)

    @mcp.tool(title="Send statements", annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True))
    def send_statements(
        period: Period = None,
        calculation_ids: Annotated[list[str] | None, Field(
            description="Specific runs instead of a period's current results.")] = None,
        formats: Annotated[list[Format] | None, Field(description="Attachment formats; html by default.")] = None,
        eml_folder: Annotated[str | None, Field(
            description="Write .eml files here to send from a mail client, instead of emailing.")] = None,
        confirm_token: Confirm = None,
    ) -> dict[str, Any]:
        """Email each person their own statement, and nobody else's. The first call previews the
        recipients, who has no email address, and a sample message; send only after the person
        agrees, with the confirm_token. Email needs ICM_SMTP_* settings, or use eml_folder."""
        return call("send_statements", period, calculation_ids,
                    list(formats or []) or None, eml_folder, confirm_token)


def build_http_server(db: Database, public_url: str) -> Any:
    """One read-only server for everyone with access, answering each request
    for the person who signed in: their own pay if a payee, everyone's if an
    administrator."""
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions

    from icm_engine.assistant.access import AccessProvider, current_principal, sign_in_page

    provider = AccessProvider(db, public_url)
    runs: dict[str, Any] = {}  # saved runs never change, so everyone shares one cache
    services: dict[str, CommissionsService] = {}
    lock = threading.Lock()  # tools run on worker threads

    def resolve() -> CommissionsService:
        who = current_principal()
        with lock:
            if who.grant_id not in services:
                services[who.grant_id] = CommissionsService(db, who.scope(db.org_id), runs=runs)
            return services[who.grant_id]

    mcp = _new_server(
        shared_instructions(db.org_id),
        auth_server_provider=provider,
        # From plain strings, so the settings keep the issuer exactly as given:
        # clients compare it character for character, and a trailing slash breaks that.
        auth=AuthSettings.model_validate({
            "issuer_url": provider.base_url,
            "resource_server_url": provider.resource,
            "validate_token_resource": True,
            "client_registration_options": ClientRegistrationOptions(enabled=True),
            "revocation_options": RevocationOptions(enabled=True),
        }),
    )
    _add_tools(mcp, resolve, with_changes=False)

    async def sign_in(request: Any) -> Any:
        return await sign_in_page(provider, request)

    mcp.custom_route("/signin", methods=["GET", "POST"], include_in_schema=False)(sign_in)
    return mcp


def http_app(db: Database, public_url: str, *, host: str = "127.0.0.1") -> tuple[Any, Any]:
    """The server, and the ASGI app serving it that answers only to its own address."""
    from urllib.parse import urlsplit

    from mcp.server.transport_security import TransportSecuritySettings

    server = build_http_server(db, public_url)
    public = urlsplit(public_url)
    local = ["127.0.0.1", "localhost", "[::1]"]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[public.netloc, f"{public.hostname}:*", *(f"{h}:*" for h in local)],
        allowed_origins=[f"{public.scheme}://{public.netloc}", *(f"http://{h}:*" for h in local)],
    )
    return server, server.streamable_http_app(transport_security=security, host=host)


def database_path(db_path: str | None = None) -> Path:
    """The database to serve: the one given, ICM_DB_PATH, or the app's own."""
    return Path(db_path or os.environ.get("ICM_DB_PATH") or default_db_path()).expanduser().resolve()


def serve(db_path: str | None = None, *, org: str = "default", payee: str | None = None,
          read_only: bool = False) -> None:
    """Run the MCP server on stdio until the client disconnects."""
    try:
        import mcp  # noqa: F401
    except ImportError:
        print("The MCP server needs the mcp extra: pip install 'icm-engine[mcp]'", file=sys.stderr)
        raise SystemExit(1) from None
    db = Database(database_path(db_path), org_id=org)
    db.init()
    scope = Scope.payee(payee, org) if payee else Scope.admin(org, allow_changes=not read_only)
    build_server(CommissionsService(db, scope)).run()


def public_address(host: str, port: int, public_url: str | None = None) -> str:
    """The address people's chat apps reach this server at (without /mcp)."""
    if public_url:
        url = public_url.strip().rstrip("/")
        return url[: -len("/mcp")] if url.endswith("/mcp") else url
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Pass --public-url with the https:// address people will connect to.")
    return f"http://{'[::1]' if host == '::1' else host}:{port}"


def serve_http(db_path: str | None = None, *, org: str = "default", host: str = "127.0.0.1",
               port: int = 8765, public_url: str | None = None) -> None:
    """Serve everyone with access over HTTP, until stopped."""
    try:
        import uvicorn
    except ImportError:
        print("The MCP server needs the mcp extra: pip install 'icm-engine[mcp]'", file=sys.stderr)
        raise SystemExit(1) from None
    db = Database(database_path(db_path), org_id=org)
    db.init()
    try:
        url = public_address(host, port, public_url)
        _server, app = http_app(db, url, host=host)
    except ValueError as e:
        print(f"{e}\nSign-in needs HTTPS: serve it behind a proxy or tunnel that provides HTTPS, "
              "and pass that address as --public-url.", file=sys.stderr)
        raise SystemExit(1) from None
    db.set_setting("assistant_url", f"{url}/mcp")
    people = len(db.list_access_grants())
    print(f"OpenIncent assistant at {url}/mcp, serving {db.path}", file=sys.stderr)
    print(f"{people} {'person has' if people == 1 else 'people have'} access. Add people with: "
          "icm access grant", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="warning")


def client_config(db_path: str | None = None, *, org: str = "default", payee: str | None = None,
                  read_only: bool = False) -> dict[str, Any]:
    """The entry an MCP client (Claude Desktop, Cursor...) needs to launch this server.

    Uses this interpreter's absolute path: clients start servers with a
    near-empty PATH, from their own working directory."""
    args = ["-m", "icm_engine.assistant", "--db", str(database_path(db_path))]
    if org != "default":
        args += ["--org", org]
    if payee:
        args += ["--payee", payee]
    if read_only:
        args.append("--read-only")
    name = f"openincent-{payee}" if payee else "openincent"
    return {"mcpServers": {name: {"command": sys.executable, "args": args}}}


def print_config(db_path: str | None = None, *, org: str = "default", payee: str | None = None,
                 read_only: bool = False) -> None:
    config = client_config(db_path, org=org, payee=payee, read_only=read_only)
    name, entry = next(iter(config["mcpServers"].items()))
    launch = " ".join(json.dumps(part) if " " in part else part for part in [entry["command"], *entry["args"]])
    print("Claude Desktop / Cursor (add to the mcpServers in the config file):")
    print(json.dumps(config, indent=2))
    print("\nClaude Code:")
    print(f"claude mcp add {name} -- {launch}")

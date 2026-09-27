"""Per-person access to the assistant over the network.

Nobody who uses the assistant this way is given the database. An administrator
grants each person access (`icm access grant`): a payee's grant sees their own
pay and nothing else; an administrator's sees everyone's. Everything here is
read-only: the pay-cycle tools read and write files on the machine they run on,
so they stay with the local server (`icm mcp` over stdio). Every grant has an
access code, shown once.

People sign in with that code from their chat app, which then holds tokens
issued here (OAuth 2.1, through the MCP SDK's authorization server): access
tokens that last an hour, and refresh tokens replaced each time they are used.
Clients that can send a header may use the code itself as a bearer token.
Revoking the grant ends all of it at once.

Needs the `mcp` extra; imported only when serving over HTTP.
"""

import html
import secrets
import time
from dataclasses import dataclass
from string import Template
from typing import Any
from urllib.parse import urlsplit

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from icm_engine.assistant.service import Scope
from icm_engine.database import ACCESS_CODE_PREFIX, Database

SIGN_IN_SECONDS = 10 * 60  # to find and type the code
CODE_SECONDS = 5 * 60  # for the chat app to collect its tokens
ACCESS_SECONDS = 60 * 60
REFRESH_SECONDS = 30 * 24 * 60 * 60
MAX_TRIES = 5  # wrong codes before a sign-in has to start again
MAX_PENDING = 1000  # sign-ins waiting for a code; anyone can start one, so there's a limit


@dataclass(frozen=True)
class Principal:
    """The person behind a request, as their grant says."""

    grant_id: str
    payee_id: str | None
    label: str = ""

    @classmethod
    def of(cls, grant_id: str, grant: dict[str, Any]) -> "Principal":
        return cls(grant_id, grant.get("payee_id"), grant.get("label") or "")

    def scope(self, org_id: str) -> Scope:
        # Only a grant with no payee at all is an administrator's.
        if self.payee_id is not None:
            return Scope.payee(self.payee_id, org_id)
        return Scope.admin(org_id, allow_changes=False)

    def claims(self) -> dict[str, Any]:
        return {"payee_id": self.payee_id, "label": self.label}


def current_principal() -> Principal:
    """Whoever sent the request being handled. Requests without a valid token
    never get this far: the SDK turns them away first."""
    token = get_access_token()
    if token is None or not token.subject:
        raise PermissionError("Not signed in.")
    return Principal.of(token.subject, token.claims or {})


class SignInError(Exception):
    def __init__(self, message: str, *, expired: bool = False) -> None:
        super().__init__(message)
        self.expired = expired


@dataclass
class _SignIn:
    """A chat app's request to sign someone in, waiting for their access code."""

    client_id: str
    client_name: str
    params: AuthorizationParams
    started: float
    tries: int = 0


class AccessProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """The SDK's authorization server, backed by the database's access grants.

    Any chat app may register (that is how claude.ai and Claude Desktop
    connect), but registering grants nothing: every token traces back to a
    person who signed in with their own access code.
    """

    def __init__(self, db: Database, base_url: str) -> None:
        self.db = db
        self.base_url = base_url.rstrip("/")
        self.resource = f"{self.base_url}/mcp"
        self._sign_ins: dict[str, _SignIn] = {}

    # ---------------- chat apps ----------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        info = self.db.get_oauth_client(client_id)
        return OAuthClientInformationFull.model_validate_json(info) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.db.save_oauth_client(client_info.client_id, client_info.model_dump_json())

    # ---------------- signing in ----------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource and params.resource.rstrip("/") not in (self.resource, self.base_url):
            raise AuthorizeError("invalid_target", "This server only issues access to itself.")
        self._forget_stale()
        while len(self._sign_ins) >= MAX_PENDING:  # the oldest go first
            del self._sign_ins[next(iter(self._sign_ins))]
        request_id = secrets.token_urlsafe(24)
        self._sign_ins[request_id] = _SignIn(client.client_id, client.client_name or "", params, time.time())
        return f"{self.base_url}/signin?request={request_id}"

    def pending(self, request_id: str) -> _SignIn | None:
        self._forget_stale()
        return self._sign_ins.get(request_id)

    def complete(self, request_id: str, code: str) -> str:
        """Check someone's access code. Returns where to send their browser: back
        to the chat app, with an authorization code it trades for tokens."""
        sign_in = self.pending(request_id)
        if sign_in is None:
            raise SignInError("This sign-in has expired. Start again from your chat app.", expired=True)
        grant = self.db.find_access_grant(code) if code.strip() else None
        if grant is None:
            sign_in.tries += 1
            if sign_in.tries >= MAX_TRIES:
                del self._sign_ins[request_id]
                raise SignInError("Too many wrong codes. Start again from your chat app.", expired=True)
            raise SignInError("That access code isn't right. Check it with your comp administrator.")
        del self._sign_ins[request_id]
        params = sign_in.params
        auth_code = secrets.token_urlsafe(32)
        self.db.save_oauth_token(
            auth_code, kind="code", grant_id=grant["id"], client_id=sign_in.client_id,
            details={
                "scopes": params.scopes or [],
                "code_challenge": params.code_challenge,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            },
            expires_at=int(time.time()) + CODE_SECONDS,
        )
        self.db.touch_access_grant(grant["id"])
        return construct_redirect_uri(str(params.redirect_uri), code=auth_code, state=params.state)

    def cancel(self, request_id: str) -> str | None:
        """Where to send the browser when someone declines, or None if the sign-in is gone."""
        sign_in = self._sign_ins.pop(request_id, None)
        if sign_in is None:
            return None
        return construct_redirect_uri(
            str(sign_in.params.redirect_uri), error="access_denied",
            error_description="Sign-in was cancelled.", state=sign_in.params.state,
        )

    def _forget_stale(self) -> None:
        cutoff = time.time() - SIGN_IN_SECONDS
        for request_id in [r for r, s in self._sign_ins.items() if s.started < cutoff]:
            del self._sign_ins[request_id]

    # ---------------- tokens ----------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str,
    ) -> AuthorizationCode | None:
        row = self.db.get_oauth_token(authorization_code, "code")
        if row is None or row["client_id"] != client.client_id:
            return None
        details = row["details"]
        return AuthorizationCode(
            code=authorization_code, scopes=details["scopes"], expires_at=row["expires_at"],
            client_id=row["client_id"], code_challenge=details["code_challenge"],
            redirect_uri=AnyUrl(details["redirect_uri"]),
            redirect_uri_provided_explicitly=details["redirect_uri_provided_explicitly"],
            resource=self.resource, subject=row["grant_id"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        # Single use: of two exchanges racing, only the one that deletes it wins.
        if not authorization_code.subject or not self.db.delete_oauth_token(authorization_code.code):
            raise TokenError("invalid_grant", "This authorization code has already been used.")
        return self._issue(authorization_code.subject, client.client_id, authorization_code.scopes)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str,
    ) -> RefreshToken | None:
        row = self.db.get_oauth_token(refresh_token, "refresh")
        if row is None or row["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token, client_id=row["client_id"], scopes=row["details"]["scopes"],
            expires_at=row["expires_at"], resource=self.resource, subject=row["grant_id"],
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str],
    ) -> OAuthToken:
        # Rotated on every use, so a stolen refresh token stops working once either party uses it.
        if not refresh_token.subject or not self.db.delete_oauth_token(refresh_token.token):
            raise TokenError("invalid_grant", "This refresh token has already been used.")
        return self._issue(refresh_token.subject, client.client_id, scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = self.db.get_oauth_token(token, "access")
        if row is not None:
            principal = Principal.of(row["grant_id"], row)
            client_id, scopes, expires_at = row["client_id"], row["details"]["scopes"], row["expires_at"]
        elif token.startswith(ACCESS_CODE_PREFIX) and (grant := self.db.find_access_grant(token)):
            # The access code itself, from a client that sends a header rather than signing in.
            principal = Principal.of(grant["id"], grant)
            client_id, scopes, expires_at = f"code:{grant['id']}", [], None
        else:
            return None
        self.db.touch_access_grant(principal.grant_id)
        return AccessToken(
            token=token, client_id=client_id, scopes=scopes, expires_at=expires_at,
            resource=self.resource, subject=principal.grant_id, claims=principal.claims(),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        # Signs that chat app out. An access code is never revoked this way:
        # only its administrator can (`icm access revoke`).
        if token.subject and not token.token.startswith(ACCESS_CODE_PREFIX):
            self.db.delete_oauth_tokens(token.subject, token.client_id)

    def _issue(self, grant_id: str, client_id: str, scopes: list[str]) -> OAuthToken:
        now = int(time.time())
        access = "oiat_" + secrets.token_urlsafe(32)
        refresh = "oirt_" + secrets.token_urlsafe(32)
        for token, kind, lifetime in ((access, "access", ACCESS_SECONDS), (refresh, "refresh", REFRESH_SECONDS)):
            self.db.save_oauth_token(token, kind=kind, grant_id=grant_id, client_id=client_id,
                                     details={"scopes": scopes}, expires_at=now + lifetime)
        return OAuthToken(access_token=access, expires_in=ACCESS_SECONDS, scope=" ".join(scopes) or None,
                          refresh_token=refresh)


# ------------------------------------------------------------------
# The sign-in page
# ------------------------------------------------------------------


async def sign_in_page(provider: AccessProvider, request: Request) -> Response:
    """GET shows the form for a pending sign-in; POST checks the code (or cancels)."""
    if request.method == "GET":
        request_id = request.query_params.get("request", "")
        sign_in = provider.pending(request_id)
        if sign_in is None:
            return _expired()
        return _form(provider, sign_in, request_id)

    form = await request.form()
    request_id = str(form.get("request") or "")
    if form.get("action") == "cancel":
        target = provider.cancel(request_id)
        return RedirectResponse(target, status_code=303) if target else _expired()
    try:
        target = provider.complete(request_id, str(form.get("code") or ""))
    except SignInError as e:
        sign_in = provider.pending(request_id)
        if e.expired or sign_in is None:
            return _expired(str(e))
        return _form(provider, sign_in, request_id, error=str(e), status=401)
    return RedirectResponse(target, status_code=303)


def _form(provider: AccessProvider, sign_in: _SignIn, request_id: str, *,
          error: str = "", status: int = 200) -> Response:
    app = html.escape(sign_in.client_name or "Your chat app")
    destination = html.escape(_destination(str(sign_in.params.redirect_uri)))
    problem = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    body = f"""<h1>Sign in to OpenIncent</h1>
<p><strong>{app}</strong> wants to answer questions about commission for you.</p>
<form method="post" action="{html.escape(provider.base_url)}/signin" autocomplete="off">
<input type="hidden" name="request" value="{html.escape(request_id)}">
<label for="code">Access code</label>
<input id="code" name="code" type="password" required autofocus spellcheck="false"
 autocapitalize="off" placeholder="{ACCESS_CODE_PREFIX}&hellip;">
{problem}
<div class="row">
<button class="primary" type="submit" name="action" value="sign-in">Sign in</button>
<button type="submit" name="action" value="cancel" formnovalidate>Cancel</button>
</div>
</form>
<p class="note">Use the access code your comp administrator gave you. You'll then go back to
<strong>{destination}</strong>. If you didn't just connect a chat app to OpenIncent, close this page.</p>"""
    return _page(body, status)


def _expired(message: str = "This sign-in has expired or was already used.") -> Response:
    body = f"""<h1>Start again from your chat app</h1>
<p>{html.escape(message)}</p>
<p class="note">Connect OpenIncent in your chat app once more to get a fresh sign-in page.</p>"""
    return _page(body, 410)


def _destination(redirect_uri: str) -> str:
    """Where the browser goes after signing in, as a person would recognise it."""
    parts = urlsplit(redirect_uri)
    host = parts.hostname or parts.scheme
    return "an app on this computer" if host in ("localhost", "127.0.0.1", "::1") else host


_LOGO = (
    '<svg width="28" height="28" viewBox="0 0 32 32" aria-hidden="true"><defs><linearGradient id="g" x1="0" '
    'y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3b82f6"/><stop offset="1" stop-color="#4338ca"/>'
    '</linearGradient></defs><rect width="32" height="32" rx="8" fill="url(#g)"/><circle cx="16" cy="16" '
    'r="7.5" fill="none" stroke="#fff" stroke-opacity=".3" stroke-width="3"/><circle cx="16" cy="16" r="7.5" '
    'fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-dasharray="35.3 47.1" '
    'transform="rotate(-90 16 16)"/></svg>'
)

# Same palette as the app (icm-ui/src/index.css), light and dark.
_PAGE = Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Sign in - OpenIncent</title>
<style>
:root { color-scheme: light dark; --canvas: #f6f7f9; --surface: #fff; --line: #e4e7ec; --ink: #101828;
  --ink-2: #475467; --ink-3: #8a94a6; --accent: #2563eb; --accent-hover: #1d4ed8; --danger-soft: #fef3f2;
  --danger-ink: #b42318; --shadow: 0 16px 40px -12px rgb(16 24 40 / .18); }
@media (prefers-color-scheme: dark) { :root { --canvas: #0b0d12; --surface: #111419; --line: #242a34;
  --ink: #e9ecf1; --ink-2: #a3acba; --ink-3: #6f7888; --accent: #3b6ef5; --accent-hover: #5582f7;
  --danger-soft: #2f1414; --danger-ink: #ff9c92; --shadow: 0 20px 48px -12px rgb(0 0 0 / .6); } }
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px 16px;
  background: var(--canvas); color: var(--ink);
  font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { width: 100%; max-width: 400px; background: var(--surface); border: 1px solid var(--line);
  border-radius: 16px; padding: 32px 28px; box-shadow: var(--shadow); }
.brand { display: flex; align-items: center; gap: 10px; font-weight: 650; letter-spacing: -.01em;
  margin-bottom: 28px; }
h1 { font-size: 20px; line-height: 1.3; margin: 0 0 8px; letter-spacing: -.015em; }
p { margin: 0 0 12px; color: var(--ink-2); }
strong { color: var(--ink); font-weight: 600; }
label { display: block; font-size: 13px; font-weight: 600; margin: 22px 0 6px; }
input[type=password] { width: 100%; padding: 10px 12px; color: var(--ink); background: var(--canvas);
  border: 1px solid var(--line); border-radius: 10px;
  font: 14px ui-monospace, "Cascadia Code", "SF Mono", Menlo, Consolas, monospace; }
input:focus-visible, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.row { display: flex; gap: 10px; margin-top: 20px; }
button { flex: 1; padding: 10px 14px; font: inherit; font-weight: 600; border-radius: 10px; cursor: pointer;
  border: 1px solid var(--line); background: transparent; color: var(--ink); }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button.primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); }
.error { margin: 12px 0 0; padding: 10px 12px; border-radius: 10px; font-size: 14px;
  background: var(--danger-soft); color: var(--danger-ink); }
.note { margin: 22px 0 0; font-size: 13px; color: var(--ink-3); }
.note strong { color: var(--ink-2); }
</style>
</head>
<body><main>
<div class="brand">$logo<span>OpenIncent</span></div>
$body
</main></body>
</html>
""")

_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _page(body: str, status: int) -> Response:
    return HTMLResponse(_PAGE.substitute(logo=_LOGO, body=body), status_code=status, headers=_HEADERS)

"""Headless OpenIncent: one scoped service that answers questions about
commissions and runs the pay cycle.

`CommissionsService` is the whole capability; the MCP server (`icm mcp`)
exposes it to assistants, and any other front end can call it directly.
Every call goes through a `Scope`: an administrator sees the organisation, a
payee sees only their own pay and can change nothing.
"""

from icm_engine.assistant.service import (
    AssistantError,
    CommissionsService,
    NotAllowed,
    NotFound,
    Refused,
    Scope,
)

__all__ = ["AssistantError", "CommissionsService", "NotAllowed", "NotFound", "Refused", "Scope"]

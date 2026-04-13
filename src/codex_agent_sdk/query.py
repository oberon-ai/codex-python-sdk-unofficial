"""Public one-shot query helper for the Codex SDK.

The real implementation lands later, but the root package can already point
users at the intended one-shot entry point.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import cast

from .approvals import ApprovalHandler
from .events import TurnEvent
from .options import AppServerConfig, CodexOptions


async def query(
    *,
    prompt: str | list[object],
    options: CodexOptions | None = None,
    app_server: AppServerConfig | None = None,
    output_schema: Mapping[str, object] | None = None,
    approval_handler: ApprovalHandler | None = None,
) -> AsyncIterator[TurnEvent]:
    """Stream one turn from a temporary app-server client via the one-shot query helper."""

    _ = (prompt, options, app_server, output_schema, approval_handler)
    if False:
        yield cast(TurnEvent, None)
    raise NotImplementedError("The one-shot query helper is not implemented yet.")


__all__ = [
    "query",
]

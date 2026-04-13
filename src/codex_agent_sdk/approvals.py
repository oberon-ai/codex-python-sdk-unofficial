"""Public approval request and decision helpers.

These placeholders reserve the stable approval-facing API without pushing
approval semantics down into transport or JSON-RPC plumbing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TypeAlias

ApprovalResponder: TypeAlias = Callable[["ApprovalDecision"], Awaitable[None]]
ApprovalHandler: TypeAlias = Callable[["ApprovalRequest"], Awaitable["ApprovalDecision"]]


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """Structured approval response sent back to the app-server."""

    decision: str
    reason: str | None = None


@dataclass(slots=True)
class ApprovalRequest:
    """Typed approval request surfaced from a pending turn."""

    request_id: str | int | None
    thread_id: str | None
    turn_id: str | None
    item_id: str | None = None
    kind: str | None = None
    payload: Mapping[str, object] | None = None
    _responder: ApprovalResponder | None = field(default=None, repr=False, compare=False)

    async def respond(self, decision: ApprovalDecision) -> None:
        """Send an approval decision back to the waiting turn."""

        if self._responder is None:
            raise NotImplementedError(
                "Approval request responses are not wired until the client layer exists."
            )
        await self._responder(decision)


__all__ = [
    "ApprovalDecision",
    "ApprovalHandler",
    "ApprovalRequest",
]

"""Typed public event surfaces for streamed Codex turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from .approvals import ApprovalDecision, ApprovalRequest

if TYPE_CHECKING:
    from .results import TurnResult


@dataclass(frozen=True, slots=True)
class TurnStartedEvent:
    """Signals that the app-server accepted and started a turn."""

    thread_id: str
    turn_id: str
    turn_status: str


@dataclass(frozen=True, slots=True)
class TurnCompletedEvent:
    """Signals that a turn reached a terminal status."""

    thread_id: str
    turn_id: str
    turn_status: str
    error: BaseException | None = None
    result: TurnResult | None = None


@dataclass(frozen=True, slots=True)
class AgentTextDeltaEvent:
    """Streams assistant-authored text for a turn item."""

    thread_id: str
    turn_id: str
    item_id: str
    text_delta: str


@dataclass(frozen=True, slots=True)
class ReasoningTextDeltaEvent:
    """Streams model reasoning text when the protocol exposes it."""

    thread_id: str
    turn_id: str
    item_id: str
    text_delta: str


@dataclass(frozen=True, slots=True)
class CommandOutputDeltaEvent:
    """Streams command output for a command item."""

    thread_id: str
    turn_id: str
    item_id: str
    output_delta: str


@dataclass(frozen=True, slots=True)
class ItemStartedEvent:
    """Signals that a new thread item entered the turn stream."""

    thread_id: str
    turn_id: str
    item: object


@dataclass(frozen=True, slots=True)
class ItemCompletedEvent:
    """Signals that a thread item reached a terminal status."""

    thread_id: str
    turn_id: str
    item: object


@dataclass(frozen=True, slots=True)
class ApprovalRequestedEvent:
    """Surfaces a pending approval request into the turn event stream."""

    thread_id: str | None
    turn_id: str | None
    item_id: str | None
    request: ApprovalRequest

    async def respond(self, decision: ApprovalDecision) -> None:
        """Forward an approval decision to the underlying request helper."""

        await self.request.respond(decision)


@dataclass(frozen=True, slots=True)
class ThreadStatusChangedEvent:
    """Reports a new high-level status for the active thread."""

    thread_id: str
    thread_status: str


@dataclass(frozen=True, slots=True)
class TokenUsageUpdatedEvent:
    """Carries incremental token-usage updates for the active turn."""

    thread_id: str
    turn_id: str
    token_usage: object


@dataclass(frozen=True, slots=True)
class RawNotificationEvent:
    """Preserves an untouched JSON-RPC notification envelope."""

    method: str
    params: object | None = None


@dataclass(frozen=True, slots=True)
class RawServerRequestEvent:
    """Preserves an untouched server-initiated JSON-RPC request envelope."""

    method: str
    request_id: str | int | None
    params: object | None = None


TurnEvent: TypeAlias = (
    TurnStartedEvent
    | TurnCompletedEvent
    | AgentTextDeltaEvent
    | ReasoningTextDeltaEvent
    | CommandOutputDeltaEvent
    | ItemStartedEvent
    | ItemCompletedEvent
    | ApprovalRequestedEvent
    | ThreadStatusChangedEvent
    | TokenUsageUpdatedEvent
    | RawNotificationEvent
    | RawServerRequestEvent
)


__all__ = [
    "AgentTextDeltaEvent",
    "ApprovalRequestedEvent",
    "CommandOutputDeltaEvent",
    "ItemCompletedEvent",
    "ItemStartedEvent",
    "RawNotificationEvent",
    "RawServerRequestEvent",
    "ReasoningTextDeltaEvent",
    "ThreadStatusChangedEvent",
    "TokenUsageUpdatedEvent",
    "TurnCompletedEvent",
    "TurnEvent",
    "TurnStartedEvent",
]

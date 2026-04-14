"""Handwritten protocol-to-event adapters built on top of generated models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..errors import CodexError
from ..events import (
    AgentTextDeltaEvent,
    CommandOutputDeltaEvent,
    ItemCompletedEvent,
    ItemStartedEvent,
    RawNotificationEvent,
    ReasoningTextDeltaEvent,
    ThreadStatusChangedEvent,
    TokenUsageUpdatedEvent,
    TurnCompletedEvent,
    TurnEvent,
    TurnStartedEvent,
)
from ..generated.stable import (
    AgentMessageDeltaNotification,
    CommandExecutionOutputDeltaNotification,
    ItemCompletedNotification,
    ItemStartedNotification,
    ReasoningSummaryTextDeltaNotification,
    ReasoningTextDeltaNotification,
    ThreadStatus,
    ThreadStatusChangedNotification,
    ThreadTokenUsage,
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
    TurnError,
    TurnStartedNotification,
    TurnStatus,
)
from ..results import TurnResult
from ..rpc.jsonrpc import JsonRpcEnvelopeLike
from .registries import RawServerNotification, TypedServerNotification, parse_server_notification


@dataclass(slots=True)
class TurnEventAdapterState:
    """Mutable per-turn adapter state carried across streamed notifications."""

    assistant_text_fragments: list[str] = field(default_factory=list)
    latest_token_usage: ThreadTokenUsage | None = None


def adapt_turn_notification(
    notification: JsonRpcEnvelopeLike,
    *,
    target_turn_id: str,
    state: TurnEventAdapterState,
) -> TurnEvent | None:
    """Adapt one server notification into a public ``TurnEvent`` when it matches one turn."""

    parsed = parse_server_notification(notification)

    if isinstance(parsed, RawServerNotification):
        raw_turn_id = _extract_turn_id_from_object(parsed.params)
        if raw_turn_id != target_turn_id:
            return None
        return RawNotificationEvent(method=parsed.method, params=parsed.params)

    params = parsed.params

    if isinstance(params, TurnStartedNotification):
        if params.turn.id != target_turn_id:
            return None
        return TurnStartedEvent(
            thread_id=params.thread_id,
            turn_id=params.turn.id,
            turn_status=_normalize_turn_status(params.turn.status),
        )

    if isinstance(params, TurnCompletedNotification):
        if params.turn.id != target_turn_id:
            return None
        turn_result = build_turn_result(params, state=state)
        return TurnCompletedEvent(
            thread_id=params.thread_id,
            turn_id=params.turn.id,
            turn_status=turn_result.status,
            error=turn_result.error,
            result=turn_result,
        )

    if isinstance(params, ThreadStatusChangedNotification):
        return ThreadStatusChangedEvent(
            thread_id=params.thread_id,
            thread_status=_normalize_thread_status(params.status),
        )

    if isinstance(params, ThreadTokenUsageUpdatedNotification):
        if params.turn_id != target_turn_id:
            return None
        state.latest_token_usage = params.token_usage
        return TokenUsageUpdatedEvent(
            thread_id=params.thread_id,
            turn_id=params.turn_id,
            token_usage=params.token_usage,
        )

    if isinstance(params, AgentMessageDeltaNotification):
        if params.turn_id != target_turn_id:
            return None
        state.assistant_text_fragments.append(params.delta)
        return AgentTextDeltaEvent(
            thread_id=params.thread_id,
            turn_id=params.turn_id,
            item_id=params.item_id,
            text_delta=params.delta,
        )

    if isinstance(params, (ReasoningTextDeltaNotification, ReasoningSummaryTextDeltaNotification)):
        if params.turn_id != target_turn_id:
            return None
        return ReasoningTextDeltaEvent(
            thread_id=params.thread_id,
            turn_id=params.turn_id,
            item_id=params.item_id,
            text_delta=params.delta,
        )

    if isinstance(params, CommandExecutionOutputDeltaNotification):
        if params.turn_id != target_turn_id:
            return None
        return CommandOutputDeltaEvent(
            thread_id=params.thread_id,
            turn_id=params.turn_id,
            item_id=params.item_id,
            output_delta=params.delta,
        )

    if isinstance(params, ItemStartedNotification):
        if params.turn_id != target_turn_id:
            return None
        return ItemStartedEvent(
            thread_id=params.thread_id,
            turn_id=params.turn_id,
            item=params.item,
        )

    if isinstance(params, ItemCompletedNotification):
        if params.turn_id != target_turn_id:
            return None
        return ItemCompletedEvent(
            thread_id=params.thread_id,
            turn_id=params.turn_id,
            item=params.item,
        )

    if _extract_turn_id_from_typed_notification(parsed) != target_turn_id:
        return None
    return RawNotificationEvent(
        method=parsed.method,
        params=parsed.envelope.params if parsed.envelope.has_params else None,
    )


def build_turn_result(
    completion: TurnCompletedNotification,
    *,
    state: TurnEventAdapterState,
) -> TurnResult:
    """Build a compact terminal turn summary from the completion payload and adapter state."""

    assistant_text = _extract_assistant_text(
        turn_items=completion.turn.items,
        streamed_fragments=state.assistant_text_fragments,
    )
    error = _turn_error_to_exception(completion.turn.error)
    return TurnResult(
        thread_id=completion.thread_id,
        turn_id=completion.turn.id,
        status=_normalize_turn_status(completion.turn.status),
        items=tuple(completion.turn.items),
        token_usage=state.latest_token_usage,
        error=error,
        assistant_text=assistant_text,
    )


def _extract_turn_id_from_typed_notification(notification: TypedServerNotification) -> str | None:
    params = notification.params
    direct_turn_id = getattr(params, "turn_id", None)
    if isinstance(direct_turn_id, str):
        return direct_turn_id

    nested_turn = getattr(params, "turn", None)
    nested_turn_id = getattr(nested_turn, "id", None)
    if isinstance(nested_turn_id, str):
        return nested_turn_id
    return None


def _extract_turn_id_from_object(payload: object) -> str | None:
    if not isinstance(payload, Mapping):
        return None

    direct_turn_id = payload.get("turnId")
    if isinstance(direct_turn_id, str):
        return direct_turn_id

    nested_turn = payload.get("turn")
    if isinstance(nested_turn, Mapping):
        nested_turn_id = nested_turn.get("id")
        if isinstance(nested_turn_id, str):
            return nested_turn_id

    return None


def _extract_assistant_text(
    *,
    turn_items: Sequence[object],
    streamed_fragments: list[str],
) -> str | None:
    if streamed_fragments:
        return "".join(streamed_fragments)

    item_texts: list[str] = []
    for item in turn_items:
        dumped_item = _model_dump_if_available(item)
        if dumped_item is None:
            continue
        item_type = dumped_item.get("type")
        item_text = dumped_item.get("text")
        if item_type == "agentMessage" and isinstance(item_text, str):
            item_texts.append(item_text)

    if not item_texts:
        return None
    return "\n\n".join(item_texts)


def _model_dump_if_available(item: object) -> dict[str, Any] | None:
    model_dump = getattr(item, "model_dump", None)
    if not callable(model_dump):
        return None
    dumped = model_dump()
    if isinstance(dumped, dict):
        return dumped
    return None


def _normalize_thread_status(status: ThreadStatus) -> str:
    raw_type = getattr(status.root, "type", None)
    if raw_type == "notLoaded":
        return "not_loaded"
    if raw_type == "systemError":
        return "system_error"
    if isinstance(raw_type, str):
        return raw_type
    raise TypeError("unsupported ThreadStatus payload")


def _normalize_turn_status(status: TurnStatus) -> str:
    if status is TurnStatus.in_progress:
        return "in_progress"
    return status.value


def _turn_error_to_exception(turn_error: TurnError | None) -> CodexError | None:
    if turn_error is None:
        return None
    return CodexError(turn_error.message)


__all__ = [
    "TurnEventAdapterState",
    "adapt_turn_notification",
    "build_turn_result",
]

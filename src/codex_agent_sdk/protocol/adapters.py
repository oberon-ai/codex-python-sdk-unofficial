"""Handwritten protocol-to-event adapters built on top of generated models."""

from __future__ import annotations

from collections.abc import Mapping

from .._turn_aggregation import (
    TurnOutputState as TurnEventAdapterState,
)
from .._turn_aggregation import (
    build_turn_result,
    observe_turn_event,
)
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
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
    TurnStartedNotification,
    TurnStatus,
)
from ..rpc.jsonrpc import JsonRpcEnvelopeLike
from .registries import RawServerNotification, TypedServerNotification, parse_server_notification


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
        turn_started_event = TurnStartedEvent(
            thread_id=params.thread_id,
            turn_id=params.turn.id,
            turn_status=_normalize_turn_status(params.turn.status),
        )
        observe_turn_event(turn_started_event, state=state)
        return turn_started_event

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
        token_usage_event = TokenUsageUpdatedEvent(
            thread_id=params.thread_id,
            turn_id=params.turn_id,
            token_usage=params.token_usage,
        )
        observe_turn_event(token_usage_event, state=state)
        return token_usage_event

    if isinstance(params, AgentMessageDeltaNotification):
        if params.turn_id != target_turn_id:
            return None
        agent_text_delta_event = AgentTextDeltaEvent(
            thread_id=params.thread_id,
            turn_id=params.turn_id,
            item_id=params.item_id,
            text_delta=params.delta,
        )
        observe_turn_event(agent_text_delta_event, state=state)
        return agent_text_delta_event

    if isinstance(params, (ReasoningTextDeltaNotification, ReasoningSummaryTextDeltaNotification)):
        if params.turn_id != target_turn_id:
            return None
        reasoning_text_delta_event = ReasoningTextDeltaEvent(
            thread_id=params.thread_id,
            turn_id=params.turn_id,
            item_id=params.item_id,
            text_delta=params.delta,
        )
        observe_turn_event(reasoning_text_delta_event, state=state)
        return reasoning_text_delta_event

    if isinstance(params, CommandExecutionOutputDeltaNotification):
        if params.turn_id != target_turn_id:
            return None
        command_output_delta_event = CommandOutputDeltaEvent(
            thread_id=params.thread_id,
            turn_id=params.turn_id,
            item_id=params.item_id,
            output_delta=params.delta,
        )
        observe_turn_event(command_output_delta_event, state=state)
        return command_output_delta_event

    if isinstance(params, ItemStartedNotification):
        if params.turn_id != target_turn_id:
            return None
        item_started_event = ItemStartedEvent(
            thread_id=params.thread_id,
            turn_id=params.turn_id,
            item=params.item,
        )
        observe_turn_event(item_started_event, state=state)
        return item_started_event

    if isinstance(params, ItemCompletedNotification):
        if params.turn_id != target_turn_id:
            return None
        item_completed_event = ItemCompletedEvent(
            thread_id=params.thread_id,
            turn_id=params.turn_id,
            item=params.item,
        )
        observe_turn_event(item_completed_event, state=state)
        return item_completed_event

    if _extract_turn_id_from_typed_notification(parsed) != target_turn_id:
        return None
    raw_notification_event = RawNotificationEvent(
        method=parsed.method,
        params=parsed.envelope.params if parsed.envelope.has_params else None,
    )
    observe_turn_event(raw_notification_event, state=state)
    return raw_notification_event


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


__all__ = [
    "TurnEventAdapterState",
    "adapt_turn_notification",
    "build_turn_result",
]

"""Shared turn-output aggregation helpers.

This module preserves the typed turn event stream as the source of truth while
layering assembled per-item and per-turn convenience state on top of it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .errors import CodexError
from .events import (
    AgentTextDeltaEvent,
    CommandOutputDeltaEvent,
    ItemCompletedEvent,
    ItemStartedEvent,
    RawNotificationEvent,
    ReasoningTextDeltaEvent,
    TokenUsageUpdatedEvent,
    TurnCompletedEvent,
    TurnEvent,
)
from .generated.stable import ThreadTokenUsage, TurnCompletedNotification, TurnError

_AGENT_MESSAGE_ITEM_TYPE = "agentMessage"
_COMMAND_EXECUTION_ITEM_TYPE = "commandExecution"
_FILE_CHANGE_ITEM_TYPE = "fileChange"
_PLAN_ITEM_TYPE = "plan"
_REASONING_ITEM_TYPE = "reasoning"

_RAW_ITEM_DELTA_HANDLERS: dict[str, tuple[str, str]] = {
    "item/agentMessage/delta": ("agent_text_fragments_by_item_id", _AGENT_MESSAGE_ITEM_TYPE),
    "item/commandExecution/outputDelta": (
        "command_output_fragments_by_item_id",
        _COMMAND_EXECUTION_ITEM_TYPE,
    ),
    "item/fileChange/outputDelta": (
        "file_change_output_fragments_by_item_id",
        _FILE_CHANGE_ITEM_TYPE,
    ),
    "item/plan/delta": ("plan_text_fragments_by_item_id", _PLAN_ITEM_TYPE),
    "item/reasoning/summaryTextDelta": (
        "reasoning_text_fragments_by_item_id",
        _REASONING_ITEM_TYPE,
    ),
    "item/reasoning/textDelta": ("reasoning_text_fragments_by_item_id", _REASONING_ITEM_TYPE),
}


@dataclass(frozen=True, slots=True)
class TurnItemAggregation:
    """Assembled convenience view for one turn item."""

    item_id: str
    item_type: str | None
    item: object | None = None
    agent_text: str | None = None
    reasoning_text: str | None = None
    command_output: str | None = None
    file_change_output: str | None = None
    plan_text: str | None = None


@dataclass(frozen=True, slots=True)
class TurnResult:
    """Compact terminal summary returned for a finished turn."""

    thread_id: str
    turn_id: str
    status: str
    items: tuple[object, ...] = ()
    token_usage: object | None = None
    error: BaseException | None = None
    assistant_text: str | None = None
    structured_output: object | None = None
    item_aggregations: tuple[TurnItemAggregation, ...] = ()

    @property
    def command_output(self) -> str | None:
        """Return the assembled command output across command-execution items."""

        return _join_item_values(item.command_output for item in self.item_aggregations)

    @property
    def file_change_output(self) -> str | None:
        """Return the assembled file-change output across file-change items."""

        return _join_item_values(item.file_change_output for item in self.item_aggregations)

    @property
    def plan_text(self) -> str | None:
        """Return the assembled plan text across plan items."""

        return _join_item_values(item.plan_text for item in self.item_aggregations)

    @property
    def reasoning_text(self) -> str | None:
        """Return the assembled reasoning text across reasoning items."""

        return _join_item_values(item.reasoning_text for item in self.item_aggregations)


@dataclass(slots=True)
class TurnOutputState:
    """Mutable per-turn aggregation state carried across streamed events."""

    agent_text_fragments_by_item_id: dict[str, list[str]] = field(default_factory=dict)
    command_output_fragments_by_item_id: dict[str, list[str]] = field(default_factory=dict)
    file_change_output_fragments_by_item_id: dict[str, list[str]] = field(default_factory=dict)
    item_ids_in_order: list[str] = field(default_factory=list)
    item_types_by_id: dict[str, str] = field(default_factory=dict)
    items_by_id: dict[str, object] = field(default_factory=dict)
    latest_token_usage: ThreadTokenUsage | None = None
    plan_text_fragments_by_item_id: dict[str, list[str]] = field(default_factory=dict)
    reasoning_text_fragments_by_item_id: dict[str, list[str]] = field(default_factory=dict)


class TurnOutputAggregator:
    """Observe streamed turn events while keeping assembled convenience state."""

    def __init__(self) -> None:
        self._result: TurnResult | None = None
        self._state = TurnOutputState()

    @property
    def assistant_text(self) -> str | None:
        """Return the best-effort assembled assistant text observed so far."""

        if self._result is not None:
            return self._result.assistant_text
        return _join_item_values(
            item.agent_text for item in _build_item_aggregations(state=self._state)
        )

    @property
    def command_output(self) -> str | None:
        """Return the best-effort assembled command output observed so far."""

        if self._result is not None:
            return self._result.command_output
        return _join_item_values(
            item.command_output for item in _build_item_aggregations(state=self._state)
        )

    @property
    def file_change_output(self) -> str | None:
        """Return the best-effort assembled file-change output observed so far."""

        if self._result is not None:
            return self._result.file_change_output
        return _join_item_values(
            item.file_change_output for item in _build_item_aggregations(state=self._state)
        )

    @property
    def item_aggregations(self) -> tuple[TurnItemAggregation, ...]:
        """Return the best-effort assembled item views observed so far."""

        if self._result is not None:
            return self._result.item_aggregations
        return _build_item_aggregations(state=self._state)

    @property
    def items(self) -> tuple[object, ...]:
        """Return the observed typed item objects in stable item order."""

        if self._result is not None:
            return self._result.items
        return _resolve_streamed_items(self._state)

    @property
    def plan_text(self) -> str | None:
        """Return the best-effort assembled plan text observed so far."""

        if self._result is not None:
            return self._result.plan_text
        return _join_item_values(
            item.plan_text for item in _build_item_aggregations(state=self._state)
        )

    @property
    def reasoning_text(self) -> str | None:
        """Return the best-effort assembled reasoning text observed so far."""

        if self._result is not None:
            return self._result.reasoning_text
        return _join_item_values(
            item.reasoning_text for item in _build_item_aggregations(state=self._state)
        )

    @property
    def result(self) -> TurnResult | None:
        """Return the final aggregated result after a completion event, if any."""

        return self._result

    def observe(self, event: TurnEvent) -> None:
        """Update the aggregator with one streamed turn event."""

        observe_turn_event(event, state=self._state)
        if isinstance(event, TurnCompletedEvent):
            self._result = event.result or _build_snapshot_turn_result(
                thread_id=event.thread_id,
                turn_id=event.turn_id,
                status=event.turn_status,
                state=self._state,
                error=event.error,
            )


def build_turn_result(
    completion: TurnCompletedNotification,
    *,
    state: TurnOutputState,
) -> TurnResult:
    """Build a compact terminal turn summary from completion plus streamed state."""

    _record_items(state, completion.turn.items)
    item_aggregations = _build_item_aggregations(
        state=state,
        authoritative_items=completion.turn.items,
    )
    return TurnResult(
        thread_id=completion.thread_id,
        turn_id=completion.turn.id,
        status=_normalize_turn_status(completion.turn.status),
        items=_resolve_turn_items(
            state=state,
            authoritative_items=completion.turn.items,
        ),
        token_usage=state.latest_token_usage,
        error=_turn_error_to_exception(completion.turn.error),
        assistant_text=_join_item_values(item.agent_text for item in item_aggregations),
        item_aggregations=item_aggregations,
    )


def observe_turn_event(event: TurnEvent, *, state: TurnOutputState) -> None:
    """Update mutable turn-output state from one public turn event."""

    if isinstance(event, AgentTextDeltaEvent):
        _append_item_fragment(
            state.agent_text_fragments_by_item_id,
            event.item_id,
            event.text_delta,
            item_type=_AGENT_MESSAGE_ITEM_TYPE,
            state=state,
        )
        return

    if isinstance(event, CommandOutputDeltaEvent):
        _append_item_fragment(
            state.command_output_fragments_by_item_id,
            event.item_id,
            event.output_delta,
            item_type=_COMMAND_EXECUTION_ITEM_TYPE,
            state=state,
        )
        return

    if isinstance(event, ReasoningTextDeltaEvent):
        _append_item_fragment(
            state.reasoning_text_fragments_by_item_id,
            event.item_id,
            event.text_delta,
            item_type=_REASONING_ITEM_TYPE,
            state=state,
        )
        return

    if isinstance(event, (ItemStartedEvent, ItemCompletedEvent)):
        _record_item(state, event.item)
        return

    if isinstance(event, TokenUsageUpdatedEvent) and isinstance(
        event.token_usage, ThreadTokenUsage
    ):
        state.latest_token_usage = event.token_usage
        return

    if isinstance(event, RawNotificationEvent):
        _observe_raw_notification_event(event, state=state)


def _append_item_fragment(
    fragments_by_item_id: dict[str, list[str]],
    item_id: str,
    fragment: str,
    *,
    item_type: str,
    state: TurnOutputState,
) -> None:
    _ensure_item_slot(state, item_id, item_type=item_type)
    fragments_by_item_id.setdefault(item_id, []).append(fragment)


def _build_item_aggregations(
    *,
    state: TurnOutputState,
    authoritative_items: Sequence[object] = (),
) -> tuple[TurnItemAggregation, ...]:
    ordered_item_ids = _resolve_item_ids(
        state=state,
        authoritative_items=authoritative_items,
    )
    resolved_items_by_id = dict(state.items_by_id)

    for item in authoritative_items:
        item_id = _extract_item_id(item)
        if item_id is not None:
            resolved_items_by_id[item_id] = item

    item_aggregations: list[TurnItemAggregation] = []
    for item_id in ordered_item_ids:
        item = resolved_items_by_id.get(item_id)
        item_data = _dump_item(item)
        item_type = state.item_types_by_id.get(item_id)
        if item_data is not None:
            item_type = _extract_item_type(item_data) or item_type

        agent_text = _assemble_item_text(
            fragments=state.agent_text_fragments_by_item_id.get(item_id),
            fallback_text=_extract_fallback_item_text(
                item_data,
                item_type=item_type,
                expected_item_type=_AGENT_MESSAGE_ITEM_TYPE,
            ),
        )
        command_output = _assemble_item_text(
            fragments=state.command_output_fragments_by_item_id.get(item_id),
            fallback_text=_extract_fallback_output(
                item_data,
                item_type=item_type,
                expected_item_type=_COMMAND_EXECUTION_ITEM_TYPE,
            ),
        )
        file_change_output = _assemble_item_text(
            fragments=state.file_change_output_fragments_by_item_id.get(item_id),
            fallback_text=_extract_fallback_output(
                item_data,
                item_type=item_type,
                expected_item_type=_FILE_CHANGE_ITEM_TYPE,
            ),
        )
        plan_text = _assemble_item_text(
            fragments=state.plan_text_fragments_by_item_id.get(item_id),
            fallback_text=_extract_fallback_item_text(
                item_data,
                item_type=item_type,
                expected_item_type=_PLAN_ITEM_TYPE,
            ),
        )
        reasoning_text = _assemble_item_text(
            fragments=state.reasoning_text_fragments_by_item_id.get(item_id),
            fallback_text=_extract_fallback_item_text(
                item_data,
                item_type=item_type,
                expected_item_type=_REASONING_ITEM_TYPE,
            ),
        )

        item_aggregations.append(
            TurnItemAggregation(
                item_id=item_id,
                item_type=item_type,
                item=item,
                agent_text=agent_text,
                reasoning_text=reasoning_text,
                command_output=command_output,
                file_change_output=file_change_output,
                plan_text=plan_text,
            )
        )

    return tuple(item_aggregations)


def _build_snapshot_turn_result(
    *,
    thread_id: str,
    turn_id: str,
    status: str,
    state: TurnOutputState,
    error: BaseException | None,
) -> TurnResult:
    item_aggregations = _build_item_aggregations(state=state)
    return TurnResult(
        thread_id=thread_id,
        turn_id=turn_id,
        status=status,
        items=_resolve_streamed_items(state),
        token_usage=state.latest_token_usage,
        error=error,
        assistant_text=_join_item_values(item.agent_text for item in item_aggregations),
        item_aggregations=item_aggregations,
    )


def _dump_item(item: object | None) -> dict[str, Any] | None:
    if item is None:
        return None

    root = getattr(item, "root", None)
    if root is not None and root is not item:
        item = root

    if isinstance(item, Mapping):
        return dict(item.items())

    model_dump = getattr(item, "model_dump", None)
    if not callable(model_dump):
        return None

    dumped = model_dump(
        by_alias=False,
        exclude_unset=False,
        warnings=False,
    )
    if isinstance(dumped, dict):
        return dumped
    return None


def _ensure_item_slot(
    state: TurnOutputState,
    item_id: str,
    *,
    item_type: str | None = None,
) -> None:
    if item_id not in state.item_ids_in_order:
        state.item_ids_in_order.append(item_id)
    if item_type is not None:
        state.item_types_by_id.setdefault(item_id, item_type)


def _extract_fallback_item_text(
    item_data: Mapping[str, Any] | None,
    *,
    item_type: str | None,
    expected_item_type: str,
) -> str | None:
    if item_data is None or item_type != expected_item_type:
        return None
    return _extract_string(item_data, "text")


def _extract_fallback_output(
    item_data: Mapping[str, Any] | None,
    *,
    item_type: str | None,
    expected_item_type: str,
) -> str | None:
    if item_data is None or item_type != expected_item_type:
        return None
    return _extract_string(item_data, "aggregated_output", "aggregatedOutput")


def _extract_item_id(item: object) -> str | None:
    item_data = _dump_item(item)
    if item_data is None:
        return None
    return _extract_string(item_data, "id")


def _extract_item_type(item_data: Mapping[str, Any]) -> str | None:
    return _extract_string(item_data, "type")


def _extract_raw_item_delta_fields(
    params: object,
) -> tuple[str, str] | tuple[None, None]:
    if not isinstance(params, Mapping):
        return None, None
    item_id = params.get("itemId")
    delta = params.get("delta")
    if not isinstance(item_id, str) or not isinstance(delta, str):
        return None, None
    return item_id, delta


def _extract_string(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def _join_item_values(values: Sequence[str | None] | Any) -> str | None:
    collected_values = [value for value in values if value is not None]
    if not collected_values:
        return None
    return "\n\n".join(collected_values)


def _normalize_turn_status(status: object) -> str:
    status_value = getattr(status, "value", None)
    if status_value == "inProgress":
        return "in_progress"
    if isinstance(status_value, str):
        return status_value
    if status == "inProgress":
        return "in_progress"
    if isinstance(status, str):
        return status
    raise TypeError("unsupported turn status payload")


def _observe_raw_notification_event(
    event: RawNotificationEvent,
    *,
    state: TurnOutputState,
) -> None:
    handler = _RAW_ITEM_DELTA_HANDLERS.get(event.method)
    if handler is None:
        return

    fragments_attr, item_type = handler
    item_id, delta = _extract_raw_item_delta_fields(event.params)
    if item_id is None or delta is None:
        return

    fragments_by_item_id = getattr(state, fragments_attr)
    _append_item_fragment(
        fragments_by_item_id,
        item_id,
        delta,
        item_type=item_type,
        state=state,
    )


def _record_item(state: TurnOutputState, item: object) -> None:
    item_data = _dump_item(item)
    if item_data is None:
        return

    item_id = _extract_item_id(item)
    if item_id is None:
        return

    _ensure_item_slot(
        state,
        item_id,
        item_type=_extract_item_type(item_data),
    )
    state.items_by_id[item_id] = item


def _record_items(state: TurnOutputState, items: Sequence[object]) -> None:
    for item in items:
        _record_item(state, item)


def _resolve_item_ids(
    *,
    state: TurnOutputState,
    authoritative_items: Sequence[object],
) -> tuple[str, ...]:
    ordered_ids: list[str] = []
    seen_item_ids: set[str] = set()

    for item in authoritative_items:
        item_id = _extract_item_id(item)
        if item_id is None or item_id in seen_item_ids:
            continue
        ordered_ids.append(item_id)
        seen_item_ids.add(item_id)

    for item_id in state.item_ids_in_order:
        if item_id in seen_item_ids:
            continue
        ordered_ids.append(item_id)
        seen_item_ids.add(item_id)

    return tuple(ordered_ids)


def _resolve_streamed_items(state: TurnOutputState) -> tuple[object, ...]:
    streamed_items: list[object] = []
    for item_id in state.item_ids_in_order:
        item = state.items_by_id.get(item_id)
        if item is not None:
            streamed_items.append(item)
    return tuple(streamed_items)


def _resolve_turn_items(
    *,
    state: TurnOutputState,
    authoritative_items: Sequence[object],
) -> tuple[object, ...]:
    if authoritative_items:
        return tuple(authoritative_items)
    return _resolve_streamed_items(state)


def _assemble_item_text(
    *,
    fragments: list[str] | None,
    fallback_text: str | None,
) -> str | None:
    if fragments is None:
        return fallback_text

    text = "".join(fragments)
    if text or fallback_text is None:
        return text
    return fallback_text


def _turn_error_to_exception(turn_error: TurnError | None) -> CodexError | None:
    if turn_error is None:
        return None
    return CodexError(turn_error.message)


__all__ = [
    "TurnItemAggregation",
    "TurnOutputAggregator",
    "TurnResult",
]

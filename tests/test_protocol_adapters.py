from __future__ import annotations

from codex_agent_sdk.events import (
    RawNotificationEvent,
    ThreadStatusChangedEvent,
    TurnCompletedEvent,
)
from codex_agent_sdk.generated.stable import ThreadTokenUsage, TurnCompletedNotification
from codex_agent_sdk.protocol.adapters import (
    TurnEventAdapterState,
    adapt_turn_notification,
    build_turn_result,
)


def test_adapt_turn_notification_filters_other_turns_and_surfaces_thread_status() -> None:
    state = TurnEventAdapterState()

    other_turn_event = adapt_turn_notification(
        {
            "method": "item/agentMessage/delta",
            "params": {
                "delta": "ignore me",
                "itemId": "item_other",
                "threadId": "thread_123",
                "turnId": "turn_other",
            },
        },
        target_turn_id="turn_target",
        state=state,
    )
    thread_status_event = adapt_turn_notification(
        {
            "method": "thread/status/changed",
            "params": {
                "threadId": "thread_123",
                "status": {
                    "type": "systemError",
                },
            },
        },
        target_turn_id="turn_target",
        state=state,
    )

    assert other_turn_event is None
    assert isinstance(thread_status_event, ThreadStatusChangedEvent)
    assert thread_status_event.thread_id == "thread_123"
    assert thread_status_event.thread_status == "system_error"


def test_adapt_turn_notification_uses_raw_fallback_for_unadapted_target_turn_methods() -> None:
    state = TurnEventAdapterState()

    event = adapt_turn_notification(
        {
            "method": "turn/plan/updated",
            "params": {
                "threadId": "thread_123",
                "turnId": "turn_123",
                "plan": [{"step": "Reproduce failure", "status": "completed"}],
            },
        },
        target_turn_id="turn_123",
        state=state,
    )

    assert isinstance(event, RawNotificationEvent)
    assert event.method == "turn/plan/updated"
    assert event.params == {
        "threadId": "thread_123",
        "turnId": "turn_123",
        "plan": [{"step": "Reproduce failure", "status": "completed"}],
    }


def test_build_turn_result_and_completion_event_preserve_terminal_state() -> None:
    state = TurnEventAdapterState(
        agent_text_fragments_by_item_id={"item_agent": ["Hello", " world"]},
        item_ids_in_order=["item_agent"],
        item_types_by_id={"item_agent": "agentMessage"},
        latest_token_usage=ThreadTokenUsage.model_validate(
            {
                "last": {
                    "cachedInputTokens": 0,
                    "inputTokens": 10,
                    "outputTokens": 4,
                    "reasoningOutputTokens": 1,
                    "totalTokens": 15,
                },
                "total": {
                    "cachedInputTokens": 2,
                    "inputTokens": 20,
                    "outputTokens": 8,
                    "reasoningOutputTokens": 3,
                    "totalTokens": 33,
                },
            }
        ),
    )

    completion = TurnCompletedNotification.model_validate(
        {
            "threadId": "thread_123",
            "turn": {
                "id": "turn_123",
                "items": [],
                "status": "failed",
                "error": {"message": "tool call failed"},
            },
        }
    )
    turn_result = build_turn_result(
        completion,
        state=state,
    )
    completion_event = adapt_turn_notification(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread_123",
                "turn": {
                    "id": "turn_123",
                    "items": [],
                    "status": "failed",
                    "error": {"message": "tool call failed"},
                },
            },
        },
        target_turn_id="turn_123",
        state=state,
    )

    assert turn_result.thread_id == "thread_123"
    assert turn_result.turn_id == "turn_123"
    assert turn_result.status == "failed"
    assert turn_result.assistant_text == "Hello world"
    assert turn_result.token_usage == state.latest_token_usage
    assert turn_result.error is not None
    assert str(turn_result.error) == "tool call failed"
    assert len(turn_result.item_aggregations) == 1
    assert turn_result.item_aggregations[0].item_id == "item_agent"
    assert turn_result.item_aggregations[0].agent_text == "Hello world"

    assert isinstance(completion_event, TurnCompletedEvent)
    assert completion_event.turn_status == "failed"
    assert completion_event.result is not None
    assert completion_event.result.thread_id == turn_result.thread_id
    assert completion_event.result.turn_id == turn_result.turn_id
    assert completion_event.result.status == turn_result.status
    assert completion_event.result.assistant_text == turn_result.assistant_text
    assert completion_event.result.token_usage == turn_result.token_usage
    assert completion_event.error is not None
    assert str(completion_event.error) == "tool call failed"


def test_build_turn_result_falls_back_to_final_turn_items_when_no_streamed_text_exists() -> None:
    completion = TurnCompletedNotification.model_validate(
        {
            "threadId": "thread_123",
            "turn": {
                "id": "turn_123",
                "status": "completed",
                "items": [
                    {
                        "id": "item_agent_1",
                        "text": "First answer.",
                        "type": "agentMessage",
                    },
                    {
                        "aggregatedOutput": "FAILED tests/test_example.py::test_case\n",
                        "command": "pytest -q",
                        "commandActions": [],
                        "cwd": "/repo",
                        "id": "item_command_1",
                        "status": "completed",
                        "type": "commandExecution",
                    },
                    {
                        "id": "item_agent_2",
                        "text": "Second answer.",
                        "type": "agentMessage",
                    },
                ],
            },
        }
    )

    result = build_turn_result(completion, state=TurnEventAdapterState())

    assert result.status == "completed"
    assert result.assistant_text == "First answer.\n\nSecond answer."
    assert result.command_output == "FAILED tests/test_example.py::test_case\n"
    assert [item.item_id for item in result.item_aggregations] == [
        "item_agent_1",
        "item_command_1",
        "item_agent_2",
    ]
    assert result.item_aggregations[0].agent_text == "First answer."
    assert result.item_aggregations[1].command_output == (
        "FAILED tests/test_example.py::test_case\n"
    )
    assert result.item_aggregations[2].agent_text == "Second answer."

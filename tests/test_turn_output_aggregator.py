from __future__ import annotations

from codex_agent_sdk import (
    AgentTextDeltaEvent,
    CommandOutputDeltaEvent,
    ItemStartedEvent,
    RawNotificationEvent,
    TurnCompletedEvent,
    TurnOutputAggregator,
)


def test_turn_output_aggregator_preserves_multi_item_boundaries_without_completion() -> None:
    aggregator = TurnOutputAggregator()

    agent_item_one = {"id": "item_agent_1", "text": "", "type": "agentMessage"}
    command_item = {
        "command": "pytest -q",
        "commandActions": [],
        "cwd": "/repo",
        "id": "item_command_1",
        "status": "completed",
        "type": "commandExecution",
    }
    agent_item_two = {"id": "item_agent_2", "text": "", "type": "agentMessage"}

    aggregator.observe(
        ItemStartedEvent(
            thread_id="thread_1",
            turn_id="turn_1",
            item=agent_item_one,
        )
    )
    aggregator.observe(
        AgentTextDeltaEvent(
            thread_id="thread_1",
            turn_id="turn_1",
            item_id="item_agent_1",
            text_delta="Hello",
        )
    )
    aggregator.observe(
        AgentTextDeltaEvent(
            thread_id="thread_1",
            turn_id="turn_1",
            item_id="item_agent_1",
            text_delta=" world",
        )
    )
    aggregator.observe(
        ItemStartedEvent(
            thread_id="thread_1",
            turn_id="turn_1",
            item=command_item,
        )
    )
    aggregator.observe(
        CommandOutputDeltaEvent(
            thread_id="thread_1",
            turn_id="turn_1",
            item_id="item_command_1",
            output_delta="FAILED line 1\n",
        )
    )
    aggregator.observe(
        CommandOutputDeltaEvent(
            thread_id="thread_1",
            turn_id="turn_1",
            item_id="item_command_1",
            output_delta="FAILED line 2\n",
        )
    )
    aggregator.observe(
        ItemStartedEvent(
            thread_id="thread_1",
            turn_id="turn_1",
            item=agent_item_two,
        )
    )
    aggregator.observe(
        AgentTextDeltaEvent(
            thread_id="thread_1",
            turn_id="turn_1",
            item_id="item_agent_2",
            text_delta="Second answer",
        )
    )
    aggregator.observe(
        RawNotificationEvent(
            method="item/plan/delta",
            params={
                "delta": "Reproduce failure first.",
                "itemId": "item_plan_1",
                "threadId": "thread_1",
                "turnId": "turn_1",
            },
        )
    )

    assert aggregator.result is None
    assert aggregator.assistant_text == "Hello world\n\nSecond answer"
    assert aggregator.command_output == "FAILED line 1\nFAILED line 2\n"
    assert aggregator.plan_text == "Reproduce failure first."
    assert aggregator.reasoning_text is None
    assert aggregator.items == (agent_item_one, command_item, agent_item_two)

    item_aggregations = aggregator.item_aggregations
    assert [item.item_id for item in item_aggregations] == [
        "item_agent_1",
        "item_command_1",
        "item_agent_2",
        "item_plan_1",
    ]
    assert [item.item_type for item in item_aggregations] == [
        "agentMessage",
        "commandExecution",
        "agentMessage",
        "plan",
    ]
    assert item_aggregations[0].agent_text == "Hello world"
    assert item_aggregations[1].command_output == "FAILED line 1\nFAILED line 2\n"
    assert item_aggregations[2].agent_text == "Second answer"
    assert item_aggregations[3].item is None
    assert item_aggregations[3].plan_text == "Reproduce failure first."


def test_turn_output_aggregator_builds_fallback_result_on_completion_without_result() -> None:
    aggregator = TurnOutputAggregator()

    aggregator.observe(
        AgentTextDeltaEvent(
            thread_id="thread_1",
            turn_id="turn_1",
            item_id="item_agent_1",
            text_delta="Fallback answer.",
        )
    )
    aggregator.observe(
        TurnCompletedEvent(
            thread_id="thread_1",
            turn_id="turn_1",
            turn_status="completed",
        )
    )

    assert aggregator.result is not None
    assert aggregator.result.thread_id == "thread_1"
    assert aggregator.result.turn_id == "turn_1"
    assert aggregator.result.status == "completed"
    assert aggregator.result.assistant_text == "Fallback answer."
    assert len(aggregator.result.item_aggregations) == 1
    assert aggregator.result.item_aggregations[0].item_id == "item_agent_1"

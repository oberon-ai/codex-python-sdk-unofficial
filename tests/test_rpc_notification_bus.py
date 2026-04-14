from __future__ import annotations

import asyncio

import pytest

from codex_agent_sdk import NotificationSubscriptionOverflowError
from codex_agent_sdk.rpc import JsonRpcNotification, JsonRpcNotificationBus

IO_TIMEOUT_SECONDS = 1.0


def _notification(
    method: str,
    *,
    thread_id: str | None = None,
    turn_id: str | None = None,
) -> JsonRpcNotification:
    params: dict[str, str] = {}
    if thread_id is not None:
        params["threadId"] = thread_id
    if turn_id is not None:
        params["turnId"] = turn_id
    return JsonRpcNotification(
        method=method,
        params=params,
        _params_present=True,
    )


@pytest.mark.asyncio
async def test_notification_bus_fans_out_to_filtered_and_catch_all_subscribers() -> None:
    bus = JsonRpcNotificationBus()
    catch_all = bus.subscribe_all().iter_notifications()
    method_only = bus.subscribe_method("turn/started").iter_notifications()
    thread_only = bus.subscribe_thread("thread_1").iter_notifications()
    turn_only = bus.subscribe_turn("turn_1", thread_id="thread_1").iter_notifications()
    other_thread = bus.subscribe_thread("thread_other").iter_notifications()

    initialized = _notification("initialized")
    turn_started = _notification("turn/started", thread_id="thread_1", turn_id="turn_1")

    await bus.publish(initialized)
    await bus.publish(turn_started)
    bus.close()

    assert await asyncio.wait_for(anext(catch_all), timeout=IO_TIMEOUT_SECONDS) == initialized
    assert await asyncio.wait_for(anext(catch_all), timeout=IO_TIMEOUT_SECONDS) == turn_started
    assert await asyncio.wait_for(anext(method_only), timeout=IO_TIMEOUT_SECONDS) == turn_started
    assert await asyncio.wait_for(anext(thread_only), timeout=IO_TIMEOUT_SECONDS) == turn_started
    assert await asyncio.wait_for(anext(turn_only), timeout=IO_TIMEOUT_SECONDS) == turn_started
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(other_thread), timeout=IO_TIMEOUT_SECONDS)


@pytest.mark.asyncio
async def test_notification_subscription_close_unregisters_itself() -> None:
    bus = JsonRpcNotificationBus()
    subscription = bus.subscribe_thread("thread_1")
    subscription_iter = subscription.iter_notifications()

    assert bus.subscriber_count == 1

    subscription.close()

    assert bus.subscriber_count == 0
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(subscription_iter), timeout=IO_TIMEOUT_SECONDS)


@pytest.mark.asyncio
async def test_notification_bus_closes_only_lagging_subscriber_on_overflow() -> None:
    bus = JsonRpcNotificationBus()
    lagging = bus.subscribe_thread("thread_1", max_queue_size=1).iter_notifications()
    healthy = bus.subscribe_thread("thread_1").iter_notifications()
    first = _notification("turn/started", thread_id="thread_1", turn_id="turn_1")
    second = _notification("item/started", thread_id="thread_1", turn_id="turn_1")
    third = _notification("turn/completed", thread_id="thread_1", turn_id="turn_1")

    await bus.publish(first)
    await bus.publish(second)
    await bus.publish(third)

    assert await asyncio.wait_for(anext(lagging), timeout=IO_TIMEOUT_SECONDS) == first
    with pytest.raises(NotificationSubscriptionOverflowError) as exc_info:
        await asyncio.wait_for(anext(lagging), timeout=IO_TIMEOUT_SECONDS)

    assert exc_info.value.thread_id == "thread_1"
    assert exc_info.value.max_queue_size == 1
    assert bus.subscriber_count == 1

    assert await asyncio.wait_for(anext(healthy), timeout=IO_TIMEOUT_SECONDS) == first
    assert await asyncio.wait_for(anext(healthy), timeout=IO_TIMEOUT_SECONDS) == second
    assert await asyncio.wait_for(anext(healthy), timeout=IO_TIMEOUT_SECONDS) == third

    bus.close()


@pytest.mark.asyncio
async def test_subscription_created_after_bus_failure_inherits_terminal_error() -> None:
    bus = JsonRpcNotificationBus()
    failure = RuntimeError("connection failed")

    bus.close(error=failure)
    subscription = bus.subscribe_all().iter_notifications()

    with pytest.raises(RuntimeError, match="connection failed"):
        await asyncio.wait_for(anext(subscription), timeout=IO_TIMEOUT_SECONDS)

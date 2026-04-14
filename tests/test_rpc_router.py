from __future__ import annotations

import asyncio

import pytest

from codex_agent_sdk import (
    DuplicateRequestIdError,
    DuplicateResponseError,
    JsonRpcMethodNotFoundError,
    LateResponseError,
    UnknownResponseIdError,
)
from codex_agent_sdk.rpc import (
    JsonRpcErrorObject,
    JsonRpcErrorResponse,
    JsonRpcRequestIdAllocator,
    JsonRpcRequestRegistry,
    JsonRpcSuccessResponse,
)

IO_TIMEOUT_SECONDS = 1.0


@pytest.mark.asyncio
async def test_request_id_allocator_is_incrementing_and_observes_manual_ids() -> None:
    allocator = JsonRpcRequestIdAllocator()

    assert await allocator.next_id() == 1
    assert await allocator.next_id() == 2

    await allocator.observe(10)

    assert await allocator.next_id() == 11


@pytest.mark.asyncio
async def test_registry_observes_remote_request_ids_for_future_allocations() -> None:
    registry = JsonRpcRequestRegistry()

    first = await registry.register_request("initialize")
    assert first.request_id == 1

    await registry.observe_remote_request_id(7)

    second = await registry.register_request("thread/start")
    assert second.request_id == 8


@pytest.mark.asyncio
async def test_registry_resolves_multiple_outstanding_requests_out_of_order() -> None:
    registry = JsonRpcRequestRegistry()
    first = await registry.register_request("thread/start")
    second = await registry.register_request("thread/resume")

    await registry.resolve_response(
        JsonRpcSuccessResponse(id=second.request_id, result={"threadId": "thread_2"})
    )
    await registry.resolve_response(
        JsonRpcSuccessResponse(id=first.request_id, result={"threadId": "thread_1"})
    )

    assert await asyncio.wait_for(first.future, timeout=IO_TIMEOUT_SECONDS) == {
        "threadId": "thread_1"
    }
    assert await asyncio.wait_for(second.future, timeout=IO_TIMEOUT_SECONDS) == {
        "threadId": "thread_2"
    }
    assert registry.pending_count == 0


@pytest.mark.asyncio
async def test_registry_maps_error_responses_onto_waiter_future() -> None:
    registry = JsonRpcRequestRegistry()
    pending = await registry.register_request("thread/start", request_id="req-7")

    await registry.resolve_response(
        JsonRpcErrorResponse(
            id="req-7",
            error=JsonRpcErrorObject(code=-32601, message="missing method"),
        )
    )

    with pytest.raises(JsonRpcMethodNotFoundError) as exc_info:
        await asyncio.wait_for(pending.future, timeout=IO_TIMEOUT_SECONDS)

    assert exc_info.value.method == "thread/start"
    assert exc_info.value.request_id == "req-7"
    assert registry.pending_count == 0


@pytest.mark.asyncio
async def test_registry_rejects_duplicate_request_ids() -> None:
    registry = JsonRpcRequestRegistry()
    await registry.register_request("initialize", request_id="req-1")

    with pytest.raises(DuplicateRequestIdError) as exc_info:
        await registry.register_request("thread/start", request_id="req-1")

    assert exc_info.value.request_id == "req-1"


@pytest.mark.asyncio
async def test_registry_rejects_unknown_response_ids() -> None:
    registry = JsonRpcRequestRegistry()

    with pytest.raises(UnknownResponseIdError) as exc_info:
        await registry.resolve_response(
            JsonRpcSuccessResponse(id="req-missing", result={"ok": True})
        )

    assert exc_info.value.request_id == "req-missing"


@pytest.mark.asyncio
async def test_registry_rejects_duplicate_responses_for_completed_request() -> None:
    registry = JsonRpcRequestRegistry()
    pending = await registry.register_request("thread/start", request_id="req-2")

    await registry.resolve_response(JsonRpcSuccessResponse(id="req-2", result={"threadId": "t"}))
    assert await asyncio.wait_for(pending.future, timeout=IO_TIMEOUT_SECONDS) == {"threadId": "t"}

    with pytest.raises(DuplicateResponseError) as exc_info:
        await registry.resolve_response(
            JsonRpcSuccessResponse(id="req-2", result={"threadId": "t"})
        )

    assert exc_info.value.request_id == "req-2"
    assert exc_info.value.method == "thread/start"


@pytest.mark.asyncio
async def test_registry_marks_timed_out_request_as_late_response_target() -> None:
    registry = JsonRpcRequestRegistry()
    pending = await registry.register_request("thread/start", request_id="req-3")

    released = await registry.cancel_request("req-3", reason="timed_out")

    assert released is True
    assert registry.pending_count == 0
    assert pending.future.cancelled()

    with pytest.raises(LateResponseError) as exc_info:
        await registry.resolve_response(
            JsonRpcSuccessResponse(id="req-3", result={"threadId": "thread_late"})
        )

    assert exc_info.value.request_id == "req-3"
    assert exc_info.value.method == "thread/start"
    assert exc_info.value.release_reason == "timed_out"


@pytest.mark.asyncio
async def test_registry_fail_all_releases_waiters_without_leaking_state() -> None:
    registry = JsonRpcRequestRegistry()
    first = await registry.register_request("thread/start", request_id="req-4")
    second = await registry.register_request("thread/resume", request_id="req-5")

    error = RuntimeError("connection closed")
    await registry.fail_all(error, reason="closed")

    with pytest.raises(RuntimeError, match="connection closed"):
        await asyncio.wait_for(first.future, timeout=IO_TIMEOUT_SECONDS)
    with pytest.raises(RuntimeError, match="connection closed"):
        await asyncio.wait_for(second.future, timeout=IO_TIMEOUT_SECONDS)

    assert registry.pending_count == 0

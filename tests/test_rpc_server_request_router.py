from __future__ import annotations

import asyncio

import pytest

from codex_agent_sdk import (
    DuplicateServerRequestIdError,
    ServerRequestAlreadyRespondedError,
    UnknownServerRequestIdError,
)
from codex_agent_sdk.rpc import (
    SERVER_REQUEST_NOT_HANDLED,
    JsonRpcErrorObject,
    JsonRpcErrorResponse,
    JsonRpcNotification,
    JsonRpcRequest,
    JsonRpcServerRequestRouter,
    JsonRpcSuccessResponse,
)

IO_TIMEOUT_SECONDS = 1.0


def _stream_waiter_task_names() -> list[str]:
    current_task = asyncio.current_task()
    return sorted(
        task.get_name()
        for task in asyncio.all_tasks()
        if task is not current_task and task.get_name().startswith("codex-agent-sdk.stream-")
    )


async def _next_server_request(router: JsonRpcServerRequestRouter) -> JsonRpcRequest:
    return await anext(router.iter_requests())


@pytest.mark.asyncio
async def test_server_request_router_runs_registered_handler_and_sends_success_response() -> None:
    sent_responses: list[object] = []
    response_written = asyncio.Event()

    async def _send_response(envelope: object) -> None:
        sent_responses.append(envelope)
        response_written.set()

    router = JsonRpcServerRequestRouter(response_sender=_send_response)

    async def _handler(request: JsonRpcRequest) -> object:
        assert request.method == "item/commandExecution/requestApproval"
        assert request.params == {"threadId": "thread_1", "turnId": "turn_1"}
        return {"decision": "accept"}

    router.register_handler("item/commandExecution/requestApproval", _handler)

    await router.route_request(
        JsonRpcRequest(
            id="approval-1",
            method="item/commandExecution/requestApproval",
            params={"threadId": "thread_1", "turnId": "turn_1"},
            _params_present=True,
        )
    )

    await asyncio.wait_for(response_written.wait(), timeout=IO_TIMEOUT_SECONDS)

    assert sent_responses == [
        JsonRpcSuccessResponse(id="approval-1", result={"decision": "accept"})
    ]
    assert router.pending_count == 1
    assert await router.resolve_request("approval-1") is True
    assert router.pending_count == 0


@pytest.mark.asyncio
async def test_server_request_router_can_fall_through_to_raw_stream() -> None:
    sent_responses: list[object] = []

    async def _send_response(envelope: object) -> None:
        sent_responses.append(envelope)

    router = JsonRpcServerRequestRouter(response_sender=_send_response)

    async def _handler(request: JsonRpcRequest) -> object:
        assert request.method == "item/fileChange/requestApproval"
        return SERVER_REQUEST_NOT_HANDLED

    router.register_handler("item/fileChange/requestApproval", _handler)
    request_task = asyncio.create_task(_next_server_request(router))

    await router.route_request(
        JsonRpcRequest(
            id="file-approval-1",
            method="item/fileChange/requestApproval",
            params={"threadId": "thread_1", "turnId": "turn_1", "itemId": "item_1"},
            _params_present=True,
        )
    )

    request = await asyncio.wait_for(request_task, timeout=IO_TIMEOUT_SECONDS)

    assert request.method == "item/fileChange/requestApproval"
    assert request.request_id == "file-approval-1"
    assert sent_responses == []
    assert router.pending_count == 1


@pytest.mark.asyncio
async def test_server_request_router_surfaces_unknown_requests_by_default() -> None:
    router = JsonRpcServerRequestRouter()
    request_task = asyncio.create_task(_next_server_request(router))

    await router.route_request(
        JsonRpcRequest(
            id="mystery-1",
            method="item/experimental/requestApproval",
            params={"threadId": "thread_1"},
            _params_present=True,
        )
    )

    request = await asyncio.wait_for(request_task, timeout=IO_TIMEOUT_SECONDS)

    assert request.method == "item/experimental/requestApproval"
    assert request.request_id == "mystery-1"
    assert router.pending_count == 1


@pytest.mark.asyncio
async def test_server_request_router_can_reject_unknown_methods() -> None:
    sent_responses: list[object] = []
    response_written = asyncio.Event()

    async def _send_response(envelope: object) -> None:
        sent_responses.append(envelope)
        response_written.set()

    router = JsonRpcServerRequestRouter(
        response_sender=_send_response,
        unhandled_policy="reject",
    )

    await router.route_request(
        JsonRpcRequest(
            id=61,
            method="mcpServer/unknown",
            params={"threadId": "thread_1"},
            _params_present=True,
        )
    )

    await asyncio.wait_for(response_written.wait(), timeout=IO_TIMEOUT_SECONDS)

    assert sent_responses == [
        JsonRpcErrorResponse(
            id=61,
            error=JsonRpcErrorObject(
                code=-32601,
                message="unsupported server request method: mcpServer/unknown",
            ),
        )
    ]


@pytest.mark.asyncio
async def test_server_request_router_converts_handler_crash_into_error_response() -> None:
    sent_responses: list[object] = []
    response_written = asyncio.Event()

    async def _send_response(envelope: object) -> None:
        sent_responses.append(envelope)
        response_written.set()

    router = JsonRpcServerRequestRouter(response_sender=_send_response)

    async def _handler(request: JsonRpcRequest) -> object:
        raise RuntimeError(f"boom for {request.request_id}")

    router.register_handler("item/tool/requestUserInput", _handler)

    await router.route_request(
        JsonRpcRequest(
            id="input-1",
            method="item/tool/requestUserInput",
            params={"threadId": "thread_1", "turnId": "turn_1"},
            _params_present=True,
        )
    )

    await asyncio.wait_for(response_written.wait(), timeout=IO_TIMEOUT_SECONDS)

    assert sent_responses == [
        JsonRpcErrorResponse(
            id="input-1",
            error=JsonRpcErrorObject(
                code=-32603,
                message="client server-request handler failed",
            ),
        )
    ]


@pytest.mark.asyncio
async def test_server_request_router_supports_manual_responses_and_state_tracking() -> None:
    sent_responses: list[object] = []

    async def _send_response(envelope: object) -> None:
        sent_responses.append(envelope)

    router = JsonRpcServerRequestRouter(response_sender=_send_response)
    request_task = asyncio.create_task(_next_server_request(router))

    await router.route_request(
        JsonRpcRequest(
            id="approval-2",
            method="item/permissions/requestApproval",
            params={"threadId": "thread_1", "turnId": "turn_1"},
            _params_present=True,
        )
    )
    request = await asyncio.wait_for(request_task, timeout=IO_TIMEOUT_SECONDS)
    assert request.request_id == "approval-2"

    await router.respond("approval-2", {"permissions": {"fileSystem": {"write": ["/tmp"]}}})

    assert sent_responses == [
        JsonRpcSuccessResponse(
            id="approval-2",
            result={"permissions": {"fileSystem": {"write": ["/tmp"]}}},
        )
    ]

    with pytest.raises(ServerRequestAlreadyRespondedError) as exc_info:
        await router.respond("approval-2", {"decision": "accept"})

    assert exc_info.value.request_id == "approval-2"

    assert await router.resolve_request("approval-2") is True

    with pytest.raises(UnknownServerRequestIdError) as unknown_exc:
        await router.respond("approval-2", {"decision": "accept"})

    assert unknown_exc.value.request_id == "approval-2"


@pytest.mark.asyncio
async def test_server_request_router_rejects_duplicate_pending_request_ids() -> None:
    router = JsonRpcServerRequestRouter()
    request = JsonRpcRequest(id=7, method="item/tool/call")

    await router.route_request(request)

    with pytest.raises(DuplicateServerRequestIdError) as exc_info:
        await router.route_request(JsonRpcRequest(id=7, method="mcpServer/elicitation/request"))

    assert exc_info.value.request_id == 7


@pytest.mark.asyncio
async def test_server_request_router_clears_pending_requests_from_resolution_notification() -> None:
    router = JsonRpcServerRequestRouter()
    await router.route_request(
        JsonRpcRequest(
            id="approval-3",
            method="item/commandExecution/requestApproval",
            params={"threadId": "thread_1", "turnId": "turn_1"},
            _params_present=True,
        )
    )

    cleared = await router.observe_resolution_notification(
        JsonRpcNotification(
            method="serverRequest/resolved",
            params={"threadId": "thread_1", "requestId": "approval-3"},
            _params_present=True,
        )
    )

    assert cleared is True
    assert router.pending_count == 0


@pytest.mark.asyncio
async def test_cancelling_server_request_consumer_cleans_up_internal_wait_tasks() -> None:
    router = JsonRpcServerRequestRouter()
    consumer_task = asyncio.create_task(_next_server_request(router), name="server-request")

    await asyncio.sleep(0.05)
    consumer_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(consumer_task, timeout=IO_TIMEOUT_SECONDS)

    await asyncio.sleep(0)
    assert _stream_waiter_task_names() == []

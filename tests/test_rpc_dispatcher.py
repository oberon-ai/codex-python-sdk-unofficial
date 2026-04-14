from __future__ import annotations

import asyncio
from typing import cast

import pytest

from codex_agent_sdk.errors import MessageDecodeError
from codex_agent_sdk.rpc import (
    JsonRpcBackgroundDispatcher,
    JsonRpcNotification,
    JsonRpcNotificationBus,
    JsonRpcRequest,
    JsonRpcRequestRegistry,
    JsonRpcServerRequestRouter,
    JsonRpcSuccessResponse,
)
from codex_agent_sdk.transport import StdioTransport

IO_TIMEOUT_SECONDS = 1.0


class _ScriptedTransport:
    def __init__(
        self,
        *,
        envelopes: list[object] | None = None,
        read_error: BaseException | None = None,
        stderr_tail: str | None = None,
    ) -> None:
        self._envelopes = list(envelopes or [])
        self._read_error = read_error
        self._allow_eof = asyncio.Event()
        self.stderr_tail = stderr_tail

    async def read_stdout_envelope(self) -> object | None:
        await asyncio.sleep(0)
        if self._read_error is not None:
            error = self._read_error
            self._read_error = None
            raise error
        if self._envelopes:
            return self._envelopes.pop(0)
        await self._allow_eof.wait()
        return None

    def allow_eof(self) -> None:
        self._allow_eof.set()


async def _stop_dispatcher(
    dispatcher: JsonRpcBackgroundDispatcher,
    transport: _ScriptedTransport,
) -> None:
    stop_task = asyncio.create_task(dispatcher.stop())
    await asyncio.sleep(0)
    transport.allow_eof()
    await asyncio.wait_for(stop_task, timeout=IO_TIMEOUT_SECONDS)


async def _read_next_notification(
    notifications: JsonRpcNotificationBus,
) -> JsonRpcNotification:
    return await anext(notifications.iter_notifications())


async def _read_next_server_request(
    server_requests: JsonRpcServerRequestRouter,
) -> JsonRpcRequest:
    return await anext(server_requests.iter_requests())


@pytest.mark.asyncio
async def test_dispatcher_start_is_idempotent_and_owns_one_task() -> None:
    transport = _ScriptedTransport()
    dispatcher = JsonRpcBackgroundDispatcher(
        transport=cast(StdioTransport, transport),
        requests=JsonRpcRequestRegistry(),
        notifications=JsonRpcNotificationBus(),
        server_requests=JsonRpcServerRequestRouter(),
    )

    await dispatcher.start()
    first_task = dispatcher.dispatch_task
    assert first_task is not None
    assert dispatcher.is_running is True

    await dispatcher.start()

    assert dispatcher.dispatch_task is first_task

    await _stop_dispatcher(dispatcher, transport)
    assert dispatcher.is_running is False


@pytest.mark.asyncio
async def test_dispatcher_routes_response_notification_and_server_request() -> None:
    registry = JsonRpcRequestRegistry()
    notifications = JsonRpcNotificationBus()
    server_requests = JsonRpcServerRequestRouter()
    pending = await registry.register_request("thread/start", request_id="req-1")
    transport = _ScriptedTransport(
        envelopes=[
            JsonRpcSuccessResponse(id="req-1", result={"threadId": "thread_1"}),
            JsonRpcNotification(
                method="thread/updated",
                params={"threadId": "thread_1", "status": "running"},
                _params_present=True,
            ),
            JsonRpcRequest(
                id="approval-1",
                method="approval/requested",
                params={"threadId": "thread_1", "kind": "command"},
                _params_present=True,
            ),
        ]
    )
    dispatcher = JsonRpcBackgroundDispatcher(
        transport=cast(StdioTransport, transport),
        requests=registry,
        notifications=notifications,
        server_requests=server_requests,
    )

    notification_task: asyncio.Task[JsonRpcNotification] = asyncio.create_task(
        _read_next_notification(notifications)
    )
    server_request_task: asyncio.Task[JsonRpcRequest] = asyncio.create_task(
        _read_next_server_request(server_requests)
    )

    await dispatcher.start()

    result = await asyncio.wait_for(pending.future, timeout=IO_TIMEOUT_SECONDS)
    notification = await asyncio.wait_for(notification_task, timeout=IO_TIMEOUT_SECONDS)
    server_request = await asyncio.wait_for(server_request_task, timeout=IO_TIMEOUT_SECONDS)

    assert result == {"threadId": "thread_1"}
    assert notification.method == "thread/updated"
    assert notification.params == {"threadId": "thread_1", "status": "running"}
    assert server_request.method == "approval/requested"
    assert server_request.request_id == "approval-1"
    assert server_request.params == {"threadId": "thread_1", "kind": "command"}

    await _stop_dispatcher(dispatcher, transport)


@pytest.mark.asyncio
async def test_dispatcher_fatal_error_propagates_to_waiters_and_callback() -> None:
    registry = JsonRpcRequestRegistry()
    notifications = JsonRpcNotificationBus()
    server_requests = JsonRpcServerRequestRouter()
    pending = await registry.register_request("thread/start", request_id="req-2")
    decode_error = MessageDecodeError(
        '{"id":"req-2","result":',
        original_error=ValueError("truncated JSON"),
        stderr_tail="broken frame",
    )
    transport = _ScriptedTransport(read_error=decode_error, stderr_tail="broken frame")
    fatal_errors: list[BaseException] = []

    async def _record_fatal_error(exc: BaseException) -> None:
        fatal_errors.append(exc)

    dispatcher = JsonRpcBackgroundDispatcher(
        transport=cast(StdioTransport, transport),
        requests=registry,
        notifications=notifications,
        server_requests=server_requests,
        on_fatal_error=_record_fatal_error,
    )

    notification_task: asyncio.Task[JsonRpcNotification] = asyncio.create_task(
        _read_next_notification(notifications)
    )
    server_request_task: asyncio.Task[JsonRpcRequest] = asyncio.create_task(
        _read_next_server_request(server_requests)
    )

    await dispatcher.start()
    dispatch_task = dispatcher.dispatch_task
    assert dispatch_task is not None
    await asyncio.wait_for(dispatch_task, timeout=IO_TIMEOUT_SECONDS)

    with pytest.raises(MessageDecodeError, match="failed to decode app-server message"):
        await asyncio.wait_for(pending.future, timeout=IO_TIMEOUT_SECONDS)
    with pytest.raises(MessageDecodeError, match="failed to decode app-server message"):
        await asyncio.wait_for(notification_task, timeout=IO_TIMEOUT_SECONDS)
    with pytest.raises(MessageDecodeError, match="failed to decode app-server message"):
        await asyncio.wait_for(server_request_task, timeout=IO_TIMEOUT_SECONDS)

    assert fatal_errors == [decode_error]

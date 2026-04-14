"""Async JSON-RPC connection orchestration on top of the stdio transport."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from typing import cast

from ..errors import (
    RequestTimeoutError,
    TransportClosedError,
    UnexpectedMessageError,
    map_jsonrpc_error,
)
from ..transport import StdioTransport
from .jsonrpc import (
    JsonRpcEnvelope,
    JsonRpcErrorResponse,
    JsonRpcId,
    JsonRpcNotification,
    JsonRpcRequest,
    JsonRpcResponseEnvelope,
    JsonRpcSuccessResponse,
    is_jsonrpc_notification_envelope,
    is_jsonrpc_request_envelope,
    is_jsonrpc_response_envelope,
)

_STREAM_CLOSED = object()


@dataclass(slots=True)
class _PendingRequest:
    method: str
    future: asyncio.Future[object]


class JsonRpcConnection:
    """Manage request correlation and inbound routing for one app-server process."""

    def __init__(self, transport: StdioTransport | None = None) -> None:
        self.transport = transport or StdioTransport()
        self._pending_requests: dict[JsonRpcId, _PendingRequest] = {}
        self._notification_queue: asyncio.Queue[JsonRpcNotification] = asyncio.Queue()
        self._server_request_queue: asyncio.Queue[JsonRpcRequest] = asyncio.Queue()
        self._request_id = 0
        self._request_id_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._closed_event = asyncio.Event()
        self._close_requested = False
        self._terminal_error: BaseException | None = None

    @property
    def terminal_error(self) -> BaseException | None:
        return self._terminal_error

    @property
    def is_closed(self) -> bool:
        return self._closed_event.is_set()

    async def start(self, *, startup_timeout: float | None = None) -> None:
        """Start the subprocess transport and the background reader once."""

        self._raise_if_closed()

        async with self._start_lock:
            self._raise_if_closed()
            if self._reader_task is not None:
                return

            await self.transport.start(timeout=startup_timeout)
            self._reader_task = asyncio.create_task(
                self._reader_loop(),
                name="codex-agent-sdk.rpc-reader",
            )

    async def request(
        self,
        method: str,
        params: object | None = None,
        *,
        timeout: float | None = None,
    ) -> object:
        """Send one JSON-RPC request and await its result."""

        await self.start()
        self._raise_if_closed()

        request_id = await self._next_request_id()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[object] = loop.create_future()
        self._pending_requests[request_id] = _PendingRequest(method=method, future=future)

        envelope = _build_request_envelope(request_id=request_id, method=method, params=params)
        try:
            await self.transport.write_stdin_envelope(envelope)
        except asyncio.CancelledError:
            self._drop_pending_request(request_id)
            raise
        except BaseException as exc:
            self._drop_pending_request(request_id)
            await self._fail_connection(exc)
            raise

        return await self._await_request_result(
            request_id=request_id,
            method=method,
            future=future,
            timeout=timeout,
        )

    async def notify(self, method: str, params: object | None = None) -> None:
        """Send one JSON-RPC notification without waiting for a result."""

        await self.start()
        self._raise_if_closed()

        envelope = _build_notification_envelope(method=method, params=params)
        try:
            await self.transport.write_stdin_envelope(envelope)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await self._fail_connection(exc)
            raise

    async def iter_notifications(self) -> AsyncIterator[JsonRpcNotification]:
        """Iterate raw JSON-RPC notifications until close or connection failure."""

        while True:
            next_item = await self._next_stream_item(self._notification_queue)
            if next_item is _STREAM_CLOSED:
                return
            yield cast(JsonRpcNotification, next_item)

    async def iter_server_requests(self) -> AsyncIterator[JsonRpcRequest]:
        """Iterate raw server-initiated JSON-RPC requests until close or failure."""

        while True:
            next_item = await self._next_stream_item(self._server_request_queue)
            if next_item is _STREAM_CLOSED:
                return
            yield cast(JsonRpcRequest, next_item)

    async def close(self) -> None:
        """Close the connection and release all pending waiters."""

        reader_task = self._reader_task
        async with self._close_lock:
            if not self._closed_event.is_set():
                self._close_requested = True
                self._release_pending_requests(
                    TransportClosedError("app-server connection closed before request completion")
                )
                self._closed_event.set()

        await self.transport.close()
        if reader_task is not None and reader_task is not asyncio.current_task():
            await asyncio.shield(reader_task)

    async def _reader_loop(self) -> None:
        try:
            while True:
                envelope = await self.transport.read_stdout_envelope()
                if envelope is None:
                    if self._close_requested:
                        return
                    await self._fail_connection(
                        TransportClosedError(
                            "app-server stdout reached EOF while requests were still possible",
                            stderr_tail=self.transport.stderr_tail,
                        )
                    )
                    return

                await self._dispatch_envelope(envelope)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if self._close_requested:
                return
            await self._fail_connection(exc)

    async def _dispatch_envelope(self, envelope: JsonRpcEnvelope) -> None:
        if is_jsonrpc_response_envelope(envelope):
            self._handle_response(cast(JsonRpcResponseEnvelope, envelope))
            return
        if is_jsonrpc_request_envelope(envelope):
            await self._server_request_queue.put(cast(JsonRpcRequest, envelope))
            return
        if is_jsonrpc_notification_envelope(envelope):
            await self._notification_queue.put(cast(JsonRpcNotification, envelope))
            return
        raise UnexpectedMessageError(f"received invalid JSON-RPC envelope shape: {envelope!r}")

    def _handle_response(self, envelope: JsonRpcResponseEnvelope) -> None:
        request_id = envelope.request_id
        pending = self._pending_requests.pop(request_id, None)
        if pending is None:
            raise UnexpectedMessageError(
                f"received JSON-RPC response for unknown request_id={request_id!r}"
            )

        future = pending.future
        if future.done():
            return

        if isinstance(envelope, JsonRpcErrorResponse):
            error_payload = envelope.error
            future.set_exception(
                map_jsonrpc_error(
                    error_payload.code,
                    error_payload.message,
                    data=error_payload.data if error_payload.has_data else None,
                    method=pending.method,
                    request_id=request_id,
                )
            )
            return

        if not isinstance(envelope, JsonRpcSuccessResponse):
            raise UnexpectedMessageError(
                f"received non-response envelope while handling request_id={request_id!r}"
            )
        future.set_result(envelope.result)

    async def _await_request_result(
        self,
        *,
        request_id: JsonRpcId,
        method: str,
        future: asyncio.Future[object],
        timeout: float | None,
    ) -> object:
        try:
            if timeout is None:
                return await asyncio.shield(future)
            timeout_seconds = timeout
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout_seconds)
        except TimeoutError as exc:
            assert timeout is not None
            raise RequestTimeoutError(
                method=method,
                timeout_seconds=timeout,
                request_id=request_id,
            ) from exc

    async def _next_request_id(self) -> int:
        async with self._request_id_lock:
            self._request_id += 1
            return self._request_id

    async def _next_stream_item(
        self,
        queue: asyncio.Queue[JsonRpcNotification] | asyncio.Queue[JsonRpcRequest],
    ) -> JsonRpcNotification | JsonRpcRequest | object:
        if not queue.empty():
            return queue.get_nowait()
        if self._closed_event.is_set():
            error = self._terminal_error
            if error is None:
                return _STREAM_CLOSED
            raise error

        get_task = asyncio.create_task(queue.get(), name="codex-agent-sdk.stream-get")
        closed_task = asyncio.create_task(
            self._closed_event.wait(),
            name="codex-agent-sdk.stream-close-wait",
        )
        done, pending = await asyncio.wait(
            {get_task, closed_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        if get_task in done:
            return get_task.result()

        if not queue.empty():
            return await queue.get()

        error = self._terminal_error
        if error is None:
            return _STREAM_CLOSED
        raise error

    async def _fail_connection(self, exc: BaseException) -> None:
        async with self._close_lock:
            if self._closed_event.is_set():
                return
            self._terminal_error = exc
            self._close_requested = True
            self._release_pending_requests(exc)
            self._closed_event.set()

        await self.transport.close()

    def _release_pending_requests(self, exc: BaseException) -> None:
        pending_requests = tuple(self._pending_requests.values())
        self._pending_requests.clear()
        for pending in pending_requests:
            if not pending.future.done():
                pending.future.set_exception(exc)

    def _drop_pending_request(self, request_id: JsonRpcId) -> None:
        pending = self._pending_requests.pop(request_id, None)
        if pending is not None and not pending.future.done():
            pending.future.cancel()

    def _raise_if_closed(self) -> None:
        error = self._terminal_error
        if error is not None:
            raise error
        if self._closed_event.is_set():
            raise TransportClosedError(
                "app-server connection is already closed",
                stderr_tail=self.transport.stderr_tail,
            )


def _build_request_envelope(
    *,
    request_id: JsonRpcId,
    method: str,
    params: object | None,
) -> JsonRpcEnvelope:
    return JsonRpcRequest(
        id=request_id,
        method=method,
        params=params,
        _params_present=params is not None,
    )


def _build_notification_envelope(
    *,
    method: str,
    params: object | None,
) -> JsonRpcEnvelope:
    return JsonRpcNotification(
        method=method,
        params=params,
        _params_present=params is not None,
    )


__all__ = [
    "JsonRpcConnection",
]

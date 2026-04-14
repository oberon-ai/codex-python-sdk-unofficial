"""Async JSON-RPC connection orchestration on top of the stdio transport."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ..errors import (
    RequestTimeoutError,
    TransportClosedError,
)
from ..transport import StdioTransport
from .jsonrpc import (
    JsonRpcEnvelope,
    JsonRpcId,
    JsonRpcNotification,
    JsonRpcRequest,
)
from .router import (
    JsonRpcBackgroundDispatcher,
    JsonRpcNotificationBus,
    JsonRpcRequestRegistry,
    JsonRpcServerRequestRouter,
)


class JsonRpcConnection:
    """Manage request correlation and inbound routing for one app-server process."""

    def __init__(self, transport: StdioTransport | None = None) -> None:
        self.transport = transport or StdioTransport()
        self._requests = JsonRpcRequestRegistry()
        self._notifications = JsonRpcNotificationBus()
        self._server_requests = JsonRpcServerRequestRouter()
        self._start_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._dispatcher = JsonRpcBackgroundDispatcher(
            transport=self.transport,
            requests=self._requests,
            notifications=self._notifications,
            server_requests=self._server_requests,
            on_fatal_error=self._fail_connection,
        )
        self._closed_event = asyncio.Event()
        self._terminal_error: BaseException | None = None

    @property
    def terminal_error(self) -> BaseException | None:
        return self._terminal_error

    @property
    def is_closed(self) -> bool:
        return self._closed_event.is_set()

    async def start(self, *, startup_timeout: float | None = None) -> None:
        """Start the subprocess transport and the background dispatcher once."""

        self._raise_if_closed()

        async with self._start_lock:
            self._raise_if_closed()
            if self._dispatcher.dispatch_task is not None:
                return

            await self.transport.start(timeout=startup_timeout)
            await self._dispatcher.start()

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

        pending = await self._requests.register_request(method)
        request_id = pending.request_id
        future = pending.future

        envelope = _build_request_envelope(request_id=request_id, method=method, params=params)
        try:
            await self.transport.write_stdin_envelope(envelope)
        except asyncio.CancelledError:
            await self._requests.cancel_request(request_id, reason="cancelled")
            raise
        except BaseException as exc:
            await self._requests.fail_request(request_id, exc, reason="failed")
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

        async for notification in self._notifications.iter_notifications():
            yield notification

    async def iter_server_requests(self) -> AsyncIterator[JsonRpcRequest]:
        """Iterate raw server-initiated JSON-RPC requests until close or failure."""

        async for request in self._server_requests.iter_requests():
            yield request

    async def close(self) -> None:
        """Close the connection and release all pending waiters."""

        async with self._close_lock:
            if not self._closed_event.is_set():
                await self._requests.fail_all(
                    TransportClosedError("app-server connection closed before request completion"),
                    reason="closed",
                )
                self._notifications.close()
                self._server_requests.close()
                self._closed_event.set()

        await self.transport.close()
        await self._dispatcher.stop()

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
        except asyncio.CancelledError:
            await self._requests.cancel_request(request_id, reason="cancelled")
            raise
        except TimeoutError as exc:
            released = await self._requests.cancel_request(request_id, reason="timed_out")
            if not released and future.done():
                return future.result()
            assert timeout is not None
            raise RequestTimeoutError(
                method=method,
                timeout_seconds=timeout,
                request_id=request_id,
            ) from exc

    async def _fail_connection(self, exc: BaseException) -> None:
        async with self._close_lock:
            if self._closed_event.is_set():
                return
            self._terminal_error = exc
            await self._requests.fail_all(exc, reason="failed")
            self._notifications.close(error=exc)
            self._server_requests.close(error=exc)
            self._closed_event.set()

        await self.transport.close()

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

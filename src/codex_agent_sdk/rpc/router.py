"""Request correlation and inbound routing helpers for the JSON-RPC layer."""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Generic, Literal, TypeAlias, TypeVar, cast

from ..errors import (
    DuplicateRequestIdError,
    DuplicateResponseError,
    LateResponseError,
    NotificationSubscriptionOverflowError,
    TransportClosedError,
    UnexpectedMessageError,
    UnknownResponseIdError,
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
    is_jsonrpc_notification_envelope,
    is_jsonrpc_request_envelope,
    is_jsonrpc_response_envelope,
)

FinalizedRequestStatus: TypeAlias = Literal[
    "completed",
    "cancelled",
    "timed_out",
    "closed",
    "failed",
]
FatalDispatchCallback: TypeAlias = Callable[[BaseException], Awaitable[None]]

_LOGGER = logging.getLogger(__name__)
_STREAM_CLOSED = object()
DEFAULT_NOTIFICATION_SUBSCRIPTION_QUEUE_MAXSIZE = 256
_T = TypeVar("_T")


@dataclass(slots=True)
class PendingJsonRpcRequest:
    """One in-flight JSON-RPC request waiting for a matching response."""

    request_id: JsonRpcId
    method: str
    future: asyncio.Future[object]


@dataclass(frozen=True, slots=True)
class _FinalizedRequest:
    method: str
    status: FinalizedRequestStatus


class _InboundMessageStream(Generic[_T]):
    """Single-consumer async stream with explicit close and terminal-error state."""

    def __init__(self, *, max_queue_size: int = 0) -> None:
        if max_queue_size < 0:
            raise ValueError("max_queue_size must be non-negative")
        self._queue: asyncio.Queue[_T] = asyncio.Queue(maxsize=max_queue_size)
        self._closed_event = asyncio.Event()
        self._terminal_error: BaseException | None = None

    @property
    def is_closed(self) -> bool:
        return self._closed_event.is_set()

    @property
    def terminal_error(self) -> BaseException | None:
        return self._terminal_error

    async def put(self, item: _T) -> bool:
        if self._closed_event.is_set():
            return False
        await self._queue.put(item)
        return True

    def put_nowait(self, item: _T) -> bool:
        if self._closed_event.is_set():
            return False
        self._queue.put_nowait(item)
        return True

    def close(self, *, error: BaseException | None = None) -> None:
        if error is not None and self._terminal_error is None:
            self._terminal_error = error
        self._closed_event.set()

    async def iter_items(self) -> AsyncIterator[_T]:
        while True:
            next_item = await self._next_item()
            if next_item is _STREAM_CLOSED:
                return
            yield cast(_T, next_item)

    async def _next_item(self) -> _T | object:
        if not self._queue.empty():
            return self._queue.get_nowait()
        if self._closed_event.is_set():
            error = self._terminal_error
            if error is None:
                return _STREAM_CLOSED
            raise error

        get_task = asyncio.create_task(self._queue.get(), name="codex-agent-sdk.stream-get")
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

        if not self._queue.empty():
            return await self._queue.get()

        error = self._terminal_error
        if error is None:
            return _STREAM_CLOSED
        raise error


@dataclass(frozen=True, slots=True)
class _NotificationSubscriptionFilter:
    method: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None

    def matches(self, notification: JsonRpcNotification) -> bool:
        if self.method is not None and notification.method != self.method:
            return False

        if self.thread_id is None and self.turn_id is None:
            return True

        params = notification.params if notification.has_params else None
        if not isinstance(params, Mapping):
            return False

        if self.thread_id is not None and params.get("threadId") != self.thread_id:
            return False
        if self.turn_id is not None and params.get("turnId") != self.turn_id:
            return False
        return True


class JsonRpcNotificationSubscription:
    """One bounded notification subscription owned by ``JsonRpcNotificationBus``."""

    def __init__(
        self,
        *,
        bus: JsonRpcNotificationBus,
        subscription_id: int,
        method: str | None,
        thread_id: str | None,
        turn_id: str | None,
        max_queue_size: int,
    ) -> None:
        self._bus = bus
        self._subscription_id = subscription_id
        self._filter = _NotificationSubscriptionFilter(
            method=method,
            thread_id=thread_id,
            turn_id=turn_id,
        )
        self._max_queue_size = max_queue_size
        self._stream: _InboundMessageStream[JsonRpcNotification] = _InboundMessageStream(
            max_queue_size=max_queue_size
        )
        self._closed = False

    @property
    def method(self) -> str | None:
        return self._filter.method

    @property
    def thread_id(self) -> str | None:
        return self._filter.thread_id

    @property
    def turn_id(self) -> str | None:
        return self._filter.turn_id

    @property
    def max_queue_size(self) -> int:
        return self._max_queue_size

    @property
    def is_closed(self) -> bool:
        return self._stream.is_closed

    @property
    def terminal_error(self) -> BaseException | None:
        return self._stream.terminal_error

    def close(self) -> None:
        """Close the subscription and unregister it from the parent bus."""

        self._bus._unsubscribe(self._subscription_id)
        self._close_from_bus()

    def iter_notifications(self) -> AsyncIterator[JsonRpcNotification]:
        return self._iter_notifications()

    def __aiter__(self) -> AsyncIterator[JsonRpcNotification]:
        return self.iter_notifications()

    def _matches(self, notification: JsonRpcNotification) -> bool:
        return self._filter.matches(notification)

    def _enqueue(self, notification: JsonRpcNotification) -> bool:
        return self._stream.put_nowait(notification)

    def _overflow_error(self) -> NotificationSubscriptionOverflowError:
        return NotificationSubscriptionOverflowError(
            max_queue_size=self._max_queue_size,
            method=self.method,
            thread_id=self.thread_id,
            turn_id=self.turn_id,
        )

    def _close_from_bus(self, *, error: BaseException | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        self._stream.close(error=error)

    async def _iter_notifications(self) -> AsyncIterator[JsonRpcNotification]:
        try:
            async for notification in self._stream.iter_items():
                yield notification
        finally:
            self.close()


class JsonRpcRequestIdAllocator:
    """Deterministic, incrementing allocator for local JSON-RPC request ids."""

    def __init__(self, *, start_at: int = 0) -> None:
        self._last_request_id = start_at
        self._lock = asyncio.Lock()

    async def next_id(self) -> int:
        async with self._lock:
            self._last_request_id += 1
            return self._last_request_id

    async def observe(self, request_id: JsonRpcId) -> None:
        if not isinstance(request_id, int):
            return

        async with self._lock:
            if request_id > self._last_request_id:
                self._last_request_id = request_id


class JsonRpcRequestRegistry:
    """Own request ids, pending waiters, and response correlation state."""

    def __init__(
        self,
        *,
        finalized_history_limit: int = 256,
        allocator: JsonRpcRequestIdAllocator | None = None,
    ) -> None:
        if finalized_history_limit <= 0:
            raise ValueError("finalized_history_limit must be positive")

        self._allocator = allocator or JsonRpcRequestIdAllocator()
        self._finalized_history_limit = finalized_history_limit
        self._pending_requests: dict[JsonRpcId, PendingJsonRpcRequest] = {}
        self._finalized_requests: OrderedDict[JsonRpcId, _FinalizedRequest] = OrderedDict()
        self._lock = asyncio.Lock()

    @property
    def pending_count(self) -> int:
        return len(self._pending_requests)

    async def register_request(
        self,
        method: str,
        *,
        request_id: JsonRpcId | None = None,
    ) -> PendingJsonRpcRequest:
        """Register one outbound request and create its waiter future."""

        allocated_request_id = request_id
        if allocated_request_id is None:
            allocated_request_id = await self._allocator.next_id()
        else:
            await self._allocator.observe(allocated_request_id)

        loop = asyncio.get_running_loop()
        pending = PendingJsonRpcRequest(
            request_id=allocated_request_id,
            method=method,
            future=loop.create_future(),
        )

        async with self._lock:
            self._ensure_request_id_available(allocated_request_id, method=method)
            self._pending_requests[allocated_request_id] = pending

        return pending

    async def resolve_response(self, envelope: JsonRpcResponseEnvelope) -> None:
        """Resolve or reject the future for one inbound response envelope."""

        request_id = envelope.request_id

        async with self._lock:
            pending = self._pending_requests.pop(request_id, None)
            if pending is None:
                finalized = self._finalized_requests.get(request_id)
            else:
                finalized = None
                self._remember_finalized_request(
                    request_id,
                    method=pending.method,
                    status="completed",
                )

        if pending is None:
            if finalized is None:
                raise UnknownResponseIdError(request_id)
            if finalized.status == "completed":
                raise DuplicateResponseError(request_id, method=finalized.method)
            raise LateResponseError(
                request_id,
                release_reason=finalized.status,
                method=finalized.method,
            )

        future = pending.future
        if future.done():
            raise DuplicateResponseError(request_id, method=pending.method)

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

        future.set_result(envelope.result)

    async def cancel_request(
        self,
        request_id: JsonRpcId,
        *,
        reason: Literal["cancelled", "timed_out"],
    ) -> bool:
        """Release one pending request locally and cancel its waiter future."""

        pending = await self._pop_pending_request(request_id, status=reason)
        if pending is None:
            return False
        if not pending.future.done():
            pending.future.cancel()
        return True

    async def fail_request(
        self,
        request_id: JsonRpcId,
        exc: BaseException,
        *,
        reason: Literal["failed", "closed"],
    ) -> bool:
        """Release one pending request locally and fail its waiter future."""

        pending = await self._pop_pending_request(request_id, status=reason)
        if pending is None:
            return False
        if not pending.future.done():
            pending.future.set_exception(exc)
        return True

    async def fail_all(
        self,
        exc: BaseException,
        *,
        reason: Literal["failed", "closed"],
    ) -> None:
        """Release every outstanding request with the same terminal exception."""

        async with self._lock:
            pending_requests = tuple(self._pending_requests.values())
            self._pending_requests.clear()
            for pending in pending_requests:
                self._remember_finalized_request(
                    pending.request_id,
                    method=pending.method,
                    status=reason,
                )

        for pending in pending_requests:
            if not pending.future.done():
                pending.future.set_exception(exc)

    async def _pop_pending_request(
        self,
        request_id: JsonRpcId,
        *,
        status: Literal["cancelled", "timed_out", "failed", "closed"],
    ) -> PendingJsonRpcRequest | None:
        async with self._lock:
            pending = self._pending_requests.pop(request_id, None)
            if pending is None:
                return None
            self._remember_finalized_request(request_id, method=pending.method, status=status)
            return pending

    def _ensure_request_id_available(self, request_id: JsonRpcId, *, method: str) -> None:
        if request_id in self._pending_requests or request_id in self._finalized_requests:
            raise DuplicateRequestIdError(request_id, method=method)

    def _remember_finalized_request(
        self,
        request_id: JsonRpcId,
        *,
        method: str,
        status: FinalizedRequestStatus,
    ) -> None:
        self._finalized_requests[request_id] = _FinalizedRequest(method=method, status=status)
        while len(self._finalized_requests) > self._finalized_history_limit:
            self._finalized_requests.popitem(last=False)


class JsonRpcNotificationBus:
    """Bounded fan-out bus for raw JSON-RPC notifications."""

    def __init__(
        self,
        *,
        default_max_queue_size: int = DEFAULT_NOTIFICATION_SUBSCRIPTION_QUEUE_MAXSIZE,
    ) -> None:
        if default_max_queue_size <= 0:
            raise ValueError("default_max_queue_size must be positive")
        self._default_max_queue_size = default_max_queue_size
        self._next_subscription_id = 0
        self._subscriptions: weakref.WeakValueDictionary[int, JsonRpcNotificationSubscription] = (
            weakref.WeakValueDictionary()
        )
        self._closed = False
        self._terminal_error: BaseException | None = None

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def terminal_error(self) -> BaseException | None:
        return self._terminal_error

    @property
    def subscriber_count(self) -> int:
        return len(self._subscriptions)

    @property
    def default_max_queue_size(self) -> int:
        return self._default_max_queue_size

    async def publish(self, notification: JsonRpcNotification) -> None:
        if self._closed:
            return

        for subscription in tuple(self._subscriptions.values()):
            if not subscription._matches(notification):
                continue
            try:
                queued = subscription._enqueue(notification)
            except asyncio.QueueFull:
                overflow_error = subscription._overflow_error()
                _LOGGER.warning(
                    "closing lagging notification subscription "
                    "method=%r thread_id=%r turn_id=%r max_queue_size=%d",
                    subscription.method,
                    subscription.thread_id,
                    subscription.turn_id,
                    subscription.max_queue_size,
                )
                self._unsubscribe(subscription._subscription_id)
                subscription._close_from_bus(error=overflow_error)
                continue

            if not queued:
                self._unsubscribe(subscription._subscription_id)

    def close(self, *, error: BaseException | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        if error is not None and self._terminal_error is None:
            self._terminal_error = error

        for subscription in tuple(self._subscriptions.values()):
            self._unsubscribe(subscription._subscription_id)
            subscription._close_from_bus(error=error)

    def subscribe(
        self,
        *,
        method: str | None = None,
        thread_id: str | None = None,
        turn_id: str | None = None,
        max_queue_size: int | None = None,
    ) -> JsonRpcNotificationSubscription:
        """Create one queue-backed subscription for all or a filtered notification subset."""

        queue_limit = self._coerce_max_queue_size(max_queue_size)
        self._next_subscription_id += 1
        subscription = JsonRpcNotificationSubscription(
            bus=self,
            subscription_id=self._next_subscription_id,
            method=method,
            thread_id=thread_id,
            turn_id=turn_id,
            max_queue_size=queue_limit,
        )

        if self._closed:
            subscription._close_from_bus(error=self._terminal_error)
            return subscription

        self._subscriptions[self._next_subscription_id] = subscription
        return subscription

    def subscribe_all(
        self,
        *,
        max_queue_size: int | None = None,
    ) -> JsonRpcNotificationSubscription:
        return self.subscribe(max_queue_size=max_queue_size)

    def subscribe_method(
        self,
        method: str,
        *,
        max_queue_size: int | None = None,
    ) -> JsonRpcNotificationSubscription:
        return self.subscribe(method=method, max_queue_size=max_queue_size)

    def subscribe_thread(
        self,
        thread_id: str,
        *,
        method: str | None = None,
        max_queue_size: int | None = None,
    ) -> JsonRpcNotificationSubscription:
        return self.subscribe(
            method=method,
            thread_id=thread_id,
            max_queue_size=max_queue_size,
        )

    def subscribe_turn(
        self,
        turn_id: str,
        *,
        thread_id: str | None = None,
        method: str | None = None,
        max_queue_size: int | None = None,
    ) -> JsonRpcNotificationSubscription:
        return self.subscribe(
            method=method,
            thread_id=thread_id,
            turn_id=turn_id,
            max_queue_size=max_queue_size,
        )

    def iter_notifications(self) -> AsyncIterator[JsonRpcNotification]:
        return self.subscribe_all().iter_notifications()

    def _coerce_max_queue_size(self, max_queue_size: int | None) -> int:
        queue_limit = self._default_max_queue_size if max_queue_size is None else max_queue_size
        if queue_limit <= 0:
            raise ValueError("max_queue_size must be positive")
        return queue_limit

    def _unsubscribe(self, subscription_id: int) -> None:
        self._subscriptions.pop(subscription_id, None)


class JsonRpcServerRequestRouter:
    """Single-consumer router for raw server-initiated JSON-RPC requests."""

    def __init__(self) -> None:
        self._stream: _InboundMessageStream[JsonRpcRequest] = _InboundMessageStream()

    @property
    def is_closed(self) -> bool:
        return self._stream.is_closed

    @property
    def terminal_error(self) -> BaseException | None:
        return self._stream.terminal_error

    async def route_request(self, request: JsonRpcRequest) -> None:
        await self._stream.put(request)

    def close(self, *, error: BaseException | None = None) -> None:
        self._stream.close(error=error)

    def iter_requests(self) -> AsyncIterator[JsonRpcRequest]:
        return self._stream.iter_items()


class JsonRpcBackgroundDispatcher:
    """Own one connection reader task and route inbound frames to the right subsystem."""

    def __init__(
        self,
        *,
        transport: StdioTransport,
        requests: JsonRpcRequestRegistry,
        notifications: JsonRpcNotificationBus,
        server_requests: JsonRpcServerRequestRouter,
        on_fatal_error: FatalDispatchCallback | None = None,
    ) -> None:
        self.transport = transport
        self.requests = requests
        self.notifications = notifications
        self.server_requests = server_requests
        self._on_fatal_error = on_fatal_error
        self._start_lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()
        self._dispatch_task: asyncio.Task[None] | None = None
        self._stop_requested = False

    @property
    def dispatch_task(self) -> asyncio.Task[None] | None:
        return self._dispatch_task

    @property
    def is_running(self) -> bool:
        task = self._dispatch_task
        return task is not None and not task.done()

    async def start(self) -> None:
        async with self._start_lock:
            if self._dispatch_task is not None:
                return
            self._stop_requested = False
            self._dispatch_task = asyncio.create_task(
                self._dispatch_loop(),
                name="codex-agent-sdk.rpc-dispatch",
            )

    async def stop(self) -> None:
        async with self._stop_lock:
            self._stop_requested = True
            task = self._dispatch_task

        if task is None or task is asyncio.current_task():
            return
        await asyncio.shield(task)

    async def dispatch_envelope(self, envelope: JsonRpcEnvelope) -> None:
        if is_jsonrpc_response_envelope(envelope):
            await self._dispatch_response(cast(JsonRpcResponseEnvelope, envelope))
            return
        if is_jsonrpc_request_envelope(envelope):
            await self.server_requests.route_request(cast(JsonRpcRequest, envelope))
            return
        if is_jsonrpc_notification_envelope(envelope):
            await self.notifications.publish(cast(JsonRpcNotification, envelope))
            return
        raise UnexpectedMessageError(f"received invalid JSON-RPC envelope shape: {envelope!r}")

    async def _dispatch_loop(self) -> None:
        try:
            while True:
                envelope = await self.transport.read_stdout_envelope()
                if envelope is None:
                    if self._stop_requested:
                        return
                    raise TransportClosedError(
                        "app-server stdout reached EOF while requests were still possible",
                        stderr_tail=self.transport.stderr_tail,
                    )
                await self.dispatch_envelope(envelope)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if self._stop_requested:
                return
            await self._fail_dispatch(exc)

    async def _dispatch_response(self, envelope: JsonRpcResponseEnvelope) -> None:
        try:
            await self.requests.resolve_response(envelope)
        except LateResponseError as exc:
            _LOGGER.warning(
                "ignoring late JSON-RPC response for request_id=%r method=%s release_reason=%s",
                exc.request_id,
                exc.method,
                exc.release_reason,
            )

    async def _fail_dispatch(self, exc: BaseException) -> None:
        await self.requests.fail_all(exc, reason="failed")
        self.notifications.close(error=exc)
        self.server_requests.close(error=exc)
        if self._on_fatal_error is not None:
            await self._on_fatal_error(exc)


__all__ = [
    "DEFAULT_NOTIFICATION_SUBSCRIPTION_QUEUE_MAXSIZE",
    "JsonRpcBackgroundDispatcher",
    "JsonRpcNotificationBus",
    "JsonRpcNotificationSubscription",
    "JsonRpcRequestIdAllocator",
    "JsonRpcRequestRegistry",
    "JsonRpcServerRequestRouter",
    "PendingJsonRpcRequest",
]

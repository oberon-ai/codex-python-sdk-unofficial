"""Request correlation helpers for the JSON-RPC connection layer."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal, TypeAlias

from ..errors import (
    DuplicateRequestIdError,
    DuplicateResponseError,
    LateResponseError,
    UnknownResponseIdError,
    map_jsonrpc_error,
)
from .jsonrpc import JsonRpcErrorResponse, JsonRpcId, JsonRpcResponseEnvelope

FinalizedRequestStatus: TypeAlias = Literal[
    "completed",
    "cancelled",
    "timed_out",
    "closed",
    "failed",
]


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


__all__ = [
    "JsonRpcRequestIdAllocator",
    "JsonRpcRequestRegistry",
    "PendingJsonRpcRequest",
]

"""Synchronous wrapper over the async high-level Codex SDK client."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from concurrent.futures import CancelledError as ConcurrentCancelledError
from concurrent.futures import Future as ConcurrentFuture
from queue import Queue
from typing import TypeVar, cast

from .approvals import ApprovalDecision, ApprovalRequest
from .client import CodexSDKClient, TurnInputLike
from .events import TurnEvent
from .options import AppServerConfig, CodexOptions
from .results import TurnHandle, TurnResult

SyncApprovalHandlerResult = ApprovalDecision | None | Awaitable[ApprovalDecision | None]
SyncApprovalHandler = Callable[[ApprovalRequest], SyncApprovalHandlerResult]

_SYNC_ITERATION_DONE = object()
ResultT = TypeVar("ResultT")


class _SyncIterationFailure:
    def __init__(self, error: BaseException) -> None:
        self.error = error


class _AsyncLoopThreadRunner:
    def __init__(self) -> None:
        self._ready = threading.Event()
        self._stopped = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(
            target=self._thread_main,
            daemon=True,
            name="codex-agent-sdk.sync-client",
        )
        self._thread.start()
        self._ready.wait()

    def run(self, coroutine: Awaitable[ResultT]) -> ResultT:
        if self._stopped or self._loop is None:
            raise RuntimeError("sync client runner is already closed")
        future = asyncio.run_coroutine_threadsafe(_coerce_coroutine(coroutine), self._loop)
        return future.result()

    def submit(self, coroutine: Awaitable[object]) -> ConcurrentFuture[object]:
        if self._stopped or self._loop is None:
            raise RuntimeError("sync client runner is already closed")
        return asyncio.run_coroutine_threadsafe(_coerce_coroutine(coroutine), self._loop)

    def stop(self) -> None:
        if self._stopped:
            return
        loop = self._loop
        self._stopped = True
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        self._thread.join()

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()

        try:
            loop.run_forever()
        finally:
            pending = tuple(asyncio.all_tasks(loop))
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()


class SyncTurnHandle(Iterator[TurnEvent]):
    """Synchronous wrapper around the async ``TurnHandle`` abstraction."""

    def __init__(
        self,
        *,
        runner: _AsyncLoopThreadRunner,
        handle: TurnHandle,
    ) -> None:
        self.thread_id = handle.thread_id
        self.turn_id = handle.turn_id
        self._runner = runner
        self._handle = handle
        self._iterator: Iterator[TurnEvent] | None = None

    def __iter__(self) -> SyncTurnHandle:
        return self

    def __next__(self) -> TurnEvent:
        if self._iterator is None:
            iterator = self._runner.run(_open_turn_iterator(self._handle))
            self._iterator = _bridge_async_iterator(self._runner, iterator)
        return next(self._iterator)

    def wait(self) -> TurnResult:
        """Wait for the terminal result of the turn."""

        return self._runner.run(self._handle.wait())

    def steer(self, prompt: str | list[object]) -> str:
        """Append steering input to the active turn."""

        return self._runner.run(self._handle.steer(prompt))

    def interrupt(self) -> None:
        """Request interruption for the active turn."""

        self._runner.run(self._handle.interrupt())


class SyncCodexSDKClient:
    """Synchronous wrapper over ``CodexSDKClient`` backed by a private event loop."""

    def __init__(
        self,
        options: CodexOptions | None = None,
        app_server: AppServerConfig | None = None,
        approval_handler: SyncApprovalHandler | None = None,
    ) -> None:
        self.options = options or CodexOptions()
        self.app_server = app_server or AppServerConfig()
        self.approval_handler = approval_handler
        self._closed = False
        self._runner = _AsyncLoopThreadRunner()
        try:
            self._async_client = self._runner.run(
                _build_async_client(
                    options=self.options,
                    app_server=self.app_server,
                    approval_handler=self.approval_handler,
                )
            )
        except BaseException:
            self._runner.stop()
            raise

    def __enter__(self) -> SyncCodexSDKClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        self.close()

    @property
    def active_turn_id(self) -> str | None:
        return self._runner.run(_read_client_attr(self._async_client, "active_turn_id"))

    @property
    def thread_id(self) -> str | None:
        return self._runner.run(_read_client_attr(self._async_client, "thread_id"))

    @property
    def thread_status(self) -> str | None:
        return self._runner.run(_read_client_attr(self._async_client, "thread_status"))

    def close(self) -> None:
        """Close the underlying async client and its private event loop thread."""

        if self._closed:
            return
        self._closed = True
        try:
            self._runner.run(self._async_client.close())
        finally:
            self._runner.stop()

    def start_thread(
        self,
        *,
        options: CodexOptions | None = None,
        ephemeral: bool = False,
    ) -> str:
        """Start a new thread and make it the active thread."""

        return self._runner.run(
            self._async_client.start_thread(options=options, ephemeral=ephemeral)
        )

    def resume_thread(
        self,
        thread_id: str,
        *,
        options: CodexOptions | None = None,
    ) -> str:
        """Resume an existing thread and make it the active thread."""

        return self._runner.run(self._async_client.resume_thread(thread_id, options=options))

    def fork_thread(
        self,
        thread_id: str | None = None,
        *,
        options: CodexOptions | None = None,
        ephemeral: bool = False,
    ) -> str:
        """Fork a thread and make the new branch the active thread."""

        return self._runner.run(
            self._async_client.fork_thread(
                thread_id,
                options=options,
                ephemeral=ephemeral,
            )
        )

    def query(
        self,
        prompt: TurnInputLike,
        *,
        options: CodexOptions | None = None,
        output_schema: dict[str, object] | None = None,
    ) -> SyncTurnHandle:
        """Start a turn on the active thread and return its synchronous handle."""

        handle = self._runner.run(
            self._async_client.query(
                prompt,
                options=options,
                output_schema=output_schema,
            )
        )
        return SyncTurnHandle(runner=self._runner, handle=handle)

    def steer(
        self,
        prompt: TurnInputLike,
        *,
        expected_turn_id: str | None = None,
    ) -> str:
        """Append steering input to the active turn."""

        return self._runner.run(
            self._async_client.steer(
                prompt,
                expected_turn_id=expected_turn_id,
            )
        )

    def interrupt(
        self,
        *,
        turn_id: str | None = None,
    ) -> None:
        """Interrupt the active turn or a specified turn."""

        self._runner.run(self._async_client.interrupt(turn_id=turn_id))

    def respond_approval_request(
        self,
        request: ApprovalRequest | str | int | None,
        decision: ApprovalDecision,
    ) -> None:
        """Send a typed approval decision for one pending approval request."""

        self._runner.run(self._async_client.respond_approval_request(request, decision))

    def receive_turn_events(
        self,
        *,
        turn_id: str | None = None,
    ) -> Iterator[TurnEvent]:
        """Return a synchronous event iterator for one turn."""

        iterator = self._runner.run(_open_client_events(self._async_client, turn_id=turn_id))
        return _bridge_async_iterator(self._runner, iterator)

    def receive_response(
        self,
        *,
        turn_id: str | None = None,
    ) -> Iterator[TurnEvent]:
        """Compatibility alias for ``receive_turn_events()``."""

        return self.receive_turn_events(turn_id=turn_id)


def _bridge_async_iterator(
    runner: _AsyncLoopThreadRunner,
    iterator: AsyncIterator[TurnEvent],
) -> Iterator[TurnEvent]:
    queue: Queue[object] = Queue()
    task = runner.submit(_pump_async_iterator(iterator, queue))

    def _generator() -> Iterator[TurnEvent]:
        try:
            while True:
                item = queue.get()
                if item is _SYNC_ITERATION_DONE:
                    return
                if isinstance(item, _SyncIterationFailure):
                    raise item.error
                yield cast(TurnEvent, item)
        finally:
            if not task.done():
                task.cancel()
            try:
                task.result(timeout=1)
            except (ConcurrentCancelledError, TimeoutError):
                pass

    return _generator()


async def _pump_async_iterator(
    iterator: AsyncIterator[TurnEvent],
    queue: Queue[object],
) -> None:
    try:
        async for item in iterator:
            queue.put(item)
    except BaseException as exc:
        queue.put(_SyncIterationFailure(exc))
    else:
        queue.put(_SYNC_ITERATION_DONE)


async def _build_async_client(
    *,
    options: CodexOptions,
    app_server: AppServerConfig,
    approval_handler: SyncApprovalHandler | None,
) -> CodexSDKClient:
    return CodexSDKClient(
        options=options,
        app_server=app_server,
        approval_handler=_wrap_approval_handler(approval_handler),
    )


async def _open_client_events(
    client: CodexSDKClient,
    *,
    turn_id: str | None,
) -> AsyncIterator[TurnEvent]:
    return client.receive_turn_events(turn_id=turn_id)


async def _open_turn_iterator(handle: TurnHandle) -> AsyncIterator[TurnEvent]:
    return handle


async def _read_client_attr(client: CodexSDKClient, attr_name: str) -> str | None:
    return cast(str | None, getattr(client, attr_name))


def _wrap_approval_handler(
    handler: SyncApprovalHandler | None,
) -> Callable[[ApprovalRequest], Awaitable[ApprovalDecision | None]] | None:
    if handler is None:
        return None

    async def _wrapped(request: ApprovalRequest) -> ApprovalDecision | None:
        result = handler(request)
        if inspect.isawaitable(result):
            return await result
        return result

    return _wrapped


async def _coerce_coroutine(awaitable: Awaitable[ResultT]) -> ResultT:
    return await awaitable


__all__ = [
    "SyncCodexSDKClient",
    "SyncTurnHandle",
]

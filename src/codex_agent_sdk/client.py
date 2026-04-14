"""Public client entry points for the Codex SDK.

``AppServerClient`` is the low-level JSON-RPC escape hatch. ``CodexSDKClient``
is the stateful high-level thread client. Their concrete behavior lands in
later tasks, but the public names live here from the start.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from .approvals import ApprovalHandler
from .errors import (
    AlreadyInitializedError,
    NotInitializedError,
    RequestTimeoutError,
    StartupTimeoutError,
)
from .events import TurnEvent
from .options import AppServerConfig, CodexOptions
from .results import TurnHandle
from .rpc.connection import JsonRpcConnection
from .rpc.jsonrpc import JsonRpcNotification, JsonRpcRequest
from .rpc.router import JsonRpcNotificationSubscription
from .transport import StdioTransport


class AppServerClient:
    """Low-level native-async client for ``codex app-server`` over stdio."""

    def __init__(self, config: AppServerConfig | None = None) -> None:
        self.config = config or AppServerConfig()
        self._connection = JsonRpcConnection(StdioTransport(self.config))
        self._initialize_lock = asyncio.Lock()
        self._initialized = False
        self._initialize_result: object | None = None

    async def __aenter__(self) -> AppServerClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying app-server connection."""

        await self._connection.close()

    async def initialize(self) -> object:
        """Perform the required initialize then initialized handshake."""

        if self._initialized:
            raise AlreadyInitializedError(-32002, "Already initialized", method="initialize")

        async with self._initialize_lock:
            if self._initialized:
                raise AlreadyInitializedError(-32002, "Already initialized", method="initialize")

            deadline = asyncio.get_running_loop().time() + self.config.startup_timeout
            try:
                await self._connection.start(
                    startup_timeout=_remaining_startup_timeout(
                        deadline=deadline,
                        config=self.config,
                        stderr_tail=self._connection.transport.stderr_tail,
                    )
                )
                initialize_result = await self._connection.request(
                    "initialize",
                    _build_initialize_params(self.config),
                    timeout=_remaining_startup_timeout(
                        deadline=deadline,
                        config=self.config,
                        stderr_tail=self._connection.transport.stderr_tail,
                    ),
                )
                await self._connection.notify("initialized", {})
            except RequestTimeoutError as exc:
                await self._connection.close()
                raise StartupTimeoutError(
                    timeout_seconds=self.config.startup_timeout,
                    stderr_tail=self._connection.transport.stderr_tail,
                    command=self._connection.transport.command,
                    cwd=self._connection.transport.cwd,
                ) from exc
            except asyncio.CancelledError:
                raise
            except BaseException:
                await self._connection.close()
                raise

            self._initialized = True
            self._initialize_result = initialize_result
            return initialize_result

    async def request(
        self,
        method: str,
        params: object | None = None,
        *,
        timeout: float | None = None,
    ) -> object:
        """Send a raw JSON-RPC request over the app-server connection."""

        self._require_initialized()
        return await self._connection.request(method, params, timeout=timeout)

    async def notify(self, method: str, params: object | None = None) -> None:
        """Send a raw JSON-RPC notification over the app-server connection."""

        self._require_initialized()
        await self._connection.notify(method, params)

    def iter_notifications(self) -> AsyncIterator[JsonRpcNotification]:
        """Iterate raw JSON-RPC notifications from the server."""

        return self._connection.iter_notifications()

    def subscribe_notifications(
        self,
        *,
        method: str | None = None,
        thread_id: str | None = None,
        turn_id: str | None = None,
        max_queue_size: int | None = None,
    ) -> JsonRpcNotificationSubscription:
        """Subscribe to all notifications or one filtered subset."""

        return self._connection.subscribe_notifications(
            method=method,
            thread_id=thread_id,
            turn_id=turn_id,
            max_queue_size=max_queue_size,
        )

    def subscribe_thread_notifications(
        self,
        thread_id: str,
        *,
        method: str | None = None,
        max_queue_size: int | None = None,
    ) -> JsonRpcNotificationSubscription:
        """Subscribe to notifications scoped to one thread id."""

        return self._connection.subscribe_thread_notifications(
            thread_id,
            method=method,
            max_queue_size=max_queue_size,
        )

    def subscribe_turn_notifications(
        self,
        turn_id: str,
        *,
        thread_id: str | None = None,
        method: str | None = None,
        max_queue_size: int | None = None,
    ) -> JsonRpcNotificationSubscription:
        """Subscribe to notifications scoped to one turn id."""

        return self._connection.subscribe_turn_notifications(
            turn_id,
            thread_id=thread_id,
            method=method,
            max_queue_size=max_queue_size,
        )

    def iter_server_requests(self) -> AsyncIterator[JsonRpcRequest]:
        """Iterate raw server-initiated JSON-RPC requests."""

        return self._connection.iter_server_requests()

    async def thread_start(self, **params: Any) -> object:
        """Start a new app-server thread."""

        return await self.request("thread/start", params or None)

    async def thread_resume(self, **params: Any) -> object:
        """Resume an existing app-server thread."""

        return await self.request("thread/resume", params or None)

    async def thread_fork(self, **params: Any) -> object:
        """Fork an app-server thread."""

        return await self.request("thread/fork", params or None)

    async def turn_start(self, **params: Any) -> object:
        """Start a turn on the active thread."""

        return await self.request("turn/start", params or None)

    async def turn_steer(self, **params: Any) -> object:
        """Steer an in-flight turn."""

        return await self.request("turn/steer", params or None)

    async def turn_interrupt(self, **params: Any) -> None:
        """Interrupt an in-flight turn."""

        await self.request("turn/interrupt", params or None)

    def _require_initialized(self) -> None:
        if self._initialized:
            return
        raise NotInitializedError(-32002, "Not initialized")


class CodexSDKClient:
    """High-level stateful client for thread and turn workflows."""

    def __init__(
        self,
        options: CodexOptions | None = None,
        app_server: AppServerConfig | None = None,
        approval_handler: ApprovalHandler | None = None,
    ) -> None:
        self.options = options or CodexOptions()
        self.app_server = app_server or AppServerConfig()
        self.approval_handler = approval_handler
        self.thread_id: str | None = None
        self.active_turn_id: str | None = None
        self.thread_status: str | None = None

    async def __aenter__(self) -> CodexSDKClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying app-server connection for this client."""

    async def start_thread(
        self,
        *,
        options: CodexOptions | None = None,
        ephemeral: bool = False,
    ) -> str:
        """Start a new thread and make it the active thread."""

        raise NotImplementedError("Thread start flows are not implemented yet.")

    async def resume_thread(
        self,
        thread_id: str,
        *,
        options: CodexOptions | None = None,
    ) -> str:
        """Resume an existing thread and make it the active thread."""

        raise NotImplementedError("Thread resume flows are not implemented yet.")

    async def fork_thread(
        self,
        thread_id: str | None = None,
        *,
        options: CodexOptions | None = None,
        ephemeral: bool = False,
    ) -> str:
        """Fork a thread and make the new branch the active thread."""

        raise NotImplementedError("Thread fork flows are not implemented yet.")

    async def query(
        self,
        prompt: str | list[object],
        *,
        options: CodexOptions | None = None,
        output_schema: dict[str, object] | None = None,
    ) -> TurnHandle:
        """Start a turn on the active thread and return its handle."""

        raise NotImplementedError("High-level turn queries are not implemented yet.")

    async def steer(
        self,
        prompt: str | list[object],
        *,
        expected_turn_id: str | None = None,
    ) -> str:
        """Append steering input to the active turn."""

        raise NotImplementedError("Turn steering is not implemented yet.")

    async def interrupt(
        self,
        *,
        turn_id: str | None = None,
    ) -> None:
        """Interrupt the active turn or a specified turn."""

        raise NotImplementedError("Turn interruption is not implemented yet.")

    def receive_turn_events(
        self,
        *,
        turn_id: str | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """Return the canonical event stream for a turn."""

        raise NotImplementedError("Turn event streaming is not implemented yet.")

    def receive_response(
        self,
        *,
        turn_id: str | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """Compatibility alias for ``receive_turn_events()``."""

        return self.receive_turn_events(turn_id=turn_id)


__all__ = [
    "AppServerClient",
    "CodexSDKClient",
]


def _build_initialize_params(config: AppServerConfig) -> dict[str, object]:
    params: dict[str, object] = {
        "clientInfo": {
            "name": config.client_name,
            "title": config.client_title,
            "version": config.client_version,
        },
        "protocolVersion": 2,
    }
    if config.experimental_api:
        params["experimentalApi"] = True
    return params


def _remaining_startup_timeout(
    *,
    deadline: float,
    config: AppServerConfig,
    stderr_tail: str | None,
) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining > 0:
        return remaining
    raise StartupTimeoutError(
        timeout_seconds=config.startup_timeout,
        stderr_tail=stderr_tail,
        command=StdioTransport.build_command(config),
        cwd=config.cwd,
    )

"""Public client entry points for the Codex SDK.

``AppServerClient`` is the low-level JSON-RPC escape hatch. ``CodexSDKClient``
is the stateful high-level thread client. Their concrete behavior lands in
later tasks, but the public names live here from the start.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Any, TypeVar, overload

from .approvals import ApprovalHandler
from .errors import (
    AlreadyInitializedError,
    ClientStateError,
    NotInitializedError,
    RequestTimeoutError,
    StartupTimeoutError,
    TransportClosedError,
)
from .events import TurnEvent
from .generated.stable import (
    ApprovalsReviewer,
    AskForApproval,
    ClientInfo,
    InitializeCapabilities,
    InitializeParams,
    Personality,
    SandboxMode,
    ServiceTier,
    ThreadArchiveParams,
    ThreadArchiveResponse,
    ThreadForkParams,
    ThreadForkResponse,
    ThreadListParams,
    ThreadListResponse,
    ThreadReadParams,
    ThreadReadResponse,
    ThreadResumeParams,
    ThreadResumeResponse,
    ThreadSetNameParams,
    ThreadSetNameResponse,
    ThreadSortKey,
    ThreadSourceKind,
    ThreadStartParams,
    ThreadStartResponse,
    ThreadUnarchiveParams,
    ThreadUnarchiveResponse,
    TurnInterruptParams,
    TurnInterruptResponse,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerParams,
    TurnSteerResponse,
)
from .options import AppServerConfig, CodexOptions
from .protocol.initialize import InitializeResult, parse_initialize_result
from .protocol.pydantic import dump_wire_value, validate_response_payload
from .results import TurnHandle
from .rpc.connection import JsonRpcConnection
from .rpc.jsonrpc import JsonRpcNotification, JsonRpcRequest
from .rpc.router import JsonRpcNotificationSubscription, JsonRpcServerRequestHandler
from .transport import StdioTransport


class _HandshakeState(StrEnum):
    CREATED = "created"
    INITIALIZING = "initializing"
    INITIALIZED = "initialized"
    FAILED = "failed"
    CLOSED = "closed"


ResponseModelT = TypeVar("ResponseModelT")


class AppServerClient:
    """Low-level native-async client for ``codex app-server`` over stdio."""

    def __init__(self, config: AppServerConfig | None = None) -> None:
        self.config = config or AppServerConfig()
        self._connection = JsonRpcConnection(StdioTransport(self.config))
        self._initialize_lock = asyncio.Lock()
        self._initialize_task: asyncio.Task[InitializeResult] | None = None
        self._handshake_state = _HandshakeState.CREATED
        self._initialize_result: InitializeResult | None = None

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
        if self._handshake_state is not _HandshakeState.FAILED:
            self._handshake_state = _HandshakeState.CLOSED

    @property
    def initialize_result(self) -> InitializeResult | None:
        """Return the cached initialize result after a successful handshake."""

        return self._initialize_result

    @property
    def is_initialized(self) -> bool:
        """Return ``True`` after ``initialize()`` has completed successfully."""

        return self._handshake_state is _HandshakeState.INITIALIZED

    async def initialize(self) -> InitializeResult:
        """Perform the required initialize then initialized handshake."""

        if self._handshake_state is _HandshakeState.INITIALIZED:
            raise AlreadyInitializedError(-32002, "Already initialized", method="initialize")

        if self._initialize_task is not None:
            return await asyncio.shield(self._initialize_task)

        self._raise_if_handshake_unavailable()

        async with self._initialize_lock:
            if self._initialize_result is not None:
                raise AlreadyInitializedError(-32002, "Already initialized", method="initialize")
            if self._initialize_task is None:
                self._handshake_state = _HandshakeState.INITIALIZING
                self._initialize_task = asyncio.create_task(
                    self._run_initialize_handshake(),
                    name="codex-agent-sdk.initialize",
                )

            task = self._initialize_task

        assert task is not None
        return await asyncio.shield(task)

    @overload
    async def request(
        self,
        method: str,
        params: object | None = None,
        *,
        response_model: None = None,
        timeout: float | None = None,
    ) -> object: ...

    @overload
    async def request(
        self,
        method: str,
        params: object | None = None,
        *,
        response_model: type[ResponseModelT],
        timeout: float | None = None,
    ) -> ResponseModelT: ...

    async def request(
        self,
        method: str,
        params: object | None = None,
        *,
        response_model: type[ResponseModelT] | None = None,
        timeout: float | None = None,
    ) -> object | ResponseModelT:
        """Send one JSON-RPC request and optionally validate its typed result."""

        self._guard_outbound_request_method(method)
        raw_result = await self._connection.request(
            method,
            dump_wire_value(params),
            timeout=timeout,
        )
        if response_model is None:
            return raw_result
        return validate_response_payload(
            raw_result,
            method=method,
            response_model=response_model,
        )

    async def notify(self, method: str, params: object | None = None) -> None:
        """Send a raw JSON-RPC notification over the app-server connection."""

        self._guard_outbound_notification_method(method)
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

    async def respond_server_request(
        self,
        request_id: str | int | None,
        result: object | None = None,
    ) -> None:
        """Send a success response for one pending server-initiated request."""

        self._require_initialized()
        await self._connection.respond_server_request(request_id, result)

    async def reject_server_request(
        self,
        request_id: str | int | None,
        code: int,
        message: str,
        *,
        data: object | None = None,
    ) -> None:
        """Send an error response for one pending server-initiated request."""

        self._require_initialized()
        await self._connection.reject_server_request(
            request_id,
            code,
            message,
            data=data,
        )

    def register_server_request_handler(
        self,
        method: str,
        handler: JsonRpcServerRequestHandler,
    ) -> None:
        """Register or replace one async handler for a server-request method."""

        self._connection.register_server_request_handler(method, handler)

    def remove_server_request_handler(self, method: str) -> None:
        """Remove one previously registered server-request handler if present."""

        self._connection.remove_server_request_handler(method)

    async def thread_start(
        self,
        *,
        approval_policy: AskForApproval | None = None,
        approvals_reviewer: ApprovalsReviewer | None = None,
        base_instructions: str | None = None,
        config: dict[str, Any] | None = None,
        cwd: str | None = None,
        developer_instructions: str | None = None,
        ephemeral: bool | None = None,
        model: str | None = None,
        model_provider: str | None = None,
        personality: Personality | None = None,
        sandbox: SandboxMode | None = None,
        service_name: str | None = None,
        service_tier: ServiceTier | None = None,
    ) -> ThreadStartResponse:
        """Start a new app-server thread."""

        return await self.request(
            "thread/start",
            ThreadStartParams(
                approval_policy=approval_policy,
                approvals_reviewer=approvals_reviewer,
                base_instructions=base_instructions,
                config=config,
                cwd=cwd,
                developer_instructions=developer_instructions,
                ephemeral=ephemeral,
                model=model,
                model_provider=model_provider,
                personality=personality,
                sandbox=sandbox,
                service_name=service_name,
                service_tier=service_tier,
            ),
            response_model=ThreadStartResponse,
        )

    async def thread_resume(
        self,
        *,
        thread_id: str,
        approval_policy: AskForApproval | None = None,
        approvals_reviewer: ApprovalsReviewer | None = None,
        base_instructions: str | None = None,
        config: dict[str, Any] | None = None,
        cwd: str | None = None,
        developer_instructions: str | None = None,
        model: str | None = None,
        model_provider: str | None = None,
        personality: Personality | None = None,
        sandbox: SandboxMode | None = None,
        service_tier: ServiceTier | None = None,
    ) -> ThreadResumeResponse:
        """Resume an existing app-server thread."""

        return await self.request(
            "thread/resume",
            ThreadResumeParams(
                thread_id=thread_id,
                approval_policy=approval_policy,
                approvals_reviewer=approvals_reviewer,
                base_instructions=base_instructions,
                config=config,
                cwd=cwd,
                developer_instructions=developer_instructions,
                model=model,
                model_provider=model_provider,
                personality=personality,
                sandbox=sandbox,
                service_tier=service_tier,
            ),
            response_model=ThreadResumeResponse,
        )

    async def thread_fork(
        self,
        *,
        thread_id: str,
        approval_policy: AskForApproval | None = None,
        approvals_reviewer: ApprovalsReviewer | None = None,
        base_instructions: str | None = None,
        config: dict[str, Any] | None = None,
        cwd: str | None = None,
        developer_instructions: str | None = None,
        ephemeral: bool | None = None,
        model: str | None = None,
        model_provider: str | None = None,
        sandbox: SandboxMode | None = None,
        service_tier: ServiceTier | None = None,
    ) -> ThreadForkResponse:
        """Fork an app-server thread."""

        return await self.request(
            "thread/fork",
            ThreadForkParams(
                thread_id=thread_id,
                approval_policy=approval_policy,
                approvals_reviewer=approvals_reviewer,
                base_instructions=base_instructions,
                config=config,
                cwd=cwd,
                developer_instructions=developer_instructions,
                ephemeral=ephemeral,
                model=model,
                model_provider=model_provider,
                sandbox=sandbox,
                service_tier=service_tier,
            ),
            response_model=ThreadForkResponse,
        )

    async def thread_list(
        self,
        *,
        archived: bool | None = None,
        cursor: str | None = None,
        cwd: str | None = None,
        limit: int | None = None,
        model_providers: list[str] | None = None,
        search_term: str | None = None,
        sort_key: ThreadSortKey | None = None,
        source_kinds: list[ThreadSourceKind] | None = None,
    ) -> ThreadListResponse:
        """List app-server threads using the server's native filters and pagination."""

        return await self.request(
            "thread/list",
            ThreadListParams(
                archived=archived,
                cursor=cursor,
                cwd=cwd,
                limit=limit,
                model_providers=model_providers,
                search_term=search_term,
                sort_key=sort_key,
                source_kinds=source_kinds,
            ),
            response_model=ThreadListResponse,
        )

    async def thread_read(
        self,
        *,
        thread_id: str,
        include_turns: bool | None = None,
    ) -> ThreadReadResponse:
        """Read one app-server thread, optionally asking the server to include turns."""

        return await self.request(
            "thread/read",
            ThreadReadParams(
                thread_id=thread_id,
                include_turns=include_turns,
            ),
            response_model=ThreadReadResponse,
        )

    async def thread_archive(
        self,
        *,
        thread_id: str,
    ) -> ThreadArchiveResponse:
        """Archive one app-server thread."""

        return await self.request(
            "thread/archive",
            ThreadArchiveParams(thread_id=thread_id),
            response_model=ThreadArchiveResponse,
        )

    async def thread_unarchive(
        self,
        *,
        thread_id: str,
    ) -> ThreadUnarchiveResponse:
        """Unarchive one app-server thread."""

        return await self.request(
            "thread/unarchive",
            ThreadUnarchiveParams(thread_id=thread_id),
            response_model=ThreadUnarchiveResponse,
        )

    async def thread_set_name(
        self,
        *,
        thread_id: str,
        name: str,
    ) -> ThreadSetNameResponse:
        """Set the server-side name for one app-server thread."""

        return await self.request(
            "thread/name/set",
            ThreadSetNameParams(thread_id=thread_id, name=name),
            response_model=ThreadSetNameResponse,
        )

    async def turn_start(self, **params: Any) -> TurnStartResponse:
        """Start a turn on the active thread."""

        return await self.request(
            "turn/start",
            TurnStartParams(**params),
            response_model=TurnStartResponse,
        )

    async def turn_steer(self, **params: Any) -> TurnSteerResponse:
        """Steer an in-flight turn."""

        return await self.request(
            "turn/steer",
            TurnSteerParams(**params),
            response_model=TurnSteerResponse,
        )

    async def turn_interrupt(self, **params: Any) -> TurnInterruptResponse:
        """Interrupt an in-flight turn."""

        return await self.request(
            "turn/interrupt",
            TurnInterruptParams(**params),
            response_model=TurnInterruptResponse,
        )

    async def _run_initialize_handshake(self) -> InitializeResult:
        deadline = asyncio.get_running_loop().time() + self.config.startup_timeout
        try:
            await self._connection.start(
                startup_timeout=_remaining_startup_timeout(
                    deadline=deadline,
                    config=self.config,
                    stderr_tail=self._connection.transport.stderr_tail,
                )
            )
            raw_result = await self._connection.request(
                "initialize",
                _build_initialize_params(self.config),
                timeout=_remaining_startup_timeout(
                    deadline=deadline,
                    config=self.config,
                    stderr_tail=self._connection.transport.stderr_tail,
                ),
            )
            initialize_result = parse_initialize_result(raw_result)
            await self._connection.notify("initialized", {})
        except RequestTimeoutError as exc:
            self._handshake_state = _HandshakeState.FAILED
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
            self._handshake_state = _HandshakeState.FAILED
            await self._connection.close()
            raise
        finally:
            self._initialize_task = None

        self._initialize_result = initialize_result
        self._handshake_state = _HandshakeState.INITIALIZED
        return initialize_result

    def _guard_outbound_request_method(self, method: str) -> None:
        if method == "initialize":
            if self._handshake_state is _HandshakeState.INITIALIZED:
                raise AlreadyInitializedError(-32002, "Already initialized", method="initialize")
            if self._handshake_state is _HandshakeState.INITIALIZING:
                raise ClientStateError("initialize() is already in progress")
            self._raise_if_handshake_unavailable()
            raise ClientStateError(
                "use AppServerClient.initialize() for the required initialize handshake"
            )

        if method == "initialized":
            raise ClientStateError(
                "'initialized' is reserved for the initialize handshake notification"
            )

        self._require_initialized(method=method)

    def _guard_outbound_notification_method(self, method: str) -> None:
        if method == "initialized":
            raise ClientStateError(
                "'initialized' is sent automatically after a successful initialize() call"
            )
        if method == "initialize":
            raise ClientStateError(
                "'initialize' is a JSON-RPC request and must be sent via initialize()"
            )
        self._require_initialized(method=method)

    def _require_initialized(self, *, method: str | None = None) -> None:
        if self._handshake_state is _HandshakeState.INITIALIZED:
            return
        self._raise_if_handshake_unavailable()
        raise NotInitializedError(-32002, "Not initialized", method=method)

    def _raise_if_handshake_unavailable(self) -> None:
        if self._handshake_state is _HandshakeState.FAILED:
            error = self._connection.terminal_error
            if error is not None:
                raise error
        if self._handshake_state is _HandshakeState.CLOSED:
            raise TransportClosedError(
                "app-server connection is already closed",
                stderr_tail=self._connection.transport.stderr_tail,
            )


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
    "InitializeResult",
]


def _build_initialize_params(config: AppServerConfig) -> dict[str, object]:
    client_info = ClientInfo(
        name=config.client_name,
        title=config.client_title,
        version=config.client_version,
    )

    capabilities: InitializeCapabilities | None = None
    if config.experimental_api and config.opt_out_notification_methods:
        capabilities = InitializeCapabilities(
            experimental_api=True,
            opt_out_notification_methods=list(config.opt_out_notification_methods),
        )
    elif config.experimental_api:
        capabilities = InitializeCapabilities(experimental_api=True)
    elif config.opt_out_notification_methods:
        capabilities = InitializeCapabilities(
            opt_out_notification_methods=list(config.opt_out_notification_methods),
        )

    if capabilities is None:
        params = InitializeParams(client_info=client_info)
    else:
        params = InitializeParams(
            client_info=client_info,
            capabilities=capabilities,
        )
    return params.model_dump()


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

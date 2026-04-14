"""Public client entry points for the Codex SDK.

``AppServerClient`` is the low-level JSON-RPC escape hatch. ``CodexSDKClient``
is the stateful high-level thread client. Their concrete behavior lands in
later tasks, but the public names live here from the start.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from enum import StrEnum
from typing import Any, TypeAlias, TypeVar, overload

from .approvals import (
    ApprovalDecision,
    ApprovalHandler,
    ApprovalRequest,
    adapt_approval_request,
)
from .errors import (
    AlreadyInitializedError,
    ApprovalCallbackError,
    ApprovalRequestExpiredError,
    ClientStateError,
    InvalidApprovalDecisionError,
    NotInitializedError,
    RequestTimeoutError,
    ServerRequestAlreadyRespondedError,
    StartupTimeoutError,
    TransportClosedError,
    UnknownServerRequestIdError,
)
from .events import TurnCompletedEvent, TurnEvent
from .generated.stable import (
    ApprovalsReviewer,
    AskForApproval,
    ClientInfo,
    InitializeCapabilities,
    InitializeParams,
    Personality,
    ReasoningEffort,
    ReasoningSummary,
    SandboxMode,
    SandboxPolicy,
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
    ThreadTokenUsageUpdatedNotification,
    ThreadUnarchiveParams,
    ThreadUnarchiveResponse,
    TurnCompletedNotification,
    TurnInterruptParams,
    TurnInterruptResponse,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerParams,
    TurnSteerResponse,
    UserInput,
    UserInput1,
    UserInput2,
    UserInput3,
    UserInput4,
    UserInput5,
)
from .options import AppServerConfig, CodexOptions
from .protocol.adapters import (
    TurnEventAdapterState,
    adapt_turn_notification,
    adapt_turn_server_request,
)
from .protocol.initialize import InitializeResult, parse_initialize_result
from .protocol.pydantic import dump_wire_value, validate_response_payload
from .protocol.registries import TypedServerNotification, parse_server_notification
from .results import TurnCompletion, TurnHandle
from .rpc.connection import JsonRpcConnection
from .rpc.jsonrpc import JsonRpcNotification, JsonRpcRequest
from .rpc.router import (
    SERVER_REQUEST_NOT_HANDLED,
    JsonRpcNotificationSubscription,
    JsonRpcServerRequestHandler,
    JsonRpcServerRequestSubscription,
)
from .transport import StdioTransport

_LOGGER = logging.getLogger(__name__)


class _HandshakeState(StrEnum):
    CREATED = "created"
    INITIALIZING = "initializing"
    INITIALIZED = "initialized"
    FAILED = "failed"
    CLOSED = "closed"


ResponseModelT = TypeVar("ResponseModelT")
TurnInputItemLike: TypeAlias = (
    UserInput
    | UserInput1
    | UserInput2
    | UserInput3
    | UserInput4
    | UserInput5
    | Mapping[str, object]
)
TurnInputLike: TypeAlias = str | TurnInputItemLike | Sequence[TurnInputItemLike]


class AppServerClient:
    """Low-level native-async client for ``codex app-server`` over stdio."""

    def __init__(self, config: AppServerConfig | None = None) -> None:
        self.config = config or AppServerConfig()
        self._connection = JsonRpcConnection(StdioTransport(self.config))
        self._initialize_lock = asyncio.Lock()
        self._initialize_task: asyncio.Task[InitializeResult] | None = None
        self._handshake_state = _HandshakeState.CREATED
        self._initialize_result: InitializeResult | None = None
        self._approval_handler: ApprovalHandler | None = None

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

    def subscribe_server_requests(
        self,
        *,
        method: str | None = None,
        thread_id: str | None = None,
        turn_id: str | None = None,
    ) -> JsonRpcServerRequestSubscription:
        """Subscribe to all unhandled server requests or one filtered subset."""

        return self._connection.subscribe_server_requests(
            method=method,
            thread_id=thread_id,
            turn_id=turn_id,
        )

    def subscribe_thread_server_requests(
        self,
        thread_id: str,
        *,
        method: str | None = None,
    ) -> JsonRpcServerRequestSubscription:
        """Subscribe to unhandled server requests scoped to one thread id."""

        return self._connection.subscribe_thread_server_requests(thread_id, method=method)

    def subscribe_turn_server_requests(
        self,
        turn_id: str,
        *,
        thread_id: str | None = None,
        method: str | None = None,
    ) -> JsonRpcServerRequestSubscription:
        """Subscribe to unhandled server requests scoped to one turn id."""

        return self._connection.subscribe_turn_server_requests(
            turn_id,
            thread_id=thread_id,
            method=method,
        )

    def iter_approval_requests(
        self,
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
    ) -> AsyncIterator[ApprovalRequest]:
        """Iterate typed approval requests that have not been auto-handled."""

        self._require_initialized(method="approval request stream")
        return _iter_approval_requests(self, thread_id=thread_id, turn_id=turn_id)

    async def respond_server_request(
        self,
        request_id: str | int | None,
        result: object | None = None,
    ) -> None:
        """Send a success response for one pending server-initiated request."""

        self._require_initialized()
        await self._connection.respond_server_request(request_id, dump_wire_value(result))

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
            data=dump_wire_value(data),
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

    def set_approval_handler(self, handler: ApprovalHandler | None) -> None:
        """Install or clear the fallback approval callback for approval requests."""

        self._approval_handler = handler
        if handler is None:
            self._connection.set_server_request_fallback_handler(None)
            return
        self._connection.set_server_request_fallback_handler(self._handle_approval_request)

    async def respond_approval_request(
        self,
        request: ApprovalRequest | str | int | None,
        decision: ApprovalDecision,
    ) -> None:
        """Send a typed approval decision for one pending approval request."""

        self._require_initialized(method="approval response")
        if not isinstance(decision, ApprovalDecision):
            raise InvalidApprovalDecisionError(decision)

        request_id = request.request_id if isinstance(request, ApprovalRequest) else request
        try:
            await self.respond_server_request(request_id, decision.as_wire_result())
        except (UnknownServerRequestIdError, ServerRequestAlreadyRespondedError) as exc:
            raise ApprovalRequestExpiredError(request_id) from exc

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

    async def turn_start(
        self,
        *,
        thread_id: str,
        input: TurnInputLike,
        approval_policy: AskForApproval | None = None,
        approvals_reviewer: ApprovalsReviewer | None = None,
        cwd: str | None = None,
        effort: ReasoningEffort | None = None,
        model: str | None = None,
        output_schema: Mapping[str, object] | None = None,
        personality: Personality | None = None,
        sandbox_policy: SandboxPolicy | Mapping[str, object] | None = None,
        service_tier: ServiceTier | None = None,
        summary: ReasoningSummary | None = None,
    ) -> TurnStartResponse:
        """Start a turn and return its initial metadata without waiting for completion."""

        return await self.request(
            "turn/start",
            TurnStartParams(
                thread_id=thread_id,
                input=_coerce_turn_input_items(input),
                approval_policy=approval_policy,
                approvals_reviewer=approvals_reviewer,
                cwd=cwd,
                effort=effort,
                model=model,
                output_schema=output_schema,
                personality=personality,
                sandbox_policy=_coerce_sandbox_policy(sandbox_policy),
                service_tier=service_tier,
                summary=summary,
            ),
            response_model=TurnStartResponse,
        )

    async def turn_steer(
        self,
        *,
        thread_id: str,
        expected_turn_id: str,
        input: TurnInputLike,
    ) -> TurnSteerResponse:
        """Append input to one in-flight turn without starting a new turn."""

        return await self.request(
            "turn/steer",
            TurnSteerParams(
                thread_id=thread_id,
                expected_turn_id=expected_turn_id,
                input=_coerce_turn_input_items(input),
            ),
            response_model=TurnSteerResponse,
        )

    async def turn_interrupt(
        self,
        *,
        thread_id: str,
        turn_id: str,
    ) -> TurnInterruptResponse:
        """Request interruption for one in-flight turn."""

        return await self.request(
            "turn/interrupt",
            TurnInterruptParams(thread_id=thread_id, turn_id=turn_id),
            response_model=TurnInterruptResponse,
        )

    async def wait_for_turn_completed(
        self,
        *,
        thread_id: str,
        turn_id: str,
    ) -> TurnCompletion:
        """Wait for one turn's terminal completion notification and latest token usage."""

        self._require_initialized(method="turn/completed")

        completion_subscription = self.subscribe_turn_notifications(
            turn_id,
            thread_id=thread_id,
            method="turn/completed",
        )
        token_usage_subscription = self.subscribe_turn_notifications(
            turn_id,
            thread_id=thread_id,
            method="thread/tokenUsage/updated",
        )
        completion_notifications = completion_subscription.iter_notifications()
        token_usage_notifications = token_usage_subscription.iter_notifications()

        completion_task = asyncio.create_task(
            _read_next_notification(completion_notifications),
            name=f"codex-agent-sdk.wait-turn-completed:{turn_id}",
        )
        token_usage_task = asyncio.create_task(
            _read_next_notification(token_usage_notifications),
            name=f"codex-agent-sdk.wait-turn-token-usage:{turn_id}",
        )

        latest_token_usage = None

        try:
            while True:
                done, _pending = await asyncio.wait(
                    {completion_task, token_usage_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if token_usage_task in done:
                    token_usage_notification = token_usage_task.result()
                    latest_token_usage = _parse_turn_token_usage_notification(
                        token_usage_notification
                    ).token_usage
                    token_usage_task = asyncio.create_task(
                        _read_next_notification(token_usage_notifications),
                        name=f"codex-agent-sdk.wait-turn-token-usage:{turn_id}",
                    )

                if completion_task in done:
                    completion_notification = completion_task.result()
                    return TurnCompletion(
                        completion=_parse_turn_completed_notification(completion_notification),
                        token_usage=latest_token_usage,
                    )
        except StopAsyncIteration:
            raise TransportClosedError(
                f"app-server connection closed before turn/completed for turn_id={turn_id!r}",
                stderr_tail=self._connection.transport.stderr_tail,
            ) from None
        finally:
            completion_task.cancel()
            token_usage_task.cancel()
            await asyncio.gather(
                completion_task,
                token_usage_task,
                return_exceptions=True,
            )
            completion_subscription.close()
            token_usage_subscription.close()

    def iter_turn_events(
        self,
        *,
        thread_id: str,
        turn_id: str,
    ) -> AsyncIterator[TurnEvent]:
        """Iterate typed turn events for one target turn from the current subscription point."""

        self._require_initialized(method="turn event stream")
        return _iter_turn_events(self, thread_id=thread_id, turn_id=turn_id)

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

    def _adapt_approval_request(self, request: JsonRpcRequest) -> ApprovalRequest | None:
        return adapt_approval_request(
            request,
            responder=self._approval_responder_for(request.request_id),
        )

    def _approval_responder_for(
        self,
        request_id: str | int | None,
    ) -> Callable[[ApprovalDecision], Awaitable[None]]:
        async def _responder(decision: ApprovalDecision) -> None:
            await self.respond_approval_request(request_id, decision)

        return _responder

    async def _handle_approval_request(self, request: JsonRpcRequest) -> object:
        approval_request = self._adapt_approval_request(request)
        if approval_request is None:
            return SERVER_REQUEST_NOT_HANDLED

        approval_handler = self._approval_handler
        if approval_handler is None:
            return SERVER_REQUEST_NOT_HANDLED

        try:
            decision = await approval_handler(approval_request)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            callback_error = ApprovalCallbackError(
                approval_request.request_id,
                original_error=exc,
            )
            _LOGGER.warning(str(callback_error), exc_info=exc)
            return SERVER_REQUEST_NOT_HANDLED

        if decision is None:
            return SERVER_REQUEST_NOT_HANDLED

        if not isinstance(decision, ApprovalDecision):
            invalid_decision_error = InvalidApprovalDecisionError(decision)
            _LOGGER.warning(str(invalid_decision_error))
            return SERVER_REQUEST_NOT_HANDLED

        return dump_wire_value(decision.as_wire_result())


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


async def _read_next_notification(
    notifications: AsyncIterator[JsonRpcNotification],
) -> JsonRpcNotification:
    return await anext(notifications)


async def _read_next_server_request(
    requests: AsyncIterator[JsonRpcRequest],
) -> JsonRpcRequest:
    return await anext(requests)


async def _stream_turn_events(
    client: AppServerClient,
    *,
    turn_id: str,
    notifications: AsyncIterator[JsonRpcNotification],
    notification_subscription: JsonRpcNotificationSubscription,
    server_requests: AsyncIterator[JsonRpcRequest],
    server_request_subscription: JsonRpcServerRequestSubscription,
    close_message: str,
) -> AsyncIterator[TurnEvent]:
    adapter_state = TurnEventAdapterState()
    stream_completed = False
    notification_task = asyncio.create_task(
        _read_next_notification(notifications),
        name=f"codex-agent-sdk.turn-notification:{turn_id}",
    )
    server_request_task = asyncio.create_task(
        _read_next_server_request(server_requests),
        name=f"codex-agent-sdk.turn-server-request:{turn_id}",
    )

    try:
        while True:
            done, _pending = await asyncio.wait(
                {notification_task, server_request_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if server_request_task in done:
                try:
                    server_request = server_request_task.result()
                except StopAsyncIteration:
                    if stream_completed:
                        return
                    raise TransportClosedError(
                        close_message,
                        stderr_tail=client._connection.transport.stderr_tail,
                    ) from None

                server_request_task = asyncio.create_task(
                    _read_next_server_request(server_requests),
                    name=f"codex-agent-sdk.turn-server-request:{turn_id}",
                )

                approval_request = client._adapt_approval_request(server_request)
                server_request_event = adapt_turn_server_request(
                    server_request,
                    target_turn_id=turn_id,
                    approval_request=approval_request,
                )
                if server_request_event is not None:
                    yield server_request_event

            if notification_task in done:
                try:
                    notification = notification_task.result()
                except StopAsyncIteration:
                    if stream_completed:
                        return
                    raise TransportClosedError(
                        close_message,
                        stderr_tail=client._connection.transport.stderr_tail,
                    ) from None

                notification_task = asyncio.create_task(
                    _read_next_notification(notifications),
                    name=f"codex-agent-sdk.turn-notification:{turn_id}",
                )

                notification_event = adapt_turn_notification(
                    notification,
                    target_turn_id=turn_id,
                    state=adapter_state,
                )
                if notification_event is not None:
                    yield notification_event
                    if isinstance(notification_event, TurnCompletedEvent):
                        stream_completed = True
                        return
    finally:
        notification_task.cancel()
        server_request_task.cancel()
        await asyncio.gather(
            notification_task,
            server_request_task,
            return_exceptions=True,
        )
        notification_subscription.close()
        server_request_subscription.close()


async def _iter_turn_events(
    client: AppServerClient,
    *,
    thread_id: str,
    turn_id: str,
) -> AsyncIterator[TurnEvent]:
    notification_subscription = client.subscribe_thread_notifications(thread_id)
    notifications = notification_subscription.iter_notifications()
    server_request_subscription = client.subscribe_turn_server_requests(
        turn_id,
        thread_id=thread_id,
    )
    server_requests = server_request_subscription.iter_requests()
    async for event in _stream_turn_events(
        client,
        turn_id=turn_id,
        notifications=notifications,
        notification_subscription=notification_subscription,
        server_requests=server_requests,
        server_request_subscription=server_request_subscription,
        close_message=(
            f"app-server connection closed before turn stream completed for turn_id={turn_id!r}"
        ),
    ):
        yield event


async def _iter_approval_requests(
    client: AppServerClient,
    *,
    thread_id: str | None,
    turn_id: str | None,
) -> AsyncIterator[ApprovalRequest]:
    if turn_id is not None:
        subscription = client.subscribe_turn_server_requests(turn_id, thread_id=thread_id)
    elif thread_id is not None:
        subscription = client.subscribe_thread_server_requests(thread_id)
    else:
        subscription = client.subscribe_server_requests()

    requests = subscription.iter_requests()
    try:
        async for request in requests:
            approval_request = client._adapt_approval_request(request)
            if approval_request is None:
                continue
            yield approval_request
    finally:
        subscription.close()


def _parse_turn_completed_notification(
    notification: JsonRpcNotification,
) -> TurnCompletedNotification:
    parsed = parse_server_notification(notification)
    if not isinstance(parsed, TypedServerNotification) or not isinstance(
        parsed.params, TurnCompletedNotification
    ):
        raise TypeError("Expected a typed turn/completed notification.")
    return parsed.params


def _parse_turn_token_usage_notification(
    notification: JsonRpcNotification,
) -> ThreadTokenUsageUpdatedNotification:
    parsed = parse_server_notification(notification)
    if not isinstance(parsed, TypedServerNotification) or not isinstance(
        parsed.params, ThreadTokenUsageUpdatedNotification
    ):
        raise TypeError("Expected a typed thread/tokenUsage/updated notification.")
    return parsed.params


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


def _coerce_turn_input_items(input: TurnInputLike) -> list[UserInput]:
    if isinstance(input, str):
        return [UserInput.model_validate({"type": "text", "text": input})]
    if isinstance(input, Sequence):
        return [UserInput.model_validate(item) for item in input]
    return [UserInput.model_validate(input)]


def _coerce_sandbox_policy(
    sandbox_policy: SandboxPolicy | Mapping[str, object] | None,
) -> SandboxPolicy | None:
    if sandbox_policy is None or isinstance(sandbox_policy, SandboxPolicy):
        return sandbox_policy
    return SandboxPolicy.model_validate(sandbox_policy)

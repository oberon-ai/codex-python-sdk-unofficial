from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

JsonRpcId = str | int | None

OVERLOAD_ERROR_CODE = -32001
JSON_RPC_SERVER_ERROR_MIN = -32099
JSON_RPC_SERVER_ERROR_MAX = -32000


class CodexError(Exception):
    """Base exception for SDK failures other than ``asyncio.CancelledError``."""


class ClientStateError(CodexError):
    """Raised when the caller uses a client object in an invalid state."""


class TransportError(CodexError):
    """Raised for subprocess or stdio transport failures."""


class StartupError(TransportError):
    """Raised when the SDK cannot start or initialize the app-server."""

    def __init__(
        self,
        message: str,
        *,
        stderr_tail: str | None = None,
        exit_code: int | None = None,
        command: Sequence[str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.stderr_tail = stderr_tail
        self.exit_code = exit_code
        self.command = tuple(command) if command is not None else None
        self.cwd = cwd
        TransportError.__init__(
            self,
            _compose_message(
                message,
                stderr_tail=stderr_tail,
                exit_code=exit_code,
                command=self.command,
                cwd=cwd,
            ),
        )


class CodexNotFoundError(StartupError, FileNotFoundError):
    """Raised when the configured Codex binary cannot be found."""

    def __init__(
        self,
        path: str | None = None,
        *,
        command: Sequence[str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.path = path
        message = "Codex binary not found"
        if path:
            message = f"{message}: {path}"
        super().__init__(message, command=command, cwd=cwd)


class ShutdownError(TransportError):
    """Raised when shutdown or cleanup fails."""

    def __init__(self, message: str, *, stderr_tail: str | None = None) -> None:
        self.stderr_tail = stderr_tail
        TransportError.__init__(self, _compose_message(message, stderr_tail=stderr_tail))


class ProcessExitError(TransportError):
    """Raised when the app-server process exits unexpectedly."""

    def __init__(
        self,
        message: str = "app-server exited unexpectedly",
        *,
        exit_code: int | None = None,
        stderr_tail: str | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.stderr_tail = stderr_tail
        super().__init__(_compose_message(message, stderr_tail=stderr_tail, exit_code=exit_code))


class TransportClosedError(TransportError):
    """Raised when the stdio transport closes before the SDK expects it to."""

    def __init__(
        self,
        message: str = "app-server transport closed",
        *,
        stderr_tail: str | None = None,
    ) -> None:
        self.stderr_tail = stderr_tail
        super().__init__(_compose_message(message, stderr_tail=stderr_tail))


class TransportWriteError(TransportError):
    """Raised when writing to the app-server transport fails or becomes unsafe."""

    def __init__(self, message: str = "failed to write request to app-server transport") -> None:
        super().__init__(message)


class MessageDecodeError(TransportError):
    """Raised when a JSONL message cannot be decoded or parsed."""

    def __init__(
        self,
        line: str,
        *,
        original_error: BaseException,
        stderr_tail: str | None = None,
    ) -> None:
        self.line = line
        self.original_error = original_error
        self.stderr_tail = stderr_tail
        preview = line[:160]
        super().__init__(
            _compose_message(
                f"failed to decode app-server message: {preview!r}",
                stderr_tail=stderr_tail,
            )
        )


class JsonRpcError(CodexError):
    """Base JSON-RPC error wrapper that preserves the original payload details."""

    def __init__(
        self,
        code: int,
        message: str,
        *,
        data: Any = None,
        method: str | None = None,
        request_id: JsonRpcId = None,
    ) -> None:
        self.code = code
        self.rpc_message = message
        self.data = data
        self.method = method
        self.request_id = request_id

        details = [f"JSON-RPC error {code}: {message}"]
        if method:
            details.append(f"method={method}")
        if request_id is not None:
            details.append(f"request_id={request_id}")
        super().__init__("; ".join(details))


class JsonRpcParseError(JsonRpcError):
    """Raised for JSON-RPC parse errors (code ``-32700``)."""


class JsonRpcInvalidRequestError(JsonRpcError):
    """Raised for JSON-RPC invalid-request errors (code ``-32600``)."""


class JsonRpcMethodNotFoundError(JsonRpcError):
    """Raised for JSON-RPC method-not-found errors (code ``-32601``)."""


class JsonRpcInvalidParamsError(JsonRpcError):
    """Raised for JSON-RPC invalid-params errors (code ``-32602``)."""


class JsonRpcInternalError(JsonRpcError):
    """Raised for JSON-RPC internal errors (code ``-32603``)."""


class JsonRpcServerError(JsonRpcError):
    """Raised for server-defined JSON-RPC errors in the ``-32099..-32000`` range."""


class RetryableOverloadError(JsonRpcServerError):
    """Raised when the server reports transient overload and a retry may succeed."""


class RetryBudgetExceededError(RetryableOverloadError):
    """Raised when overload handling exhausted a retry budget."""


class ProtocolError(CodexError):
    """Raised when transport messages violate the expected app-server protocol."""


class HandshakeError(ProtocolError):
    """Raised when the required initialize or initialized handshake fails."""


class NotInitializedError(HandshakeError, JsonRpcServerError):
    """Raised when the client sends a request before initialization finished."""


class AlreadyInitializedError(HandshakeError, JsonRpcServerError):
    """Raised when the client attempts to initialize the same connection twice."""


class UnexpectedMessageError(ProtocolError):
    """Raised when the SDK receives a message that does not fit its current state."""


class ResponseValidationError(ProtocolError):
    """Raised when a response payload does not match the expected schema."""

    def __init__(self, message: str, *, method: str | None = None, payload: Any = None) -> None:
        self.method = method
        self.payload = payload
        parts = [message]
        if method:
            parts.append(f"method={method}")
        super().__init__("; ".join(parts))


class CodexTimeoutError(CodexError):
    """Base class for SDK-local timeout errors."""

    def __init__(self, message: str, *, timeout_seconds: float | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        if timeout_seconds is not None:
            message = f"{message} (timeout={timeout_seconds:.3f}s)"
        super().__init__(message)


class StartupTimeoutError(StartupError, CodexTimeoutError):
    """Raised when process startup or initialization exceeds the local deadline."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        stderr_tail: str | None = None,
        command: Sequence[str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        StartupError.__init__(
            self,
            _format_timeout_message("app-server startup timed out", timeout_seconds),
            stderr_tail=stderr_tail,
            command=command,
            cwd=cwd,
        )


class ShutdownTimeoutError(ShutdownError, CodexTimeoutError):
    """Raised when shutdown exceeds the configured local deadline."""

    def __init__(self, *, timeout_seconds: float, stderr_tail: str | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        ShutdownError.__init__(
            self,
            _format_timeout_message("app-server shutdown timed out", timeout_seconds),
            stderr_tail=stderr_tail,
        )


class RequestTimeoutError(CodexTimeoutError):
    """Raised when a caller-configured request deadline expires."""

    def __init__(
        self,
        *,
        method: str | None = None,
        timeout_seconds: float,
        request_id: JsonRpcId = None,
    ) -> None:
        self.method = method
        self.request_id = request_id
        message = "request timed out"
        if method:
            message = f"{method} timed out"
        CodexTimeoutError.__init__(self, message, timeout_seconds=timeout_seconds)


class ApprovalError(CodexError):
    """Base class for approval-request handling failures."""


class ApprovalCallbackError(ApprovalError):
    """Raised when an approval callback crashes."""

    def __init__(self, request_id: JsonRpcId, *, original_error: BaseException) -> None:
        self.request_id = request_id
        self.original_error = original_error
        super().__init__(f"approval callback failed for request_id={request_id}")


class InvalidApprovalDecisionError(ApprovalError):
    """Raised when a callback returns a decision the SDK cannot encode."""

    def __init__(self, decision: Any) -> None:
        self.decision = decision
        super().__init__(f"invalid approval decision: {decision!r}")


class ApprovalRequestExpiredError(ApprovalError):
    """Raised when a caller responds to an approval request after it was cleared."""

    def __init__(self, request_id: JsonRpcId) -> None:
        self.request_id = request_id
        super().__init__(f"approval request no longer pending for request_id={request_id}")


def map_jsonrpc_error(
    code: int,
    message: str,
    *,
    data: Any = None,
    method: str | None = None,
    request_id: JsonRpcId = None,
) -> JsonRpcError:
    """Map a raw JSON-RPC error payload into a richer SDK exception type."""

    kwargs = {"data": data, "method": method, "request_id": request_id}

    if code == -32700:
        return JsonRpcParseError(code, message, **kwargs)
    if code == -32600:
        return JsonRpcInvalidRequestError(code, message, **kwargs)
    if code == -32601:
        return JsonRpcMethodNotFoundError(code, message, **kwargs)
    if code == -32602:
        return JsonRpcInvalidParamsError(code, message, **kwargs)
    if code == -32603:
        return JsonRpcInternalError(code, message, **kwargs)

    if JSON_RPC_SERVER_ERROR_MIN <= code <= JSON_RPC_SERVER_ERROR_MAX:
        lowered = message.lower()
        if lowered == "not initialized":
            return NotInitializedError(code, message, **kwargs)
        if lowered == "already initialized":
            return AlreadyInitializedError(code, message, **kwargs)
        if _is_overload_error(code, message, data):
            if _contains_retry_budget_text(message):
                return RetryBudgetExceededError(code, message, **kwargs)
            return RetryableOverloadError(code, message, **kwargs)
        return JsonRpcServerError(code, message, **kwargs)

    return JsonRpcError(code, message, **kwargs)


def is_retryable_error(exc: BaseException) -> bool:
    """Return ``True`` when an exception represents transient overload."""

    return isinstance(exc, RetryableOverloadError)


def _compose_message(
    message: str,
    *,
    stderr_tail: str | None = None,
    exit_code: int | None = None,
    command: Sequence[str] | None = None,
    cwd: str | None = None,
) -> str:
    details = [message]
    if command is not None:
        details.append(f"command={list(command)!r}")
    if cwd is not None:
        details.append(f"cwd={cwd}")
    if exit_code is not None:
        details.append(f"exit_code={exit_code}")
    if stderr_tail:
        details.append(f"stderr_tail={stderr_tail}")
    return "; ".join(details)


def _format_timeout_message(message: str, timeout_seconds: float) -> str:
    return f"{message} (timeout={timeout_seconds:.3f}s)"


def _is_overload_error(code: int, message: str, data: Any) -> bool:
    if code == OVERLOAD_ERROR_CODE:
        return True
    if "overload" in message.lower() or "retry later" in message.lower():
        return True
    return _contains_overload_marker(data)


def _contains_retry_budget_text(message: str) -> bool:
    lowered = message.lower()
    return (
        "retry budget" in lowered
        or "retry limit" in lowered
        or "too many failed attempts" in lowered
    )


def _contains_overload_marker(data: Any) -> bool:
    if data is None:
        return False
    if isinstance(data, str):
        return data.lower() == "server_overloaded"
    if isinstance(data, Mapping):
        for key, value in data.items():
            if key in {
                "codexErrorInfo",
                "codex_error_info",
                "errorInfo",
                "error_info",
            }:
                if _contains_overload_marker(value):
                    return True
            if _contains_overload_marker(value):
                return True
        return False
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        return any(_contains_overload_marker(value) for value in data)
    return False

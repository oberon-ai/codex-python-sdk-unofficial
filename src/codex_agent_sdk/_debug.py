"""Internal helpers for opt-in, redacted transport debug logging."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence

from .rpc.jsonrpc import JsonRpcEnvelope

DEFAULT_DEBUG_LOGGER_NAME = "codex_agent_sdk.debug"
MAX_DEBUG_ITEMS = 8
MAX_DEBUG_STRING_CHARS = 120
MAX_DEBUG_DEPTH = 4
SENSITIVE_FIELD_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "command",
    "content",
    "cookie",
    "credential",
    "cwd",
    "diff",
    "env",
    "file",
    "input",
    "instruction",
    "output",
    "password",
    "patch",
    "path",
    "prompt",
    "secret",
    "stderr",
    "stdout",
    "text",
    "token",
)
SENSITIVE_ENV_KEY_FRAGMENTS = (
    "api",
    "auth",
    "cookie",
    "credential",
    "key",
    "password",
    "secret",
    "token",
)


class DebugLogger:
    """Small wrapper around stdlib logging for sanitized protocol diagnostics."""

    def __init__(
        self,
        *,
        enabled: bool,
        logger: logging.Logger | None = None,
    ) -> None:
        self._enabled = enabled
        self._logger = logger or logging.getLogger(DEFAULT_DEBUG_LOGGER_NAME)

    @property
    def enabled(self) -> bool:
        return self._enabled and self._logger.isEnabledFor(logging.DEBUG)

    def log_transport_starting(
        self,
        *,
        command: tuple[str, ...],
        cwd: str | None,
        env_overrides: Mapping[str, str] | None,
    ) -> None:
        self._emit(
            "transport_starting",
            "transport starting",
            command=_summarize_command(command),
            cwd_set=cwd is not None,
            env_override_keys=_summarize_env_override_keys(env_overrides),
        )

    def log_transport_started(
        self,
        *,
        command: tuple[str, ...],
        pid: int | None,
    ) -> None:
        self._emit(
            "transport_started",
            "transport started",
            command=_summarize_command(command),
            pid=pid,
        )

    def log_transport_start_failed(
        self,
        *,
        command: tuple[str, ...],
        error: BaseException,
    ) -> None:
        self._emit(
            "transport_start_failed",
            f"transport start failed: {error.__class__.__name__}",
            command=_summarize_command(command),
            error_type=error.__class__.__name__,
        )

    def log_transport_closing(
        self,
        *,
        pid: int | None,
        returncode: int | None,
    ) -> None:
        self._emit(
            "transport_closing",
            "transport closing",
            pid=pid,
            returncode=returncode,
        )

    def log_transport_shutdown_escalation(
        self,
        *,
        signal_name: str,
        pid: int | None,
    ) -> None:
        self._emit(
            "transport_shutdown_escalation",
            f"transport shutdown escalating via {signal_name}",
            pid=pid,
            signal=signal_name,
        )

    def log_transport_closed(
        self,
        *,
        pid: int | None,
        returncode: int | None,
    ) -> None:
        self._emit(
            "transport_closed",
            "transport closed",
            pid=pid,
            returncode=returncode,
        )

    def log_transport_close_failed(
        self,
        *,
        pid: int | None,
        error: BaseException,
    ) -> None:
        self._emit(
            "transport_close_failed",
            f"transport close failed: {error.__class__.__name__}",
            pid=pid,
            error_type=error.__class__.__name__,
        )

    def log_frame(self, *, direction: str, envelope: JsonRpcEnvelope) -> None:
        kind = _frame_kind(envelope)
        request_id = envelope.get("id")
        method = envelope.get("method")
        message = f"jsonrpc {direction} {kind}"
        if request_id is not None:
            message += f" id={request_id!r}"
        if isinstance(method, str):
            message += f" method={method}"

        self._emit(
            "jsonrpc_frame",
            message,
            direction=direction,
            kind=kind,
            request_id=request_id,
            method=method if isinstance(method, str) else None,
            frame_preview=sanitize_debug_value(envelope),
        )

    def log_frame_read_failed(self, *, error: BaseException, preview: str | None = None) -> None:
        self._emit(
            "jsonrpc_frame_read_failed",
            f"jsonrpc inbound frame read failed: {error.__class__.__name__}",
            direction="inbound",
            error_type=error.__class__.__name__,
            frame_preview=_truncate_string(preview) if preview is not None else None,
        )

    def log_frame_write_failed(
        self,
        *,
        envelope: JsonRpcEnvelope,
        error: BaseException,
    ) -> None:
        kind = _frame_kind(envelope)
        request_id = envelope.get("id")
        method = envelope.get("method")
        message = f"jsonrpc outbound {kind} failed"
        if request_id is not None:
            message += f" id={request_id!r}"
        if isinstance(method, str):
            message += f" method={method}"

        self._emit(
            "jsonrpc_frame_write_failed",
            message,
            direction="outbound",
            kind=kind,
            request_id=request_id,
            method=method if isinstance(method, str) else None,
            error_type=error.__class__.__name__,
            frame_preview=sanitize_debug_value(envelope),
        )

    def _emit(self, event: str, message: str, **fields: object) -> None:
        if not self.enabled:
            return

        extra: dict[str, object] = {"codex_debug_event": event}
        for key, value in fields.items():
            extra[f"codex_{key}"] = value
        self._logger.debug(message, extra=extra)


def sanitize_debug_value(
    value: object,
    *,
    key: str | None = None,
    depth: int = 0,
) -> object:
    """Redact or truncate values before they reach debug logs."""

    if depth >= MAX_DEBUG_DEPTH:
        return "<max-depth>"

    if key is not None and _is_sensitive_key(key):
        return _redacted_value(key=key, value=value)

    if value is None or isinstance(value, bool | int | float):
        return value

    if isinstance(value, str):
        return _truncate_string(value)

    if isinstance(value, bytes | bytearray | memoryview):
        return f"<bytes len={len(value)}>"

    if isinstance(value, Mapping):
        sanitized: dict[str, object] = {}
        items = list(value.items())
        for index, (item_key, item_value) in enumerate(items):
            if index >= MAX_DEBUG_ITEMS:
                sanitized["__truncated_items__"] = len(items) - MAX_DEBUG_ITEMS
                break
            key_str = str(item_key)
            sanitized[key_str] = sanitize_debug_value(
                item_value,
                key=key_str,
                depth=depth + 1,
            )
        return sanitized

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray | memoryview):
        items = list(value)
        sanitized_items = [
            sanitize_debug_value(item, depth=depth + 1) for item in items[:MAX_DEBUG_ITEMS]
        ]
        if len(items) > MAX_DEBUG_ITEMS:
            sanitized_items.append(f"<{len(items) - MAX_DEBUG_ITEMS} more items>")
        return sanitized_items

    return _truncate_string(repr(value))


def _frame_kind(envelope: Mapping[str, object]) -> str:
    if "method" in envelope and "id" in envelope:
        return "request"
    if "method" in envelope:
        return "notification"
    return "response"


def _summarize_command(command: tuple[str, ...]) -> tuple[str, ...]:
    if not command:
        return command
    return (os.path.basename(command[0]), *command[1:])


def _summarize_env_override_keys(env_overrides: Mapping[str, str] | None) -> tuple[str, ...]:
    if not env_overrides:
        return ()
    return tuple(_summarize_env_key(key) for key in sorted(env_overrides))


def _summarize_env_key(key: str) -> str:
    normalized = key.lower().replace("-", "_")
    if any(fragment in normalized for fragment in SENSITIVE_ENV_KEY_FRAGMENTS):
        return "<redacted-env-key>"
    return key


def _redacted_value(*, key: str, value: object) -> str:
    normalized = key.lower().replace("-", "_")
    if isinstance(value, Mapping):
        return f"<redacted {normalized} keys={len(value)}>"
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray | memoryview):
        return f"<redacted {normalized} items={len(value)}>"
    if isinstance(value, str):
        return f"<redacted {normalized} len={len(value)}>"
    if isinstance(value, bytes | bytearray | memoryview):
        return f"<redacted {normalized} bytes={len(value)}>"
    return f"<redacted {normalized}>"


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(fragment in normalized for fragment in SENSITIVE_FIELD_FRAGMENTS)


def _truncate_string(value: str) -> str:
    single_line = value.replace("\r", "\\r").replace("\n", "\\n")
    if len(single_line) <= MAX_DEBUG_STRING_CHARS:
        return single_line
    return f"{single_line[:MAX_DEBUG_STRING_CHARS]}...<truncated len={len(single_line)}>"


__all__ = [
    "DEFAULT_DEBUG_LOGGER_NAME",
    "DebugLogger",
    "sanitize_debug_value",
]

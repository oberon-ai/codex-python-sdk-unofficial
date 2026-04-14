"""Async fake ``codex app-server`` harness for integration tests.

The harness replays deterministic JSONL scripts over stdio so transport, JSON-RPC
connection, routing, approval, and cancellation tests do not need a real Codex
binary.

Example:

```python
from pathlib import Path

from codex_agent_sdk.testing.fake_app_server import (
    FakeAppServerScript,
    expect_notification,
    expect_request,
    send_response,
)

script = FakeAppServerScript.from_actions(
    expect_request("initialize", save_as="initialize"),
    send_response(
        request_ref="initialize",
        result={"protocolVersion": 2, "serverInfo": {"name": "fake-codex"}},
    ),
    expect_notification("initialized"),
)
script.write_jsonl(Path("tests/fixtures/fake_server_scripts/001_initialize.script.jsonl"))
```

The subprocess entry point is exposed as ``python -m codex_agent_sdk.testing.fake_app_server``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, cast

JsonRpcId = str | int | None
JsonObject = dict[str, Any]

DEFAULT_EXPECT_TIMEOUT_MS: Final[int] = 1_000
_UNSET: Final[object] = object()
_ALLOWED_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "close",
        "emit_raw",
        "expect_notification",
        "expect_request",
        "expect_response",
        "send_notification",
        "send_response",
        "send_server_request",
        "sleep",
    }
)


class FakeAppServerScriptError(ValueError):
    """Raised when a fake app-server script is malformed."""


class FakeAppServerRuntimeError(RuntimeError):
    """Raised when script replay and client traffic diverge."""


@dataclass(frozen=True, slots=True)
class FakeAppServerScript:
    """Deterministic fake app-server scenario stored as JSONL actions."""

    actions: tuple[JsonObject, ...]

    def __post_init__(self) -> None:
        normalized = tuple(_normalize_action(action) for action in self.actions)
        object.__setattr__(self, "actions", normalized)

    @classmethod
    def from_actions(cls, *actions: Mapping[str, Any]) -> FakeAppServerScript:
        """Build a script from in-memory action mappings."""

        return cls(actions=tuple(deepcopy(dict(action)) for action in actions))

    @classmethod
    def from_jsonl(cls, path: Path) -> FakeAppServerScript:
        """Load a JSONL script from disk."""

        return load_fake_app_server_script(path)

    def to_jsonl(self) -> str:
        """Serialize the script to deterministic JSONL text."""

        if not self.actions:
            return ""
        lines = [json.dumps(action, sort_keys=True) for action in self.actions]
        return "\n".join(lines) + "\n"

    def write_jsonl(self, path: Path) -> None:
        """Write the JSONL script to disk."""

        path.write_text(self.to_jsonl(), encoding="utf-8")


def load_fake_app_server_script(path: Path) -> FakeAppServerScript:
    """Load and validate a fake app-server JSONL script."""

    actions: list[JsonObject] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            parsed = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise FakeAppServerScriptError(
                f"failed to parse {path}:{line_number} as JSON: {exc.msg}"
            ) from exc
        if not isinstance(parsed, dict):
            raise FakeAppServerScriptError(
                f"expected {path}:{line_number} to be a JSON object action"
            )
        actions.append(_normalize_action(parsed, source=path, line_number=line_number))
    return FakeAppServerScript(actions=tuple(actions))


def expect_request(
    method: str,
    *,
    params: Any | None = None,
    request_id: JsonRpcId | object = _UNSET,
    save_as: str | None = None,
    timeout_ms: int | None = None,
) -> JsonObject:
    """Expect a client JSON-RPC request from stdin."""

    action: JsonObject = {"action": "expect_request", "method": method}
    if params is not None:
        action["params"] = deepcopy(params)
    if request_id is not _UNSET:
        action["request_id"] = request_id
    if save_as is not None:
        action["save_as"] = save_as
    if timeout_ms is not None:
        action["timeout_ms"] = timeout_ms
    return _normalize_action(action)


def expect_notification(
    method: str,
    *,
    params: Any | None = None,
    save_as: str | None = None,
    timeout_ms: int | None = None,
) -> JsonObject:
    """Expect a client JSON-RPC notification from stdin."""

    action: JsonObject = {"action": "expect_notification", "method": method}
    if params is not None:
        action["params"] = deepcopy(params)
    if save_as is not None:
        action["save_as"] = save_as
    if timeout_ms is not None:
        action["timeout_ms"] = timeout_ms
    return _normalize_action(action)


def send_response(
    *,
    request_ref: str | None = None,
    result: Any = _UNSET,
    error: Mapping[str, Any] | None = None,
    delay_ms: int | None = None,
) -> JsonObject:
    """Send a JSON-RPC response for an earlier client request."""

    action: JsonObject = {"action": "send_response"}
    if request_ref is not None:
        action["request_ref"] = request_ref
    if result is not _UNSET:
        action["result"] = deepcopy(result)
    if error is not None:
        action["error"] = deepcopy(dict(error))
    if delay_ms is not None:
        action["delay_ms"] = delay_ms
    return _normalize_action(action)


def send_notification(
    method: str,
    *,
    params: Any | None = None,
    delay_ms: int | None = None,
) -> JsonObject:
    """Send a server notification to stdout."""

    action: JsonObject = {"action": "send_notification", "method": method}
    if params is not None:
        action["params"] = deepcopy(params)
    if delay_ms is not None:
        action["delay_ms"] = delay_ms
    return _normalize_action(action)


def send_server_request(
    method: str,
    *,
    request_id: str | int,
    params: Any | None = None,
    save_as: str | None = None,
    delay_ms: int | None = None,
) -> JsonObject:
    """Send a server-initiated JSON-RPC request to the client."""

    action: JsonObject = {
        "action": "send_server_request",
        "method": method,
        "request_id": request_id,
    }
    if params is not None:
        action["params"] = deepcopy(params)
    if save_as is not None:
        action["save_as"] = save_as
    if delay_ms is not None:
        action["delay_ms"] = delay_ms
    return _normalize_action(action)


def expect_response(
    *,
    request_ref: str | None = None,
    result: Any = _UNSET,
    error: Mapping[str, Any] | None = None,
    save_as: str | None = None,
    timeout_ms: int | None = None,
) -> JsonObject:
    """Expect a JSON-RPC response from the client to an earlier server request."""

    action: JsonObject = {"action": "expect_response"}
    if request_ref is not None:
        action["request_ref"] = request_ref
    if result is not _UNSET:
        action["result"] = deepcopy(result)
    if error is not None:
        action["error"] = deepcopy(dict(error))
    if save_as is not None:
        action["save_as"] = save_as
    if timeout_ms is not None:
        action["timeout_ms"] = timeout_ms
    return _normalize_action(action)


def sleep_action(duration_ms: int) -> JsonObject:
    """Pause script replay for a deterministic number of milliseconds."""

    return _normalize_action({"action": "sleep", "duration_ms": duration_ms})


def emit_raw(line: str, *, delay_ms: int | None = None) -> JsonObject:
    """Emit a raw stdout line, which may be invalid JSON on purpose."""

    action: JsonObject = {"action": "emit_raw", "line": line}
    if delay_ms is not None:
        action["delay_ms"] = delay_ms
    return _normalize_action(action)


def emit_invalid_json(
    line: str = '{"this-is": invalid json}',
    *,
    delay_ms: int | None = None,
) -> JsonObject:
    """Emit a deliberately invalid JSON line."""

    return emit_raw(line, delay_ms=delay_ms)


def close_connection(*, delay_ms: int | None = None, exit_code: int = 0) -> JsonObject:
    """Close stdout and exit the fake server process."""

    action: JsonObject = {"action": "close", "exit_code": exit_code}
    if delay_ms is not None:
        action["delay_ms"] = delay_ms
    return _normalize_action(action)


@dataclass(slots=True)
class FakeAppServer:
    """Replay a deterministic JSONL scenario over async stdio streams."""

    script: FakeAppServerScript
    default_expect_timeout_ms: int = DEFAULT_EXPECT_TIMEOUT_MS

    async def run(self, *, reader: asyncio.StreamReader, writer: _AsyncJsonlWriter) -> int:
        """Run the script until completion or a deliberate close action."""

        if self.default_expect_timeout_ms <= 0:
            raise FakeAppServerScriptError("default_expect_timeout_ms must be positive")

        state = _RuntimeState()
        exit_code = 0
        try:
            for index, action in enumerate(self.script.actions, start=1):
                maybe_exit_code = await self._run_action(index, action, state, reader, writer)
                if maybe_exit_code is not None:
                    exit_code = maybe_exit_code
                    break
        finally:
            await writer.aclose()
        return exit_code

    async def _run_action(
        self,
        step_index: int,
        action: JsonObject,
        state: _RuntimeState,
        reader: asyncio.StreamReader,
        writer: _AsyncJsonlWriter,
    ) -> int | None:
        action_name = cast(str, action["action"])
        if action_name == "sleep":
            await asyncio.sleep(_milliseconds_to_seconds(cast(int, action["duration_ms"])))
            return None

        if action_name == "expect_request":
            message = await _read_json_message(
                reader,
                timeout_ms=_expect_timeout_ms(action, self.default_expect_timeout_ms),
                expectation=f"client request for step {step_index}",
            )
            _assert_is_request(message, action, step_index)
            state.remember_client_request(message, save_as=_optional_string(action, "save_as"))
            return None

        if action_name == "expect_notification":
            message = await _read_json_message(
                reader,
                timeout_ms=_expect_timeout_ms(action, self.default_expect_timeout_ms),
                expectation=f"client notification for step {step_index}",
            )
            _assert_is_notification(message, action, step_index)
            state.remember_alias(message, _optional_string(action, "save_as"))
            return None

        if action_name == "send_response":
            await _maybe_delay(action)
            request = state.resolve_client_request(_optional_string(action, "request_ref"))
            response = _build_response(action, request)
            await writer.write_json(response)
            return None

        if action_name == "send_notification":
            await _maybe_delay(action)
            notification = _build_notification(action)
            await writer.write_json(notification)
            return None

        if action_name == "send_server_request":
            await _maybe_delay(action)
            request = _build_server_request(action)
            state.remember_server_request(request, save_as=_optional_string(action, "save_as"))
            await writer.write_json(request)
            return None

        if action_name == "expect_response":
            message = await _read_json_message(
                reader,
                timeout_ms=_expect_timeout_ms(action, self.default_expect_timeout_ms),
                expectation=f"client response for step {step_index}",
            )
            request = state.resolve_server_request(_optional_string(action, "request_ref"))
            _assert_is_response(message, action, request, step_index)
            state.remember_alias(message, _optional_string(action, "save_as"))
            return None

        if action_name == "emit_raw":
            await _maybe_delay(action)
            await writer.write_raw_line(cast(str, action["line"]))
            return None

        if action_name == "close":
            await _maybe_delay(action)
            return cast(int, action.get("exit_code", 0))

        raise FakeAppServerRuntimeError(f"unsupported action at runtime: {action_name}")


@dataclass(slots=True)
class _RuntimeState:
    aliases: dict[str, JsonObject] = field(default_factory=dict)
    last_client_request: JsonObject | None = None
    last_server_request: JsonObject | None = None

    def remember_client_request(self, envelope: JsonObject, *, save_as: str | None) -> None:
        self.last_client_request = envelope
        self.remember_alias(envelope, "last_client_request")
        self.remember_alias(envelope, save_as)

    def remember_server_request(self, envelope: JsonObject, *, save_as: str | None) -> None:
        self.last_server_request = envelope
        self.remember_alias(envelope, "last_server_request")
        self.remember_alias(envelope, save_as)
        request_id = envelope.get("id")
        if isinstance(request_id, str):
            self.remember_alias(envelope, request_id)

    def remember_alias(self, envelope: JsonObject, alias: str | None) -> None:
        if alias is not None:
            self.aliases[alias] = envelope

    def resolve_client_request(self, request_ref: str | None) -> JsonObject:
        if request_ref is None:
            if self.last_client_request is None:
                raise FakeAppServerRuntimeError(
                    "send_response has no prior client request to answer"
                )
            return self.last_client_request
        try:
            return self.aliases[request_ref]
        except KeyError as exc:
            raise FakeAppServerRuntimeError(
                f"unknown client request reference: {request_ref!r}"
            ) from exc

    def resolve_server_request(self, request_ref: str | None) -> JsonObject:
        if request_ref is None:
            if self.last_server_request is None:
                raise FakeAppServerRuntimeError(
                    "expect_response has no prior server request to match"
                )
            return self.last_server_request
        try:
            return self.aliases[request_ref]
        except KeyError as exc:
            raise FakeAppServerRuntimeError(
                f"unknown server request reference: {request_ref!r}"
            ) from exc


class _WritePipeProtocol(asyncio.Protocol):
    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._closed: asyncio.Future[None] = self._loop.create_future()
        self._drain_waiter: asyncio.Future[None] | None = None
        self._paused = False

    def pause_writing(self) -> None:
        self._paused = True

    def resume_writing(self) -> None:
        self._paused = False
        waiter = self._drain_waiter
        self._drain_waiter = None
        if waiter is not None and not waiter.done():
            waiter.set_result(None)

    def connection_lost(self, exc: BaseException | None) -> None:
        waiter = self._drain_waiter
        self._drain_waiter = None
        if waiter is not None and not waiter.done():
            if exc is None:
                waiter.set_result(None)
            else:
                waiter.set_exception(exc)

        if self._closed.done():
            return
        if exc is None:
            self._closed.set_result(None)
        else:
            self._closed.set_exception(exc)

    async def drain(self) -> None:
        if self._paused:
            if self._drain_waiter is None or self._drain_waiter.done():
                self._drain_waiter = self._loop.create_future()
            await self._drain_waiter
        if self._closed.done():
            exception = self._closed.exception()
            if exception is not None:
                raise exception

    async def wait_closed(self) -> None:
        await asyncio.shield(self._closed)


@dataclass(slots=True)
class _AsyncJsonlWriter:
    transport: asyncio.WriteTransport
    protocol: _WritePipeProtocol
    _closed: bool = False

    async def write_json(self, envelope: Mapping[str, Any]) -> None:
        await self.write_raw_line(json.dumps(dict(envelope), separators=(",", ":")))

    async def write_raw_line(self, line: str) -> None:
        if self._closed:
            raise FakeAppServerRuntimeError("stdout writer is already closed")
        payload = line.removesuffix("\n").encode("utf-8") + b"\n"
        self.transport.write(payload)
        await self.protocol.drain()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.transport.close()
        await self.protocol.wait_closed()


async def _open_stdin_reader() -> asyncio.StreamReader:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)
    return reader


async def _open_stdout_writer() -> _AsyncJsonlWriter:
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.connect_write_pipe(_WritePipeProtocol, sys.stdout.buffer)
    return _AsyncJsonlWriter(transport=transport, protocol=protocol)


async def _read_json_message(
    reader: asyncio.StreamReader,
    *,
    timeout_ms: int | None,
    expectation: str,
) -> JsonObject:
    line = await _read_line(reader, timeout_ms=timeout_ms, expectation=expectation)
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError as exc:
        raise FakeAppServerRuntimeError(
            f"{expectation} contained invalid JSON from client: {exc.msg}"
        ) from exc
    if not isinstance(parsed, dict):
        raise FakeAppServerRuntimeError(f"{expectation} must be a JSON object envelope")
    return cast(JsonObject, parsed)


async def _read_line(
    reader: asyncio.StreamReader,
    *,
    timeout_ms: int | None,
    expectation: str,
) -> str:
    try:
        if timeout_ms is None:
            raw_line = await reader.readline()
        else:
            raw_line = await asyncio.wait_for(
                reader.readline(),
                timeout=_milliseconds_to_seconds(timeout_ms),
            )
    except TimeoutError as exc:
        raise FakeAppServerRuntimeError(
            f"timed out after {timeout_ms}ms waiting for {expectation}"
        ) from exc

    if raw_line == b"":
        raise FakeAppServerRuntimeError(f"stdin reached EOF while waiting for {expectation}")

    try:
        return raw_line.decode("utf-8").rstrip("\n")
    except UnicodeDecodeError as exc:
        raise FakeAppServerRuntimeError(f"{expectation} was not valid UTF-8 on stdin") from exc


def _build_response(action: JsonObject, request: JsonObject) -> JsonObject:
    request_id = request.get("id")
    if request_id is None:
        raise FakeAppServerRuntimeError("cannot send a response for a request without an id")

    response: JsonObject = {"id": request_id}
    has_result = "result" in action
    has_error = "error" in action
    if has_result == has_error:
        raise FakeAppServerRuntimeError("send_response requires exactly one of result or error")
    if has_result:
        response["result"] = deepcopy(action["result"])
    else:
        response["error"] = deepcopy(cast(JsonObject, action["error"]))
    return response


def _build_notification(action: JsonObject) -> JsonObject:
    notification: JsonObject = {"method": cast(str, action["method"])}
    if "params" in action:
        notification["params"] = deepcopy(action["params"])
    return notification


def _build_server_request(action: JsonObject) -> JsonObject:
    request: JsonObject = {
        "id": action["request_id"],
        "method": cast(str, action["method"]),
    }
    if "params" in action:
        request["params"] = deepcopy(action["params"])
    return request


def _assert_is_request(message: JsonObject, action: JsonObject, step_index: int) -> None:
    if "method" not in message or "id" not in message:
        raise FakeAppServerRuntimeError(
            f"step {step_index} expected a client request envelope, got {message!r}"
        )
    expected_method = cast(str, action["method"])
    if message["method"] != expected_method:
        raise FakeAppServerRuntimeError(
            f"step {step_index} expected request method {expected_method!r}, "
            f"got {message['method']!r}"
        )
    if "request_id" in action and message.get("id") != action["request_id"]:
        raise FakeAppServerRuntimeError(
            f"step {step_index} expected request id {action['request_id']!r}, "
            f"got {message.get('id')!r}"
        )
    if "params" in action and not _matches_expected(message.get("params"), action["params"]):
        raise FakeAppServerRuntimeError(
            f"step {step_index} request params did not match expected subset"
        )


def _assert_is_notification(message: JsonObject, action: JsonObject, step_index: int) -> None:
    if "method" not in message or "id" in message:
        raise FakeAppServerRuntimeError(
            f"step {step_index} expected a client notification envelope, got {message!r}"
        )
    expected_method = cast(str, action["method"])
    if message["method"] != expected_method:
        raise FakeAppServerRuntimeError(
            f"step {step_index} expected notification method {expected_method!r}, "
            f"got {message['method']!r}"
        )
    if "params" in action and not _matches_expected(message.get("params"), action["params"]):
        raise FakeAppServerRuntimeError(
            f"step {step_index} notification params did not match expected subset"
        )


def _assert_is_response(
    message: JsonObject,
    action: JsonObject,
    request: JsonObject,
    step_index: int,
) -> None:
    if "id" not in message or "method" in message:
        raise FakeAppServerRuntimeError(
            f"step {step_index} expected a client response envelope, got {message!r}"
        )
    if message["id"] != request.get("id"):
        raise FakeAppServerRuntimeError(
            f"step {step_index} expected response id {request.get('id')!r}, got {message['id']!r}"
        )

    has_expected_result = "result" in action
    has_expected_error = "error" in action
    if has_expected_result and not _matches_expected(message.get("result"), action["result"]):
        raise FakeAppServerRuntimeError(
            f"step {step_index} response result did not match expected subset"
        )
    if has_expected_error and not _matches_expected(message.get("error"), action["error"]):
        raise FakeAppServerRuntimeError(
            f"step {step_index} response error did not match expected subset"
        )
    if (
        not has_expected_result
        and not has_expected_error
        and "error" not in message
        and "result" not in message
    ):
        raise FakeAppServerRuntimeError(
            f"step {step_index} response did not include result or error"
        )


def _matches_expected(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return False
        for key, expected_value in expected.items():
            if key not in actual or not _matches_expected(actual[key], expected_value):
                return False
        return True
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(
            _matches_expected(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected, strict=True)
        )
    return bool(actual == expected)


def _expect_timeout_ms(action: JsonObject, default_timeout_ms: int) -> int | None:
    if "timeout_ms" in action:
        return cast(int | None, action["timeout_ms"])
    return default_timeout_ms


async def _maybe_delay(action: JsonObject) -> None:
    delay_ms = cast(int | None, action.get("delay_ms"))
    if delay_ms is not None:
        await asyncio.sleep(_milliseconds_to_seconds(delay_ms))


def _milliseconds_to_seconds(value: int) -> float:
    return value / 1_000.0


def _optional_string(action: JsonObject, key: str) -> str | None:
    value = action.get(key)
    if value is None:
        return None
    return cast(str, value)


def _normalize_action(
    action: Mapping[str, Any],
    *,
    source: Path | None = None,
    line_number: int | None = None,
) -> JsonObject:
    if "action" not in action or not isinstance(action["action"], str):
        raise _script_error(
            "script action must include a string 'action' field",
            source,
            line_number,
        )

    action_name = action["action"]
    if action_name not in _ALLOWED_ACTIONS:
        raise _script_error(f"unsupported action {action_name!r}", source, line_number)

    normalized = deepcopy(dict(action))

    if action_name in {
        "expect_request",
        "expect_notification",
        "send_notification",
        "send_server_request",
    }:
        _require_string(normalized, "method", source, line_number)

    if action_name == "expect_request":
        _validate_optional_json_rpc_id(normalized, "request_id", source, line_number)
        _validate_optional_timeout_ms(normalized, source, line_number)
        _validate_optional_string(normalized, "save_as", source, line_number)
    elif action_name == "expect_notification":
        _validate_optional_timeout_ms(normalized, source, line_number)
        _validate_optional_string(normalized, "save_as", source, line_number)
    elif action_name == "send_response":
        if ("result" in normalized) == ("error" in normalized):
            raise _script_error(
                "send_response requires exactly one of 'result' or 'error'",
                source,
                line_number,
            )
        _validate_optional_string(normalized, "request_ref", source, line_number)
        _validate_optional_delay_ms(normalized, source, line_number)
    elif action_name == "send_notification":
        _validate_optional_delay_ms(normalized, source, line_number)
    elif action_name == "send_server_request":
        request_id = normalized.get("request_id")
        if not isinstance(request_id, (str, int)):
            raise _script_error(
                "send_server_request requires a string or integer 'request_id'",
                source,
                line_number,
            )
        _validate_optional_string(normalized, "save_as", source, line_number)
        _validate_optional_delay_ms(normalized, source, line_number)
    elif action_name == "expect_response":
        if "result" in normalized and "error" in normalized:
            raise _script_error(
                "expect_response accepts at most one of 'result' or 'error'",
                source,
                line_number,
            )
        _validate_optional_string(normalized, "request_ref", source, line_number)
        _validate_optional_string(normalized, "save_as", source, line_number)
        _validate_optional_timeout_ms(normalized, source, line_number)
    elif action_name == "sleep":
        duration_ms = normalized.get("duration_ms")
        if not isinstance(duration_ms, int) or duration_ms < 0:
            raise _script_error(
                "sleep requires a non-negative integer 'duration_ms'",
                source,
                line_number,
            )
    elif action_name == "emit_raw":
        _require_string(normalized, "line", source, line_number)
        _validate_optional_delay_ms(normalized, source, line_number)
    elif action_name == "close":
        exit_code = normalized.get("exit_code", 0)
        if not isinstance(exit_code, int):
            raise _script_error("close requires an integer 'exit_code'", source, line_number)
        normalized["exit_code"] = exit_code
        _validate_optional_delay_ms(normalized, source, line_number)

    return normalized


def _require_string(
    action: JsonObject,
    key: str,
    source: Path | None,
    line_number: int | None,
) -> None:
    if not isinstance(action.get(key), str):
        raise _script_error(
            f"{action['action']} requires string field {key!r}",
            source,
            line_number,
        )


def _validate_optional_string(
    action: JsonObject,
    key: str,
    source: Path | None,
    line_number: int | None,
) -> None:
    value = action.get(key)
    if value is not None and not isinstance(value, str):
        raise _script_error(
            f"{action['action']} field {key!r} must be a string when present",
            source,
            line_number,
        )


def _validate_optional_json_rpc_id(
    action: JsonObject,
    key: str,
    source: Path | None,
    line_number: int | None,
) -> None:
    value = action.get(key, _UNSET)
    if value is _UNSET:
        return
    if value is None or isinstance(value, (str, int)):
        return
    raise _script_error(
        f"{action['action']} field {key!r} must be a JSON-RPC id when present",
        source,
        line_number,
    )


def _validate_optional_timeout_ms(
    action: JsonObject,
    source: Path | None,
    line_number: int | None,
) -> None:
    value = action.get("timeout_ms")
    if value is not None and (not isinstance(value, int) or value <= 0):
        raise _script_error(
            f"{action['action']} field 'timeout_ms' must be a positive integer when present",
            source,
            line_number,
        )


def _validate_optional_delay_ms(
    action: JsonObject,
    source: Path | None,
    line_number: int | None,
) -> None:
    value = action.get("delay_ms")
    if value is not None and (not isinstance(value, int) or value < 0):
        raise _script_error(
            f"{action['action']} field 'delay_ms' must be a non-negative integer when present",
            source,
            line_number,
        )


def _script_error(
    message: str,
    source: Path | None,
    line_number: int | None,
) -> FakeAppServerScriptError:
    if source is not None and line_number is not None:
        return FakeAppServerScriptError(f"{source}:{line_number}: {message}")
    return FakeAppServerScriptError(message)


async def _run_from_cli(script_path: Path, default_expect_timeout_ms: int) -> int:
    script = load_fake_app_server_script(script_path)
    reader = await _open_stdin_reader()
    writer = await _open_stdout_writer()
    server = FakeAppServer(script=script, default_expect_timeout_ms=default_expect_timeout_ms)
    return await server.run(reader=reader, writer=writer)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the fake app-server subprocess entry point."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--script",
        type=Path,
        required=True,
        help="Path to the JSONL fake app-server script to replay.",
    )
    parser.add_argument(
        "--default-timeout-ms",
        type=int,
        default=DEFAULT_EXPECT_TIMEOUT_MS,
        help="Default timeout for expect_* actions that do not set timeout_ms explicitly.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        return asyncio.run(_run_from_cli(args.script, args.default_timeout_ms))
    except (FakeAppServerRuntimeError, FakeAppServerScriptError) as exc:
        print(exc, file=sys.stderr)
        return 1


__all__ = [
    "DEFAULT_EXPECT_TIMEOUT_MS",
    "FakeAppServer",
    "FakeAppServerRuntimeError",
    "FakeAppServerScript",
    "FakeAppServerScriptError",
    "close_connection",
    "emit_invalid_json",
    "emit_raw",
    "expect_notification",
    "expect_request",
    "expect_response",
    "load_fake_app_server_script",
    "main",
    "send_notification",
    "send_response",
    "send_server_request",
    "sleep_action",
]


if __name__ == "__main__":
    raise SystemExit(main())

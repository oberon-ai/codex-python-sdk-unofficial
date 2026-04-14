"""Typed JSON-RPC envelope models and helpers.

The transport layer owns byte-oriented JSONL framing. This module owns the
first typed step above that boundary: turning one decoded JSON line into one
normalized JSON-RPC envelope object without yet imposing app-server-specific
schema models.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, TypeAlias, cast

from ..errors import MessageDecodeError

JSON_RPC_VERSION = "2.0"
JsonRpcId: TypeAlias = str | int | None
JsonRpcEnvelopeKind: TypeAlias = Literal[
    "request",
    "notification",
    "success_response",
    "error_response",
]


class JsonRpcEnvelope:
    """Base type for one normalized JSON-RPC envelope."""

    kind: JsonRpcEnvelopeKind
    jsonrpc = JSON_RPC_VERSION

    @property
    def request_id(self) -> JsonRpcId | None:
        return None

    @property
    def method_name(self) -> str | None:
        return None

    def to_wire_dict(self, *, include_jsonrpc: bool = False) -> dict[str, object]:
        """Return a JSON-serializable mapping for one envelope."""

        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class JsonRpcErrorObject:
    """Typed JSON-RPC error object payload."""

    code: int
    message: str
    data: object | None = None
    _data_present: bool = field(default=False, repr=False)

    @property
    def has_data(self) -> bool:
        return self._data_present

    def to_wire_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "message": self.message,
        }
        if self._data_present:
            payload["data"] = self.data
        return payload


@dataclass(frozen=True, slots=True)
class JsonRpcRequest(JsonRpcEnvelope):
    """Typed JSON-RPC request envelope with a request id."""

    id: JsonRpcId
    method: str
    params: object | None = None
    _params_present: bool = field(default=False, repr=False)
    kind: Literal["request"] = field(default="request", init=False)

    @property
    def request_id(self) -> JsonRpcId | None:
        return self.id

    @property
    def method_name(self) -> str | None:
        return self.method

    @property
    def has_params(self) -> bool:
        return self._params_present

    def to_wire_dict(self, *, include_jsonrpc: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "method": self.method,
        }
        if self._params_present:
            payload["params"] = self.params
        if include_jsonrpc:
            payload["jsonrpc"] = self.jsonrpc
        return payload


@dataclass(frozen=True, slots=True)
class JsonRpcNotification(JsonRpcEnvelope):
    """Typed JSON-RPC notification envelope without a request id."""

    method: str
    params: object | None = None
    _params_present: bool = field(default=False, repr=False)
    kind: Literal["notification"] = field(default="notification", init=False)

    @property
    def method_name(self) -> str | None:
        return self.method

    @property
    def has_params(self) -> bool:
        return self._params_present

    def to_wire_dict(self, *, include_jsonrpc: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {"method": self.method}
        if self._params_present:
            payload["params"] = self.params
        if include_jsonrpc:
            payload["jsonrpc"] = self.jsonrpc
        return payload


@dataclass(frozen=True, slots=True)
class JsonRpcSuccessResponse(JsonRpcEnvelope):
    """Typed JSON-RPC success response envelope."""

    id: JsonRpcId
    result: object | None = None
    kind: Literal["success_response"] = field(default="success_response", init=False)

    @property
    def request_id(self) -> JsonRpcId | None:
        return self.id

    def to_wire_dict(self, *, include_jsonrpc: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "result": self.result,
        }
        if include_jsonrpc:
            payload["jsonrpc"] = self.jsonrpc
        return payload


@dataclass(frozen=True, slots=True)
class JsonRpcErrorResponse(JsonRpcEnvelope):
    """Typed JSON-RPC error response envelope."""

    id: JsonRpcId
    error: JsonRpcErrorObject
    kind: Literal["error_response"] = field(default="error_response", init=False)

    @property
    def request_id(self) -> JsonRpcId | None:
        return self.id

    def to_wire_dict(self, *, include_jsonrpc: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "error": self.error.to_wire_dict(),
        }
        if include_jsonrpc:
            payload["jsonrpc"] = self.jsonrpc
        return payload


JsonRpcResponseEnvelope: TypeAlias = JsonRpcSuccessResponse | JsonRpcErrorResponse
JsonRpcEnvelopeLike: TypeAlias = JsonRpcEnvelope | Mapping[str, object]


def parse_jsonrpc_envelope(
    line: str,
    *,
    stderr_tail: str | None = None,
) -> JsonRpcEnvelope:
    """Parse one decoded JSONL line into a typed JSON-RPC envelope."""

    try:
        parsed = json.loads(line)
    except json.JSONDecodeError as exc:
        raise MessageDecodeError(
            line,
            original_error=exc,
            stderr_tail=stderr_tail,
        ) from exc

    try:
        return coerce_jsonrpc_envelope(parsed)
    except ValueError as exc:
        raise MessageDecodeError(
            line,
            original_error=exc,
            stderr_tail=stderr_tail,
        ) from exc


def coerce_jsonrpc_envelope(value: object) -> JsonRpcEnvelope:
    """Normalize one typed or raw JSON object into a JSON-RPC envelope model."""

    if isinstance(value, JsonRpcEnvelope):
        return value

    if not isinstance(value, Mapping):
        raise ValueError("JSON-RPC envelope must be a JSON object")

    payload = cast(Mapping[str, object], value)
    _validate_jsonrpc_version(payload)

    has_id = "id" in payload
    has_method = "method" in payload
    has_result = "result" in payload
    has_error = "error" in payload

    if has_method:
        method = payload.get("method")
        if not isinstance(method, str):
            raise ValueError("JSON-RPC request or notification method must be a string")
        if has_result or has_error:
            raise ValueError(
                "JSON-RPC request or notification envelopes cannot contain result or error"
            )

        params_present = "params" in payload
        params = payload.get("params")
        if has_id:
            request_id = _coerce_request_id(payload.get("id"))
            return JsonRpcRequest(
                id=request_id,
                method=method,
                params=params,
                _params_present=params_present,
            )
        return JsonRpcNotification(
            method=method,
            params=params,
            _params_present=params_present,
        )

    if not has_id:
        raise ValueError("JSON-RPC response envelope must include id")
    if has_result == has_error:
        raise ValueError("JSON-RPC response envelope must contain exactly one of result or error")

    request_id = _coerce_request_id(payload.get("id"))
    if has_result:
        return JsonRpcSuccessResponse(
            id=request_id,
            result=payload.get("result"),
        )

    error_payload = payload.get("error")
    if not isinstance(error_payload, Mapping):
        raise ValueError("JSON-RPC response error must be an object")

    return JsonRpcErrorResponse(
        id=request_id,
        error=_coerce_error_object(cast(Mapping[str, object], error_payload)),
    )


def serialize_jsonrpc_envelope(
    envelope: JsonRpcEnvelopeLike,
    *,
    include_jsonrpc: bool = False,
) -> str:
    """Serialize one JSON-RPC envelope to compact JSON text.

    The Codex wire convention omits the ``"jsonrpc":"2.0"`` member on the wire,
    so the serializer does the same by default while the typed models still keep
    the protocol version internally consistent.
    """

    normalized = coerce_jsonrpc_envelope(envelope)
    return json.dumps(
        normalized.to_wire_dict(include_jsonrpc=include_jsonrpc),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def classify_jsonrpc_envelope(envelope: JsonRpcEnvelopeLike) -> JsonRpcEnvelopeKind:
    """Return the normalized message family for one envelope."""

    return coerce_jsonrpc_envelope(envelope).kind


def is_jsonrpc_request_envelope(envelope: JsonRpcEnvelopeLike) -> bool:
    return classify_jsonrpc_envelope(envelope) == "request"


def is_jsonrpc_notification_envelope(envelope: JsonRpcEnvelopeLike) -> bool:
    return classify_jsonrpc_envelope(envelope) == "notification"


def is_jsonrpc_success_response_envelope(envelope: JsonRpcEnvelopeLike) -> bool:
    return classify_jsonrpc_envelope(envelope) == "success_response"


def is_jsonrpc_error_response_envelope(envelope: JsonRpcEnvelopeLike) -> bool:
    return classify_jsonrpc_envelope(envelope) == "error_response"


def is_jsonrpc_response_envelope(envelope: JsonRpcEnvelopeLike) -> bool:
    kind = classify_jsonrpc_envelope(envelope)
    return kind in ("success_response", "error_response")


def _validate_jsonrpc_version(payload: Mapping[str, object]) -> None:
    if "jsonrpc" not in payload:
        return

    version = payload.get("jsonrpc")
    if version != JSON_RPC_VERSION:
        raise ValueError(
            f"JSON-RPC envelope jsonrpc field must be {JSON_RPC_VERSION!r} when present"
        )


def _coerce_request_id(value: object) -> JsonRpcId:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("JSON-RPC id must be a string, integer, or null")
    if isinstance(value, str | int):
        return cast(JsonRpcId, value)
    raise ValueError("JSON-RPC id must be a string, integer, or null")


def _coerce_error_object(payload: Mapping[str, object]) -> JsonRpcErrorObject:
    code = payload.get("code")
    if isinstance(code, bool) or not isinstance(code, int):
        raise ValueError("JSON-RPC error object code must be an integer")

    message = payload.get("message")
    if not isinstance(message, str):
        raise ValueError("JSON-RPC error object message must be a string")

    data_present = "data" in payload
    data = payload.get("data")
    return JsonRpcErrorObject(
        code=code,
        message=message,
        data=data,
        _data_present=data_present,
    )


__all__ = [
    "JSON_RPC_VERSION",
    "JsonRpcEnvelope",
    "JsonRpcEnvelopeKind",
    "JsonRpcEnvelopeLike",
    "JsonRpcErrorObject",
    "JsonRpcErrorResponse",
    "JsonRpcId",
    "JsonRpcNotification",
    "JsonRpcRequest",
    "JsonRpcResponseEnvelope",
    "JsonRpcSuccessResponse",
    "classify_jsonrpc_envelope",
    "coerce_jsonrpc_envelope",
    "is_jsonrpc_error_response_envelope",
    "is_jsonrpc_notification_envelope",
    "is_jsonrpc_request_envelope",
    "is_jsonrpc_response_envelope",
    "is_jsonrpc_success_response_envelope",
    "parse_jsonrpc_envelope",
    "serialize_jsonrpc_envelope",
]

"""Typed protocol registry helpers layered over generated model indexes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

from pydantic import BaseModel

from ..generated.stable_notification_registry import (
    KNOWN_SERVER_NOTIFICATION_METHODS,
    SERVER_NOTIFICATION_METHODS,
    SERVER_NOTIFICATION_REGISTRY,
    StableNotificationRegistryEntry,
    get_server_notification_registry_entry,
)
from ..generated.stable_server_request_registry import (
    KNOWN_SERVER_REQUEST_METHODS,
    SERVER_REQUEST_METHODS,
    SERVER_REQUEST_REGISTRY,
    StableServerRequestRegistryEntry,
    get_server_request_registry_entry,
)
from ..rpc.jsonrpc import (
    JsonRpcEnvelopeLike,
    JsonRpcId,
    JsonRpcNotification,
    JsonRpcRequest,
    coerce_jsonrpc_envelope,
    is_jsonrpc_notification_envelope,
    is_jsonrpc_request_envelope,
)


@dataclass(frozen=True, slots=True)
class TypedServerNotification:
    """One server notification parsed into a generated payload model."""

    method: str
    envelope: JsonRpcNotification
    params: BaseModel
    params_model: type[BaseModel]
    envelope_model: type[BaseModel]


@dataclass(frozen=True, slots=True)
class RawServerNotification:
    """Controlled fallback for server notification methods the registry does not know."""

    method: str
    envelope: JsonRpcNotification
    fallback_reason: Literal["unknown_method"] = "unknown_method"

    @property
    def params(self) -> object | None:
        if self.envelope.has_params:
            return self.envelope.params
        return None


ServerNotificationParseResult: TypeAlias = TypedServerNotification | RawServerNotification


@dataclass(frozen=True, slots=True)
class TypedServerRequest:
    """One server-initiated request parsed into a typed params model."""

    method: str
    request_id: JsonRpcId
    envelope: JsonRpcRequest
    params: BaseModel
    params_model: type[BaseModel]


@dataclass(frozen=True, slots=True)
class RawServerRequest:
    """Controlled fallback for server request methods the registry does not know."""

    method: str
    request_id: JsonRpcId
    envelope: JsonRpcRequest
    fallback_reason: Literal["unknown_method"] = "unknown_method"

    @property
    def params(self) -> object | None:
        if self.envelope.has_params:
            return self.envelope.params
        return None


ServerRequestParseResult: TypeAlias = TypedServerRequest | RawServerRequest


def get_server_notification_entry(method: str) -> StableNotificationRegistryEntry | None:
    """Look up the generated registry entry for one server notification method."""

    return get_server_notification_registry_entry(method)


def is_known_server_notification_method(method: str) -> bool:
    """Return whether the stable notification registry knows this method."""

    return method in KNOWN_SERVER_NOTIFICATION_METHODS


def is_known_server_request_method(method: str) -> bool:
    """Return whether the stable server request registry knows this method."""

    return method in KNOWN_SERVER_REQUEST_METHODS


def parse_server_notification(
    notification: JsonRpcEnvelopeLike,
) -> ServerNotificationParseResult:
    """Parse one raw JSON-RPC notification into a typed or controlled raw notification."""

    envelope = coerce_jsonrpc_envelope(notification)
    if not is_jsonrpc_notification_envelope(envelope):
        raise TypeError(
            "Expected a JSON-RPC notification envelope when parsing a server notification."
        )
    notification_envelope = cast(JsonRpcNotification, envelope)

    entry = get_server_notification_entry(notification_envelope.method)
    if entry is None:
        return RawServerNotification(
            method=notification_envelope.method,
            envelope=notification_envelope,
        )

    typed_envelope = entry.envelope_model.model_validate(notification_envelope.to_wire_dict())
    params = getattr(typed_envelope, "params", None)
    if not isinstance(params, BaseModel):
        raise TypeError("Generated notification registry produced a non-BaseModel params payload.")

    return TypedServerNotification(
        method=notification_envelope.method,
        envelope=notification_envelope,
        params=params,
        params_model=entry.params_model,
        envelope_model=entry.envelope_model,
    )


def parse_server_request(request: JsonRpcEnvelopeLike) -> ServerRequestParseResult:
    """Parse one raw JSON-RPC request into a typed or controlled raw server request."""

    envelope = coerce_jsonrpc_envelope(request)
    if not is_jsonrpc_request_envelope(envelope):
        raise TypeError("Expected a JSON-RPC request envelope when parsing a server request.")
    request_envelope = cast(JsonRpcRequest, envelope)

    entry = get_server_request_entry(request_envelope.method)
    if entry is None:
        return RawServerRequest(
            method=request_envelope.method,
            request_id=request_envelope.request_id,
            envelope=request_envelope,
        )

    params = entry.params_model.model_validate(
        request_envelope.params if request_envelope.has_params else None
    )
    if not isinstance(params, BaseModel):
        raise TypeError(
            "Generated server request registry produced a non-BaseModel params payload."
        )

    return TypedServerRequest(
        method=request_envelope.method,
        request_id=request_envelope.request_id,
        envelope=request_envelope,
        params=params,
        params_model=entry.params_model,
    )


def get_server_request_entry(method: str) -> StableServerRequestRegistryEntry | None:
    """Look up the derived registry entry for one server request method."""

    return get_server_request_registry_entry(method)


__all__ = [
    "KNOWN_SERVER_NOTIFICATION_METHODS",
    "KNOWN_SERVER_REQUEST_METHODS",
    "RawServerNotification",
    "RawServerRequest",
    "SERVER_NOTIFICATION_METHODS",
    "SERVER_NOTIFICATION_REGISTRY",
    "SERVER_REQUEST_METHODS",
    "SERVER_REQUEST_REGISTRY",
    "ServerNotificationParseResult",
    "ServerRequestParseResult",
    "StableNotificationRegistryEntry",
    "StableServerRequestRegistryEntry",
    "TypedServerNotification",
    "TypedServerRequest",
    "get_server_notification_entry",
    "get_server_request_entry",
    "is_known_server_notification_method",
    "is_known_server_request_method",
    "parse_server_notification",
    "parse_server_request",
]

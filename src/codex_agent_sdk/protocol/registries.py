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
from ..rpc.jsonrpc import (
    JsonRpcEnvelopeLike,
    JsonRpcNotification,
    coerce_jsonrpc_envelope,
    is_jsonrpc_notification_envelope,
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


def get_server_notification_entry(method: str) -> StableNotificationRegistryEntry | None:
    """Look up the generated registry entry for one server notification method."""

    return get_server_notification_registry_entry(method)


def is_known_server_notification_method(method: str) -> bool:
    """Return whether the stable notification registry knows this method."""

    return method in KNOWN_SERVER_NOTIFICATION_METHODS


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


__all__ = [
    "KNOWN_SERVER_NOTIFICATION_METHODS",
    "RawServerNotification",
    "SERVER_NOTIFICATION_METHODS",
    "SERVER_NOTIFICATION_REGISTRY",
    "ServerNotificationParseResult",
    "StableNotificationRegistryEntry",
    "TypedServerNotification",
    "get_server_notification_entry",
    "is_known_server_notification_method",
    "parse_server_notification",
]

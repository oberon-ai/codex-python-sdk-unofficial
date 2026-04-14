"""Typed models for the app-server initialize handshake."""

from __future__ import annotations

from pydantic import ConfigDict, Field, ValidationError

from ..errors import ResponseValidationError
from .pydantic import WireModel


class InitializeServerCapabilities(WireModel):
    """Known server-declared capabilities returned from ``initialize``."""

    model_config = ConfigDict(
        populate_by_name=True,
        serialize_by_alias=True,
        extra="allow",
    )

    experimental_api: bool | None = Field(alias="experimentalApi", default=None)


class InitializeServerInfo(WireModel):
    """Server metadata returned from ``initialize``."""

    model_config = ConfigDict(
        populate_by_name=True,
        serialize_by_alias=True,
        extra="allow",
    )

    name: str
    title: str | None = None
    version: str


class InitializeResult(WireModel):
    """Validated result payload for the required initialize handshake."""

    model_config = ConfigDict(
        populate_by_name=True,
        serialize_by_alias=True,
        extra="allow",
    )

    protocol_version: int = Field(alias="protocolVersion")
    server_info: InitializeServerInfo | None = Field(alias="serverInfo", default=None)
    capabilities: InitializeServerCapabilities | None = None
    codex_home: str | None = Field(alias="codexHome", default=None)
    platform_family: str | None = Field(alias="platformFamily", default=None)
    platform_os: str | None = Field(alias="platformOs", default=None)
    user_agent: str | None = Field(alias="userAgent", default=None)


def parse_initialize_result(payload: object) -> InitializeResult:
    """Validate one raw initialize result payload."""

    try:
        return InitializeResult.model_validate(payload)
    except ValidationError as exc:
        raise ResponseValidationError(
            "initialize response payload failed validation",
            method="initialize",
            payload=payload,
        ) from exc


__all__ = [
    "InitializeResult",
    "InitializeServerCapabilities",
    "InitializeServerInfo",
    "parse_initialize_result",
]

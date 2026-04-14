"""Typed models for the app-server initialize handshake."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from .pydantic import WireModel, validate_response_payload


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

    return validate_response_payload(
        payload,
        method="initialize",
        response_model=InitializeResult,
    )


__all__ = [
    "InitializeResult",
    "InitializeServerCapabilities",
    "InitializeServerInfo",
    "parse_initialize_result",
]

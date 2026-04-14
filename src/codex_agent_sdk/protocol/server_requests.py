"""Handwritten server-request param models layered over generated stable types."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from ..generated import stable
from .pydantic import WireModel, WireRootModel


class ServerRequestParamsModel(WireModel):
    """Base model for server-request params that preserves forward-compatible extras."""

    model_config = ConfigDict(
        populate_by_name=True,
        serialize_by_alias=True,
        extra="allow",
    )


class ThreadTurnScopedServerRequestParams(ServerRequestParamsModel):
    thread_id: str = Field(alias="threadId")
    turn_id: str | None = Field(alias="turnId", default=None)


class ItemScopedServerRequestParams(ThreadTurnScopedServerRequestParams):
    item_id: str = Field(alias="itemId")


class RequestedFileSystemPermissions(ServerRequestParamsModel):
    read: list[str] | None = None
    write: list[str] | None = None


class RequestedPermissions(ServerRequestParamsModel):
    file_system: RequestedFileSystemPermissions | None = Field(
        alias="fileSystem",
        default=None,
    )
    network: dict[str, Any] | None = None


class CommandExecutionApprovalRequestParams(ItemScopedServerRequestParams):
    approval_id: str | None = Field(alias="approvalId", default=None)
    available_decisions: list[Any] | None = Field(alias="availableDecisions", default=None)
    command: list[str] | None = None
    command_actions: list[stable.CommandAction] | None = Field(
        alias="commandActions",
        default=None,
    )
    cwd: str | None = None
    additional_permissions: RequestedPermissions | None = Field(
        alias="additionalPermissions",
        default=None,
    )
    network_approval_context: dict[str, Any] | None = Field(
        alias="networkApprovalContext",
        default=None,
    )
    proposed_execpolicy_amendment: list[dict[str, Any]] | None = Field(
        alias="proposedExecpolicyAmendment",
        default=None,
    )
    proposed_network_policy_amendments: list[dict[str, Any]] | None = Field(
        alias="proposedNetworkPolicyAmendments",
        default=None,
    )
    reason: str | None = None


class FileChangeApprovalRequestParams(ItemScopedServerRequestParams):
    grant_root: str | None = Field(alias="grantRoot", default=None)
    reason: str | None = None


class PermissionsRequestApprovalParams(ItemScopedServerRequestParams):
    permissions: RequestedPermissions
    reason: str | None = None


class UserInputRequestOption(ServerRequestParamsModel):
    description: str
    label: str


class UserInputRequestQuestion(ServerRequestParamsModel):
    header: str
    id: str
    options: list[UserInputRequestOption] | None = None
    question: str


class ToolRequestUserInputParams(ThreadTurnScopedServerRequestParams):
    questions: list[UserInputRequestQuestion]


class McpServerElicitationFormRequestParams(ThreadTurnScopedServerRequestParams):
    message: str
    meta: dict[str, Any] | None = None
    mode: Literal["form"]
    requested_schema: dict[str, Any] = Field(alias="requestedSchema")
    server_name: str = Field(alias="serverName")


class McpServerElicitationUrlRequestParams(ThreadTurnScopedServerRequestParams):
    elicitation_id: str = Field(alias="elicitationId")
    message: str
    meta: dict[str, Any] | None = None
    mode: Literal["url"]
    server_name: str = Field(alias="serverName")
    url: str


class McpServerElicitationRequestParams(
    WireRootModel[McpServerElicitationFormRequestParams | McpServerElicitationUrlRequestParams]
):
    root: McpServerElicitationFormRequestParams | McpServerElicitationUrlRequestParams


class DynamicToolCallRequestParams(ThreadTurnScopedServerRequestParams):
    arguments: dict[str, Any]
    call_id: str = Field(alias="callId")
    tool: str


__all__ = [
    "CommandExecutionApprovalRequestParams",
    "DynamicToolCallRequestParams",
    "FileChangeApprovalRequestParams",
    "McpServerElicitationFormRequestParams",
    "McpServerElicitationRequestParams",
    "McpServerElicitationUrlRequestParams",
    "PermissionsRequestApprovalParams",
    "RequestedFileSystemPermissions",
    "RequestedPermissions",
    "ServerRequestParamsModel",
    "ToolRequestUserInputParams",
    "UserInputRequestOption",
    "UserInputRequestQuestion",
]

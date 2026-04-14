"""Public approval request models and adaptation helpers.

These handwritten models sit above the typed server-request registry so
application code can switch on approval request types without unpacking raw
dictionaries manually, while still preserving the original wire payload for
advanced use cases.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, TypeAlias, cast

from .errors import InvalidApprovalDecisionError
from .protocol.pydantic import dump_wire_value
from .protocol.registries import (
    RawServerRequest,
    ServerRequestParseResult,
    TypedServerRequest,
    parse_server_request,
)
from .protocol.server_requests import (
    CommandExecutionApprovalRequestParams,
    FileChangeApprovalRequestParams,
    PermissionsRequestApprovalParams,
    RequestedFileSystemPermissions,
    RequestedPermissions,
)
from .rpc.jsonrpc import JsonRpcEnvelopeLike, JsonRpcId, JsonRpcRequest

_EMPTY_MAPPING: Mapping[str, object] = MappingProxyType({})

ApprovalRequestKind: TypeAlias = Literal["command_execution", "file_change", "permissions"]
ApprovalResponseKind: TypeAlias = Literal["decision", "permission_grant"]
PermissionGrantScope: TypeAlias = Literal["turn", "session"]

_DEFAULT_COMMAND_DECISIONS = ("accept", "acceptForSession", "decline", "cancel")
_DEFAULT_FILE_CHANGE_DECISIONS = ("accept", "acceptForSession", "decline", "cancel")
_DEFAULT_PERMISSION_SCOPES: tuple[PermissionGrantScope, ...] = ("turn", "session")


def _freeze_mapping(value: Mapping[str, object] | None) -> Mapping[str, object]:
    if value is None:
        return _EMPTY_MAPPING
    return MappingProxyType(dict(value))


def _copy_wire_payload(value: object | None) -> Mapping[str, object]:
    if value is None:
        return _EMPTY_MAPPING
    if not isinstance(value, Mapping):
        raise TypeError("approval request payloads must be JSON object mappings")
    copied: dict[str, object] = {str(key): cast(object, item) for key, item in value.items()}
    return MappingProxyType(copied)


def _freeze_mapping_sequence(
    values: Sequence[Mapping[str, object]] | None,
) -> tuple[Mapping[str, object], ...]:
    if values is None:
        return ()
    return tuple(_freeze_mapping(value) for value in values)


def _normalize_available_decision_name(raw_value: object) -> str | None:
    if isinstance(raw_value, str):
        return raw_value
    if isinstance(raw_value, Mapping):
        keys = [key for key in raw_value if isinstance(key, str)]
        if len(keys) == 1:
            return keys[0]
    return None


def _normalize_available_decisions(
    raw_values: Sequence[object] | None,
    *,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    if raw_values is None:
        return default

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        normalized_name = _normalize_available_decision_name(raw_value)
        if normalized_name is None or normalized_name in seen:
            continue
        normalized.append(normalized_name)
        seen.add(normalized_name)

    if not normalized:
        return default
    return tuple(normalized)


def _default_command_available_decisions(
    params: CommandExecutionApprovalRequestParams,
) -> tuple[str, ...]:
    decisions: list[str] = ["accept", "acceptForSession"]
    if params.proposed_execpolicy_amendment:
        decisions.append("acceptWithExecpolicyAmendment")
    if params.proposed_network_policy_amendments:
        decisions.append("applyNetworkPolicyAmendment")
    decisions.extend(("decline", "cancel"))
    return tuple(decisions)


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalCommandAction:
    """Normalized command-action summary for approval UIs and callbacks."""

    kind: str
    command: str
    name: str | None = None
    path: str | None = None
    query: str | None = None
    payload: Mapping[str, object] = field(default_factory=lambda: _EMPTY_MAPPING)


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalFileChange:
    """One normalized proposed file change, when the request payload includes it."""

    path: str
    kind: str
    diff: str
    payload: Mapping[str, object] = field(default_factory=lambda: _EMPTY_MAPPING)


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalFileSystemPermissions:
    """Normalized filesystem permission request details."""

    read_paths: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()
    payload: Mapping[str, object] = field(default_factory=lambda: _EMPTY_MAPPING)


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalPermissions:
    """Normalized additional-permissions profile with the raw payload preserved."""

    file_system: ApprovalFileSystemPermissions | None = None
    network: Mapping[str, object] | None = None
    payload: Mapping[str, object] = field(default_factory=lambda: _EMPTY_MAPPING)


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalDecision:
    """Structured approval response sent back to the app-server."""

    decision: str | Mapping[str, object] | None = None
    permissions: ApprovalPermissions | None = None
    scope: PermissionGrantScope | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.decision is None and self.permissions is None:
            raise ValueError("approval decisions must include either decision or permissions")
        if self.decision is not None and self.permissions is not None:
            raise ValueError("approval decisions cannot include both decision and permissions")
        if self.permissions is None and self.scope is not None:
            raise ValueError("approval decision scope requires permissions to be present")

    def as_wire_result(self) -> Mapping[str, object]:
        """Render the decision in the JSON-RPC result shape expected by app-server."""

        if self.permissions is not None:
            payload: dict[str, object] = {"permissions": dict(self.permissions.payload)}
            if self.scope is not None:
                payload["scope"] = self.scope
            return MappingProxyType(payload)

        payload = {"decision": self.decision}
        if self.reason is not None:
            payload["reason"] = self.reason
        return MappingProxyType(payload)

    @classmethod
    def accept(cls) -> ApprovalDecision:
        """Create a simple accept decision."""

        return cls(decision="accept")

    @classmethod
    def accept_for_session(cls) -> ApprovalDecision:
        """Create a session-scoped accept decision when the server supports it."""

        return cls(decision="acceptForSession")

    @classmethod
    def decline(cls) -> ApprovalDecision:
        """Create a decline decision."""

        return cls(decision="decline")

    @classmethod
    def cancel(cls) -> ApprovalDecision:
        """Create a cancel decision."""

        return cls(decision="cancel")

    @classmethod
    def grant_permissions(
        cls,
        permissions: ApprovalPermissions,
        *,
        scope: PermissionGrantScope | None = None,
    ) -> ApprovalDecision:
        """Create a permission-grant response for `item/permissions/requestApproval`."""

        return cls(permissions=permissions, scope=scope)


ApprovalResponder: TypeAlias = Callable[[ApprovalDecision], Awaitable[None]]
ApprovalHandlerResult: TypeAlias = ApprovalDecision | None
ApprovalHandler: TypeAlias = Callable[["ApprovalRequest"], Awaitable[ApprovalHandlerResult]]


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalRequest:
    """Typed approval request surfaced from server-initiated JSON-RPC requests."""

    request_id: JsonRpcId
    thread_id: str
    turn_id: str | None
    item_id: str
    kind: ApprovalRequestKind
    method: str
    response_kind: ApprovalResponseKind
    reason: str | None
    payload: Mapping[str, object]
    request_envelope: JsonRpcRequest
    _responder: ApprovalResponder | None = field(default=None, repr=False, compare=False)

    async def respond(self, decision: ApprovalDecision) -> None:
        """Send an approval decision back to the waiting turn."""

        if not isinstance(decision, ApprovalDecision):
            raise InvalidApprovalDecisionError(decision)
        if self._responder is None:
            raise NotImplementedError(
                "Approval request responses are not wired until the client layer exists."
            )
        await self._responder(decision)


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandApprovalRequest(ApprovalRequest):
    """Command execution approval request with normalized command details."""

    kind: Literal["command_execution"] = "command_execution"
    method: Literal["item/commandExecution/requestApproval"] = (
        "item/commandExecution/requestApproval"
    )
    response_kind: Literal["decision"] = "decision"
    params: CommandExecutionApprovalRequestParams
    approval_id: str | None = None
    available_decisions: tuple[str, ...] = ()
    raw_available_decisions: tuple[object, ...] = ()
    command: tuple[str, ...] | None = None
    cwd: str | None = None
    command_actions: tuple[ApprovalCommandAction, ...] = ()
    additional_permissions: ApprovalPermissions | None = None
    network_approval_context: Mapping[str, object] | None = None
    proposed_execpolicy_amendment: tuple[Mapping[str, object], ...] = ()
    proposed_network_policy_amendments: tuple[Mapping[str, object], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class FileChangeApprovalRequest(ApprovalRequest):
    """File-change approval request with best-effort diff details when present."""

    kind: Literal["file_change"] = "file_change"
    method: Literal["item/fileChange/requestApproval"] = "item/fileChange/requestApproval"
    response_kind: Literal["decision"] = "decision"
    params: FileChangeApprovalRequestParams
    available_decisions: tuple[str, ...] = _DEFAULT_FILE_CHANGE_DECISIONS
    grant_root: str | None = None
    changes: tuple[ApprovalFileChange, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class PermissionsApprovalRequest(ApprovalRequest):
    """Permission approval request with normalized requested-permission details."""

    kind: Literal["permissions"] = "permissions"
    method: Literal["item/permissions/requestApproval"] = "item/permissions/requestApproval"
    response_kind: Literal["permission_grant"] = "permission_grant"
    params: PermissionsRequestApprovalParams
    permissions: ApprovalPermissions
    available_scopes: tuple[PermissionGrantScope, ...] = _DEFAULT_PERMISSION_SCOPES


ApprovalRequestSource: TypeAlias = ApprovalRequest | ServerRequestParseResult | JsonRpcEnvelopeLike


def _adapt_command_action(action: object) -> ApprovalCommandAction:
    wire_payload_object = dump_wire_value(action)
    if not isinstance(wire_payload_object, Mapping):
        raise TypeError("command actions must serialize to JSON object mappings")

    wire_payload = _freeze_mapping(cast(Mapping[str, object], wire_payload_object))
    kind = wire_payload.get("type")
    command = wire_payload.get("command")
    if not isinstance(kind, str) or not isinstance(command, str):
        raise TypeError("command approval action payload is missing required string fields")

    name = wire_payload.get("name")
    path = wire_payload.get("path")
    query = wire_payload.get("query")

    return ApprovalCommandAction(
        kind=kind,
        command=command,
        name=name if isinstance(name, str) else None,
        path=path if isinstance(path, str) else None,
        query=query if isinstance(query, str) else None,
        payload=wire_payload,
    )


def _adapt_file_change(change: object) -> ApprovalFileChange | None:
    if not isinstance(change, Mapping):
        return None

    payload = _freeze_mapping(cast(Mapping[str, object], change))
    path = payload.get("path")
    kind = payload.get("kind")
    diff = payload.get("diff")
    if not isinstance(path, str) or not isinstance(kind, str) or not isinstance(diff, str):
        return None

    return ApprovalFileChange(path=path, kind=kind, diff=diff, payload=payload)


def _adapt_filesystem_permissions(
    permissions: RequestedFileSystemPermissions | None,
) -> ApprovalFileSystemPermissions | None:
    if permissions is None:
        return None

    payload_object = dump_wire_value(permissions)
    payload = _copy_wire_payload(payload_object)
    read_paths = tuple(path for path in permissions.read or () if isinstance(path, str))
    write_paths = tuple(path for path in permissions.write or () if isinstance(path, str))

    return ApprovalFileSystemPermissions(
        read_paths=read_paths,
        write_paths=write_paths,
        payload=payload,
    )


def _adapt_permissions(permissions: RequestedPermissions | None) -> ApprovalPermissions | None:
    if permissions is None:
        return None

    payload_object = dump_wire_value(permissions)
    payload = _copy_wire_payload(payload_object)
    network_payload = (
        _freeze_mapping(permissions.network) if permissions.network is not None else None
    )

    return ApprovalPermissions(
        file_system=_adapt_filesystem_permissions(permissions.file_system),
        network=network_payload,
        payload=payload,
    )


def _coerce_parsed_request(
    request: ApprovalRequestSource,
) -> ServerRequestParseResult | ApprovalRequest:
    if isinstance(request, ApprovalRequest):
        return request
    if isinstance(request, (TypedServerRequest, RawServerRequest)):
        return request
    return parse_server_request(request)


def adapt_approval_request(
    request: ApprovalRequestSource,
    *,
    responder: ApprovalResponder | None = None,
) -> ApprovalRequest | None:
    """Adapt one server request into a typed high-level approval request.

    Non-approval server requests return ``None`` so callers can keep using the
    same adapter entrypoint on a mixed server-request stream.
    """

    parsed_request = _coerce_parsed_request(request)
    if isinstance(parsed_request, ApprovalRequest):
        return parsed_request
    if isinstance(parsed_request, RawServerRequest):
        return None

    payload = _copy_wire_payload(parsed_request.envelope.params)

    if parsed_request.method == "item/commandExecution/requestApproval":
        command_params = cast(CommandExecutionApprovalRequestParams, parsed_request.params)
        raw_available_decisions = tuple(command_params.available_decisions or ())
        available_decisions = _normalize_available_decisions(
            raw_available_decisions,
            default=_default_command_available_decisions(command_params),
        )
        network_context = (
            _freeze_mapping(command_params.network_approval_context)
            if command_params.network_approval_context is not None
            else None
        )
        return CommandApprovalRequest(
            request_id=parsed_request.request_id,
            thread_id=command_params.thread_id,
            turn_id=command_params.turn_id,
            item_id=command_params.item_id,
            reason=command_params.reason,
            payload=payload,
            request_envelope=parsed_request.envelope,
            params=command_params,
            approval_id=command_params.approval_id,
            available_decisions=available_decisions,
            raw_available_decisions=raw_available_decisions,
            command=tuple(command_params.command) if command_params.command is not None else None,
            cwd=command_params.cwd,
            command_actions=tuple(
                _adapt_command_action(action) for action in command_params.command_actions or ()
            ),
            additional_permissions=_adapt_permissions(command_params.additional_permissions),
            network_approval_context=network_context,
            proposed_execpolicy_amendment=_freeze_mapping_sequence(
                command_params.proposed_execpolicy_amendment
            ),
            proposed_network_policy_amendments=_freeze_mapping_sequence(
                command_params.proposed_network_policy_amendments
            ),
            _responder=responder,
        )

    if parsed_request.method == "item/fileChange/requestApproval":
        file_change_params = cast(FileChangeApprovalRequestParams, parsed_request.params)
        changes_payload = payload.get("changes")
        changes: list[ApprovalFileChange] = []
        if isinstance(changes_payload, Sequence) and not isinstance(changes_payload, (str, bytes)):
            for raw_change in changes_payload:
                adapted_change = _adapt_file_change(raw_change)
                if adapted_change is not None:
                    changes.append(adapted_change)
        return FileChangeApprovalRequest(
            request_id=parsed_request.request_id,
            thread_id=file_change_params.thread_id,
            turn_id=file_change_params.turn_id,
            item_id=file_change_params.item_id,
            reason=file_change_params.reason,
            payload=payload,
            request_envelope=parsed_request.envelope,
            params=file_change_params,
            grant_root=file_change_params.grant_root,
            changes=tuple(changes),
            _responder=responder,
        )

    if parsed_request.method == "item/permissions/requestApproval":
        permissions_params = cast(PermissionsRequestApprovalParams, parsed_request.params)
        permissions = _adapt_permissions(permissions_params.permissions)
        assert permissions is not None
        return PermissionsApprovalRequest(
            request_id=parsed_request.request_id,
            thread_id=permissions_params.thread_id,
            turn_id=permissions_params.turn_id,
            item_id=permissions_params.item_id,
            reason=permissions_params.reason,
            payload=payload,
            request_envelope=parsed_request.envelope,
            params=permissions_params,
            permissions=permissions,
            _responder=responder,
        )

    return None


__all__ = [
    "ApprovalCommandAction",
    "ApprovalDecision",
    "ApprovalFileChange",
    "ApprovalFileSystemPermissions",
    "ApprovalHandler",
    "ApprovalHandlerResult",
    "ApprovalPermissions",
    "ApprovalRequest",
    "CommandApprovalRequest",
    "FileChangeApprovalRequest",
    "PermissionGrantScope",
    "PermissionsApprovalRequest",
    "adapt_approval_request",
]

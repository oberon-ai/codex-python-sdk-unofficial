from __future__ import annotations

from typing import cast

import pytest

from codex_agent_sdk import (
    ApprovalDecision,
    ApprovalFileSystemPermissions,
    ApprovalPermissions,
    CommandApprovalRequest,
    FileChangeApprovalRequest,
    PermissionsApprovalRequest,
    adapt_approval_request,
)
from codex_agent_sdk.protocol.registries import parse_server_request
from codex_agent_sdk.rpc import JsonRpcRequest


def build_command_approval_request() -> JsonRpcRequest:
    return JsonRpcRequest(
        id="approval-command-1",
        method="item/commandExecution/requestApproval",
        params={
            "threadId": "thread_123",
            "turnId": "turn_123",
            "itemId": "item_123",
            "approvalId": "approval_subcommand_1",
            "command": ["pytest", "-q"],
            "cwd": "/repo",
            "reason": "Run the test suite.",
            "commandActions": [
                {
                    "type": "search",
                    "command": "rg -n failing",
                    "query": "failing",
                }
            ],
            "additionalPermissions": {
                "fileSystem": {
                    "write": ["/repo/.pytest_cache"],
                },
                "network": {"enabled": True},
            },
            "networkApprovalContext": {
                "host": "example.com",
                "reason": "Download a fixture",
            },
            "proposedExecpolicyAmendment": [
                {"tool": "shell", "mode": "allow", "pattern": "pytest *"}
            ],
            "proposedNetworkPolicyAmendments": [{"host": "example.com", "action": "allow"}],
            "availableDecisions": [
                "accept",
                {
                    "applyNetworkPolicyAmendment": {
                        "network_policy_amendment": {"host": "example.com"}
                    }
                },
                "decline",
            ],
        },
        _params_present=True,
    )


def build_file_change_approval_request() -> JsonRpcRequest:
    return JsonRpcRequest(
        id="approval-file-1",
        method="item/fileChange/requestApproval",
        params={
            "threadId": "thread_123",
            "turnId": "turn_123",
            "itemId": "item_456",
            "reason": "Apply the proposed patch.",
            "grantRoot": "/repo",
            "changes": [
                {
                    "path": "src/example.py",
                    "kind": "modified",
                    "diff": "@@ -1 +1 @@\n-print('old')\n+print('new')\n",
                }
            ],
        },
        _params_present=True,
    )


def build_permissions_approval_request() -> JsonRpcRequest:
    return JsonRpcRequest(
        id="approval-permissions-1",
        method="item/permissions/requestApproval",
        params={
            "threadId": "thread_123",
            "turnId": "turn_123",
            "itemId": "item_789",
            "reason": "Select a workspace root.",
            "permissions": {
                "fileSystem": {
                    "read": ["/repo"],
                    "write": ["/repo", "/shared"],
                },
                "network": {"enabled": True},
            },
        },
        _params_present=True,
    )


def build_dynamic_tool_request() -> JsonRpcRequest:
    return JsonRpcRequest(
        id="tool-call-1",
        method="item/tool/call",
        params={
            "threadId": "thread_123",
            "turnId": "turn_123",
            "callId": "call_123",
            "tool": "lookup_ticket",
            "arguments": {"id": "ABC-123"},
        },
        _params_present=True,
    )


def test_adapt_command_approval_request_normalizes_command_details() -> None:
    approval = adapt_approval_request(build_command_approval_request())

    assert isinstance(approval, CommandApprovalRequest)
    assert approval.kind == "command_execution"
    assert approval.response_kind == "decision"
    assert approval.thread_id == "thread_123"
    assert approval.turn_id == "turn_123"
    assert approval.item_id == "item_123"
    assert approval.reason == "Run the test suite."
    assert approval.approval_id == "approval_subcommand_1"
    assert approval.command == ("pytest", "-q")
    assert approval.cwd == "/repo"
    assert approval.available_decisions == (
        "accept",
        "applyNetworkPolicyAmendment",
        "decline",
    )
    assert approval.raw_available_decisions[0] == "accept"
    assert approval.command_actions[0].kind == "search"
    assert approval.command_actions[0].query == "failing"
    assert approval.additional_permissions is not None
    assert approval.additional_permissions.file_system is not None
    assert approval.additional_permissions.file_system.write_paths == ("/repo/.pytest_cache",)
    assert approval.additional_permissions.network == {"enabled": True}
    assert approval.network_approval_context == {
        "host": "example.com",
        "reason": "Download a fixture",
    }
    assert approval.proposed_execpolicy_amendment[0]["tool"] == "shell"
    assert approval.proposed_network_policy_amendments[0]["host"] == "example.com"
    assert approval.payload["command"] == ["pytest", "-q"]
    assert approval.request_envelope.method == "item/commandExecution/requestApproval"


def test_adapt_file_change_approval_request_keeps_best_effort_diff_details() -> None:
    approval = adapt_approval_request(build_file_change_approval_request())

    assert isinstance(approval, FileChangeApprovalRequest)
    assert approval.kind == "file_change"
    assert approval.response_kind == "decision"
    assert approval.available_decisions == (
        "accept",
        "acceptForSession",
        "decline",
        "cancel",
    )
    assert approval.grant_root == "/repo"
    assert len(approval.changes) == 1
    assert approval.changes[0].path == "src/example.py"
    assert approval.changes[0].kind == "modified"
    assert "@@ -1 +1 @@" in approval.changes[0].diff
    raw_changes = cast(list[dict[str, object]], approval.payload["changes"])
    assert raw_changes[0]["path"] == "src/example.py"


def test_adapt_permissions_approval_request_normalizes_requested_permissions() -> None:
    parsed = parse_server_request(build_permissions_approval_request())
    approval = adapt_approval_request(parsed)

    assert isinstance(approval, PermissionsApprovalRequest)
    assert approval.kind == "permissions"
    assert approval.response_kind == "permission_grant"
    assert approval.available_scopes == ("turn", "session")
    assert approval.reason == "Select a workspace root."
    assert approval.permissions.file_system is not None
    assert approval.permissions.file_system.read_paths == ("/repo",)
    assert approval.permissions.file_system.write_paths == ("/repo", "/shared")
    assert approval.permissions.network == {"enabled": True}
    raw_permissions = cast(dict[str, object], approval.payload["permissions"])
    raw_file_system = cast(dict[str, object], raw_permissions["fileSystem"])
    assert raw_file_system["write"] == ["/repo", "/shared"]


def test_adapt_approval_request_returns_none_for_non_approval_server_requests() -> None:
    assert adapt_approval_request(build_dynamic_tool_request()) is None


@pytest.mark.asyncio
async def test_approval_request_respond_forwards_permission_grant_decision() -> None:
    captured: list[ApprovalDecision] = []

    async def _responder(decision: ApprovalDecision) -> None:
        captured.append(decision)

    approval = adapt_approval_request(build_permissions_approval_request(), responder=_responder)

    assert isinstance(approval, PermissionsApprovalRequest)
    await approval.respond(
        ApprovalDecision.grant_permissions(
            ApprovalPermissions(
                file_system=ApprovalFileSystemPermissions(
                    write_paths=("/repo",),
                    payload={"write": ["/repo"]},
                ),
                payload={"fileSystem": {"write": ["/repo"]}},
            ),
            scope="session",
        )
    )

    assert len(captured) == 1
    assert captured[0].as_wire_result() == {
        "scope": "session",
        "permissions": {"fileSystem": {"write": ["/repo"]}},
    }


def test_simple_approval_decision_renders_wire_result() -> None:
    decision = ApprovalDecision.accept_for_session()

    assert decision.as_wire_result() == {"decision": "acceptForSession"}


def test_decision_and_permissions_are_mutually_exclusive() -> None:
    permissions = ApprovalPermissions(payload={"fileSystem": {"write": ["/repo"]}})

    with pytest.raises(ValueError):
        ApprovalDecision(decision="accept", permissions=permissions)


def test_decision_scope_requires_permissions() -> None:
    with pytest.raises(ValueError):
        ApprovalDecision(decision="accept", scope="session")

from __future__ import annotations

import unittest
from typing import Any

from pydantic import ValidationError

from codex_agent_sdk.generated import stable
from codex_agent_sdk.protocol.registries import (
    RawServerNotification,
    RawServerRequest,
    TypedServerNotification,
    TypedServerRequest,
    get_server_notification_entry,
    get_server_request_entry,
    is_known_server_notification_method,
    is_known_server_request_method,
    parse_server_notification,
    parse_server_request,
)
from codex_agent_sdk.protocol.server_requests import (
    CommandExecutionApprovalRequestParams,
    DynamicToolCallRequestParams,
    FileChangeApprovalRequestParams,
    McpServerElicitationRequestParams,
    PermissionsRequestApprovalParams,
    ToolRequestUserInputParams,
)
from codex_agent_sdk.rpc import JsonRpcNotification, JsonRpcRequest


def build_thread_payload() -> dict[str, object]:
    return {
        "cliVersion": "codex-cli 0.118.0",
        "createdAt": 1_710_000_000,
        "cwd": "/repo",
        "ephemeral": False,
        "id": "thread_123",
        "modelProvider": "openai",
        "preview": "Find the smallest failing test.",
        "source": "appServer",
        "status": {"type": "idle"},
        "turns": [],
        "updatedAt": 1_710_000_001,
    }


def build_turn_payload(*, status: str) -> dict[str, object]:
    return {
        "id": "turn_123",
        "items": [],
        "status": status,
    }


def build_agent_message_item_payload() -> dict[str, object]:
    return {
        "id": "item_123",
        "text": "Working on it.",
        "type": "agentMessage",
    }


def build_command_approval_payload() -> dict[str, object]:
    return {
        "threadId": "thread_123",
        "turnId": "turn_123",
        "itemId": "item_123",
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
            }
        },
        "availableDecisions": ["accept", "decline"],
    }


def build_file_change_approval_payload() -> dict[str, object]:
    return {
        "threadId": "thread_123",
        "turnId": "turn_123",
        "itemId": "item_456",
        "reason": "Apply the proposed patch.",
        "grantRoot": "/repo",
    }


def build_permissions_approval_payload() -> dict[str, object]:
    return {
        "threadId": "thread_123",
        "turnId": "turn_123",
        "itemId": "item_789",
        "reason": "Select a workspace root.",
        "permissions": {
            "fileSystem": {
                "write": ["/repo", "/shared"],
            }
        },
    }


def build_user_input_request_payload() -> dict[str, object]:
    return {
        "threadId": "thread_123",
        "turnId": "turn_123",
        "questions": [
            {
                "id": "ticket",
                "header": "Ticket",
                "question": "Which ticket should I inspect?",
                "options": [
                    {
                        "label": "ABC-123",
                        "description": "The failing CI ticket.",
                    }
                ],
            }
        ],
    }


def build_mcp_elicitation_payload() -> dict[str, object]:
    return {
        "threadId": "thread_123",
        "turnId": None,
        "serverName": "github",
        "mode": "form",
        "message": "Authorize the MCP tool call.",
        "requestedSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
            },
            "required": ["token"],
        },
        "meta": {"codex_approval_kind": "mcp_tool_call"},
    }


def build_dynamic_tool_call_payload() -> dict[str, object]:
    return {
        "threadId": "thread_123",
        "turnId": "turn_123",
        "callId": "call_123",
        "tool": "lookup_ticket",
        "arguments": {"id": "ABC-123"},
    }


class ProtocolRegistriesTests(unittest.TestCase):
    def test_known_server_notification_methods_have_registry_entries(self) -> None:
        representative_methods = (
            "thread/started",
            "turn/started",
            "item/started",
            "item/agentMessage/delta",
            "turn/completed",
        )

        for method in representative_methods:
            with self.subTest(method=method):
                self.assertTrue(is_known_server_notification_method(method))
                self.assertIsNotNone(get_server_notification_entry(method))

    def test_parse_server_notification_returns_typed_models_for_known_methods(self) -> None:
        cases: tuple[tuple[str, dict[str, Any], type[Any]], ...] = (
            (
                "thread/started",
                {"thread": build_thread_payload()},
                stable.ThreadStartedNotification,
            ),
            (
                "turn/started",
                {
                    "threadId": "thread_123",
                    "turn": build_turn_payload(status="inProgress"),
                },
                stable.TurnStartedNotification,
            ),
            (
                "item/started",
                {
                    "threadId": "thread_123",
                    "turnId": "turn_123",
                    "item": build_agent_message_item_payload(),
                },
                stable.ItemStartedNotification,
            ),
            (
                "item/agentMessage/delta",
                {
                    "delta": "hello",
                    "itemId": "item_123",
                    "threadId": "thread_123",
                    "turnId": "turn_123",
                },
                stable.AgentMessageDeltaNotification,
            ),
            (
                "turn/completed",
                {
                    "threadId": "thread_123",
                    "turn": build_turn_payload(status="completed"),
                },
                stable.TurnCompletedNotification,
            ),
        )

        for method, params, expected_params_model in cases:
            with self.subTest(method=method):
                parsed = parse_server_notification({"method": method, "params": params})
                self.assertIsInstance(parsed, TypedServerNotification)
                assert isinstance(parsed, TypedServerNotification)
                self.assertEqual(parsed.method, method)
                self.assertIs(parsed.params_model, expected_params_model)
                self.assertIsInstance(parsed.params, expected_params_model)
                self.assertEqual(
                    parsed.envelope.to_wire_dict(),
                    {"method": method, "params": params},
                )

    def test_parse_server_notification_accepts_typed_jsonrpc_notification_envelope(self) -> None:
        envelope = JsonRpcNotification(
            method="item/agentMessage/delta",
            params={
                "delta": "partial text",
                "itemId": "item_123",
                "threadId": "thread_123",
                "turnId": "turn_123",
            },
            _params_present=True,
        )

        parsed = parse_server_notification(envelope)

        self.assertIsInstance(parsed, TypedServerNotification)
        assert isinstance(parsed, TypedServerNotification)
        self.assertIsInstance(parsed.params, stable.AgentMessageDeltaNotification)
        typed_params = parsed.params
        assert isinstance(typed_params, stable.AgentMessageDeltaNotification)
        self.assertIs(parsed.envelope, envelope)
        self.assertEqual(typed_params.delta, "partial text")
        self.assertEqual(typed_params.item_id, "item_123")

    def test_parse_server_notification_uses_raw_fallback_for_unknown_methods(self) -> None:
        parsed = parse_server_notification(
            {"method": "future/notification", "params": {"opaque": True}}
        )

        self.assertIsInstance(parsed, RawServerNotification)
        assert isinstance(parsed, RawServerNotification)
        self.assertEqual(parsed.method, "future/notification")
        self.assertEqual(parsed.fallback_reason, "unknown_method")
        self.assertEqual(parsed.params, {"opaque": True})

    def test_parse_server_notification_rejects_non_notification_envelopes(self) -> None:
        with self.assertRaises(TypeError):
            parse_server_notification(
                {
                    "id": 7,
                    "method": "turn/start",
                    "params": {"threadId": "thread_123", "input": []},
                }
            )

    def test_known_methods_do_not_fall_back_raw_on_invalid_payloads(self) -> None:
        with self.assertRaises(ValidationError):
            parse_server_notification(
                {
                    "method": "turn/started",
                    "params": {"threadId": "thread_123"},
                }
            )

    def test_known_server_request_methods_have_registry_entries(self) -> None:
        representative_methods = (
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
            "item/tool/requestUserInput",
            "mcpServer/elicitation/request",
            "item/tool/call",
        )

        for method in representative_methods:
            with self.subTest(method=method):
                self.assertTrue(is_known_server_request_method(method))
                self.assertIsNotNone(get_server_request_entry(method))

    def test_parse_server_request_returns_typed_models_for_known_methods(self) -> None:
        cases: tuple[tuple[str, dict[str, Any], type[Any]], ...] = (
            (
                "item/commandExecution/requestApproval",
                build_command_approval_payload(),
                CommandExecutionApprovalRequestParams,
            ),
            (
                "item/fileChange/requestApproval",
                build_file_change_approval_payload(),
                FileChangeApprovalRequestParams,
            ),
            (
                "item/permissions/requestApproval",
                build_permissions_approval_payload(),
                PermissionsRequestApprovalParams,
            ),
            (
                "item/tool/requestUserInput",
                build_user_input_request_payload(),
                ToolRequestUserInputParams,
            ),
            (
                "mcpServer/elicitation/request",
                build_mcp_elicitation_payload(),
                McpServerElicitationRequestParams,
            ),
            (
                "item/tool/call",
                build_dynamic_tool_call_payload(),
                DynamicToolCallRequestParams,
            ),
        )

        for method, params, expected_params_model in cases:
            with self.subTest(method=method):
                parsed = parse_server_request(
                    {"id": f"{method}-request", "method": method, "params": params}
                )
                self.assertIsInstance(parsed, TypedServerRequest)
                assert isinstance(parsed, TypedServerRequest)
                self.assertEqual(parsed.method, method)
                self.assertEqual(parsed.request_id, f"{method}-request")
                self.assertIs(parsed.params_model, expected_params_model)
                self.assertIsInstance(parsed.params, expected_params_model)
                self.assertEqual(
                    parsed.envelope.to_wire_dict(),
                    {"id": f"{method}-request", "method": method, "params": params},
                )

    def test_parse_server_request_accepts_typed_jsonrpc_request_envelope(self) -> None:
        envelope = JsonRpcRequest(
            id="approval-1",
            method="item/commandExecution/requestApproval",
            params=build_command_approval_payload(),
            _params_present=True,
        )

        parsed = parse_server_request(envelope)

        self.assertIsInstance(parsed, TypedServerRequest)
        assert isinstance(parsed, TypedServerRequest)
        self.assertIsInstance(parsed.params, CommandExecutionApprovalRequestParams)
        typed_params = parsed.params
        assert isinstance(typed_params, CommandExecutionApprovalRequestParams)
        self.assertIs(parsed.envelope, envelope)
        self.assertEqual(typed_params.item_id, "item_123")
        self.assertEqual(typed_params.command, ["pytest", "-q"])
        self.assertIsNotNone(typed_params.command_actions)
        assert typed_params.command_actions is not None
        self.assertEqual(
            typed_params.command_actions[0].model_dump(),
            {
                "type": "search",
                "command": "rg -n failing",
                "query": "failing",
            },
        )

    def test_parse_server_request_uses_raw_fallback_for_unknown_methods(self) -> None:
        parsed = parse_server_request(
            {
                "id": "future-request-1",
                "method": "item/experimental/requestApproval",
                "params": {"threadId": "thread_123", "opaque": True},
            }
        )

        self.assertIsInstance(parsed, RawServerRequest)
        assert isinstance(parsed, RawServerRequest)
        self.assertEqual(parsed.method, "item/experimental/requestApproval")
        self.assertEqual(parsed.request_id, "future-request-1")
        self.assertEqual(parsed.fallback_reason, "unknown_method")
        self.assertEqual(parsed.params, {"threadId": "thread_123", "opaque": True})

    def test_parse_server_request_rejects_non_request_envelopes(self) -> None:
        with self.assertRaises(TypeError):
            parse_server_request(
                {
                    "method": "turn/started",
                    "params": {"threadId": "thread_123", "turnId": "turn_123"},
                }
            )

    def test_known_server_request_methods_do_not_fall_back_raw_on_invalid_payloads(self) -> None:
        with self.assertRaises(ValidationError):
            parse_server_request(
                {
                    "id": "approval-2",
                    "method": "item/fileChange/requestApproval",
                    "params": {"threadId": "thread_123", "turnId": "turn_123"},
                }
            )


if __name__ == "__main__":
    unittest.main()

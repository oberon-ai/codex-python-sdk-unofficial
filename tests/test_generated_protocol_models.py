from __future__ import annotations

import importlib
import importlib.util
import re
import sys
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATED_MODULE_PATH = REPO_ROOT / "src" / "codex_agent_sdk" / "generated" / "stable.py"
CODEGEN_SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_protocol_models.py"


def load_codegen_script_module() -> object:
    spec = importlib.util.spec_from_file_location("generate_protocol_models", CODEGEN_SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module spec for {CODEGEN_SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def build_turn_payload(*, status: str = "inProgress") -> dict[str, object]:
    return {
        "id": "turn_123",
        "items": [],
        "status": status,
    }


class GeneratedProtocolModelsTests(unittest.TestCase):
    generated: Any
    codegen_script: Any

    @classmethod
    def setUpClass(cls) -> None:
        cls.generated = importlib.import_module("codex_agent_sdk.generated.stable")
        cls.codegen_script = load_codegen_script_module()

    def test_generation_script_uses_pinned_stable_snapshot(self) -> None:
        output_path = self.codegen_script.OUTPUT_PATH
        target = self.codegen_script.load_generation_target()

        self.assertEqual(target.schema_artifact_name, "stable")
        self.assertEqual(output_path, GENERATED_MODULE_PATH)
        self.assertTrue(target.schema_path.name.endswith(".stable.schemas.json"))
        self.assertEqual(target.output_path, GENERATED_MODULE_PATH)

        command = self.codegen_script.build_codegen_command(
            schema_path=target.schema_path,
            output_path=Path("/tmp/generated.py"),
        )
        self.assertIn("--snake-case-field", command)
        self.assertIn("--allow-population-by-field-name", command)
        self.assertIn("--base-class", command)
        self.assertIn("codex_agent_sdk.protocol.pydantic.WireModel", command)
        self.assertIn("--disable-timestamp", command)
        self.assertIn("--use-annotated", command)
        self.assertIn("pydantic_v2.BaseModel", command)
        self.assertIn("ruff-format", command)
        self.assertIn("ruff-check", command)

    def test_generated_module_is_machine_written_and_deterministic(self) -> None:
        content = GENERATED_MODULE_PATH.read_text(encoding="utf-8")
        target = self.codegen_script.load_generation_target()
        pinned_codegen_version = self.codegen_script.read_codegen_version_pin()
        expected_header = self.codegen_script.build_stable_output_header(
            target=target,
            pinned_codegen_version=pinned_codegen_version,
        )

        self.assertTrue(content.startswith(expected_header + "\n\n"))
        self.assertNotIn("timestamp:", "\n".join(content.splitlines()[:3]))
        self.assertIn(
            "from codex_agent_sdk.protocol.pydantic import WireModel, WireRootModel",
            content,
        )
        self.assertIn("class InitializeParams(WireModel):", content)
        self.assertIn("class ServerNotification(WireRootModel[", content)
        self.assertIn("class InitializeParams", content)
        self.assertIn("class ThreadStartParams", content)
        self.assertIn("class TurnStartParams", content)
        self.assertIn("class ClientRequest(", content)
        self.assertIn("class ServerNotification(", content)
        self.assertRegex(
            content,
            re.compile(r"thread_id: .*Field\(alias=['\"]threadId['\"]\)"),
        )

    def test_generated_module_exports_major_protocol_shapes(self) -> None:
        expected_names = {
            "AgentMessageDeltaNotification",
            "AskForApproval",
            "ClientRequest",
            "CommandExecutionOutputDeltaNotification",
            "InitializeParams",
            "ItemCompletedNotification",
            "ItemStartedNotification",
            "ReasoningTextDeltaNotification",
            "ServerNotification",
            "ServerRequestResolvedNotification",
            "Thread",
            "ThreadStartedNotification",
            "ThreadStartParams",
            "ThreadStartResponse",
            "ThreadStatusChangedNotification",
            "ThreadTokenUsageUpdatedNotification",
            "Turn",
            "TurnCompletedNotification",
            "TurnStartedNotification",
            "TurnStartParams",
            "TurnStartResponse",
            "UserInput",
        }

        missing = {name for name in expected_names if not hasattr(self.generated, name)}
        self.assertEqual(missing, set())

    def test_generated_models_validate_request_params(self) -> None:
        initialize = self.generated.InitializeParams(
            client_info={
                "name": "codex-agent-sdk-unofficial",
                "version": "0.0.0",
            }
        )
        self.assertEqual(initialize.client_info.name, "codex-agent-sdk-unofficial")
        self.assertEqual(
            initialize.model_dump(),
            {
                "clientInfo": {
                    "name": "codex-agent-sdk-unofficial",
                    "version": "0.0.0",
                }
            },
        )

        thread_start = self.generated.ThreadStartParams(
            approval_policy="on-request",
            cwd="/repo",
            model="gpt-5.4",
            model_provider="openai",
            sandbox="workspace-write",
        )
        self.assertEqual(thread_start.cwd, "/repo")
        self.assertEqual(thread_start.model, "gpt-5.4")
        self.assertEqual(thread_start.model_provider, "openai")
        self.assertEqual(
            thread_start.model_dump(),
            {
                "approvalPolicy": "on-request",
                "cwd": "/repo",
                "model": "gpt-5.4",
                "modelProvider": "openai",
                "sandbox": "workspace-write",
            },
        )

        turn_start = self.generated.TurnStartParams(
            thread_id="thread_123",
            input=[{"type": "text", "text": "Find the failing tests."}],
            approval_policy={
                "granular": {
                    "mcp_elicitations": True,
                    "rules": True,
                    "sandbox_approval": True,
                }
            },
        )
        self.assertEqual(turn_start.thread_id, "thread_123")
        self.assertEqual(turn_start.input[0].root.type, "text")
        self.assertEqual(
            turn_start.model_dump(),
            {
                "threadId": "thread_123",
                "input": [{"type": "text", "text": "Find the failing tests."}],
                "approvalPolicy": {
                    "granular": {
                        "mcp_elicitations": True,
                        "rules": True,
                        "sandbox_approval": True,
                    }
                },
            },
        )
        self.assertEqual(
            turn_start.model_dump(by_alias=False)["thread_id"],
            "thread_123",
        )
        self.assertIn("approval_policy", turn_start.model_dump(by_alias=False))

    def test_generated_models_validate_responses_and_notifications(self) -> None:
        thread_payload = build_thread_payload()
        turn_payload = build_turn_payload()

        thread_start_response = self.generated.ThreadStartResponse.model_validate(
            {
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "cwd": "/repo",
                "model": "gpt-5.4",
                "modelProvider": "openai",
                "sandbox": {"type": "dangerFullAccess"},
                "thread": thread_payload,
            }
        )
        self.assertEqual(thread_start_response.thread.id, "thread_123")
        self.assertEqual(thread_start_response.thread.created_at, 1_710_000_000)
        self.assertEqual(thread_start_response.thread.model_provider, "openai")
        self.assertEqual(
            thread_start_response.model_dump()["thread"]["createdAt"],
            1_710_000_000,
        )

        turn_start_response = self.generated.TurnStartResponse.model_validate(
            {"turn": turn_payload}
        )
        self.assertEqual(turn_start_response.turn.status, "inProgress")

        server_notification = self.generated.ServerNotification.model_validate(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread_123",
                    "turn": turn_payload,
                },
            }
        )
        self.assertEqual(server_notification.root.method, "turn/started")
        self.assertEqual(server_notification.root.params.thread_id, "thread_123")
        self.assertEqual(server_notification.root.params.turn.id, "turn_123")
        self.assertEqual(
            server_notification.model_dump(),
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread_123",
                    "turn": turn_payload,
                },
            },
        )

    def test_generated_models_validate_client_request_and_server_request_lifecycle_notification(
        self,
    ) -> None:
        client_request = self.generated.ClientRequest.model_validate(
            {
                "id": 7,
                "method": "turn/start",
                "params": {
                    "threadId": "thread_123",
                    "input": [{"type": "text", "text": "Summarize the highest-risk issues."}],
                },
            }
        )
        self.assertEqual(client_request.root.method, "turn/start")
        self.assertEqual(client_request.root.params.thread_id, "thread_123")
        self.assertEqual(
            client_request.model_dump(),
            {
                "id": 7,
                "method": "turn/start",
                "params": {
                    "threadId": "thread_123",
                    "input": [{"type": "text", "text": "Summarize the highest-risk issues."}],
                },
            },
        )

        resolved = self.generated.ServerRequestResolvedNotification.model_validate(
            {"requestId": 99, "threadId": "thread_123"}
        )
        self.assertEqual(resolved.request_id.root, 99)
        self.assertEqual(resolved.thread_id, "thread_123")
        self.assertEqual(
            resolved.model_dump(),
            {"requestId": 99, "threadId": "thread_123"},
        )

    def test_generated_models_accept_wire_keys_and_pythonic_names(self) -> None:
        from_wire = self.generated.ThreadStartParams.model_validate(
            {
                "approvalPolicy": "on-request",
                "cwd": "/repo",
                "model": "gpt-5.4",
                "modelProvider": "openai",
                "sandbox": "workspace-write",
            }
        )
        from_python = self.generated.ThreadStartParams(
            approval_policy="on-request",
            cwd="/repo",
            model="gpt-5.4",
            model_provider="openai",
            sandbox="workspace-write",
        )

        self.assertEqual(from_wire, from_python)
        self.assertEqual(from_wire.model_dump(), from_python.model_dump())
        self.assertEqual(from_python.model_dump()["approvalPolicy"], "on-request")
        self.assertEqual(from_python.model_dump(by_alias=False)["approval_policy"], "on-request")


if __name__ == "__main__":
    unittest.main()

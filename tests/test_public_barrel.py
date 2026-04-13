from __future__ import annotations

import importlib
import unittest
from pathlib import Path

import codex_agent_sdk
from codex_agent_sdk import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRequestedEvent,
    AppServerClient,
    AppServerConfig,
    CodexOptions,
    CodexSDKClient,
    TurnEvent,
    TurnHandle,
    TurnResult,
    TurnStartedEvent,
    query,
)

approvals_module = importlib.import_module("codex_agent_sdk.approvals")
client_module = importlib.import_module("codex_agent_sdk.client")
events_module = importlib.import_module("codex_agent_sdk.events")
options_module = importlib.import_module("codex_agent_sdk.options")
query_module = importlib.import_module("codex_agent_sdk.query")
results_module = importlib.import_module("codex_agent_sdk.results")

REPO_ROOT = Path(__file__).resolve().parents[1]


class PublicBarrelTests(unittest.TestCase):
    def test_root_barrel_exports_contract_high_level_names(self) -> None:
        expected = {
            "AppServerClient",
            "AppServerConfig",
            "ApprovalDecision",
            "ApprovalRequest",
            "AgentTextDeltaEvent",
            "ApprovalRequestedEvent",
            "CodexOptions",
            "CodexSDKClient",
            "query",
            "ThreadStatusChangedEvent",
            "TurnCompletedEvent",
            "TurnEvent",
            "TurnHandle",
            "TurnResult",
            "TurnStartedEvent",
        }

        self.assertTrue(expected.issubset(set(codex_agent_sdk.__all__)))

    def test_root_barrel_points_at_public_module_definitions(self) -> None:
        self.assertIs(AppServerClient, client_module.AppServerClient)
        self.assertIs(AppServerConfig, options_module.AppServerConfig)
        self.assertIs(CodexSDKClient, client_module.CodexSDKClient)
        self.assertIs(ApprovalDecision, approvals_module.ApprovalDecision)
        self.assertIs(ApprovalRequest, approvals_module.ApprovalRequest)
        self.assertIs(CodexOptions, options_module.CodexOptions)
        self.assertIs(TurnHandle, results_module.TurnHandle)
        self.assertIs(TurnResult, results_module.TurnResult)
        self.assertIs(TurnStartedEvent, events_module.TurnStartedEvent)
        self.assertIs(ApprovalRequestedEvent, events_module.ApprovalRequestedEvent)
        self.assertIs(TurnEvent, events_module.TurnEvent)
        self.assertIs(query, query_module.query)

    def test_root_barrel_does_not_promote_lower_layers(self) -> None:
        promoted_names = set(codex_agent_sdk.__all__)

        for name in {"transport", "rpc", "protocol", "generated", "testing"}:
            with self.subTest(name=name):
                self.assertNotIn(name, promoted_names)

    def test_root_docstring_explains_main_entry_points(self) -> None:
        docstring = codex_agent_sdk.__doc__ or ""

        self.assertIn("query()", docstring)
        self.assertIn("CodexSDKClient", docstring)
        self.assertIn("AppServerClient", docstring)
        self.assertIn("Import policy", docstring)

    def test_public_import_policy_doc_exists_and_marks_lower_layers_private(self) -> None:
        policy_doc = REPO_ROOT / "docs" / "public-import-policy.md"
        content = policy_doc.read_text(encoding="utf-8")

        self.assertTrue(policy_doc.exists())
        self.assertIn("root package is authoritative", content.lower())
        self.assertIn("codex_agent_sdk.transport", content)
        self.assertIn("codex_agent_sdk.rpc", content)
        self.assertIn("codex_agent_sdk.protocol", content)
        self.assertIn("codex_agent_sdk.generated", content)

    def test_placeholder_objects_tell_the_public_api_story(self) -> None:
        self.assertIn("Low-level native-async client", AppServerClient.__doc__ or "")
        self.assertIn("High-level stateful client", CodexSDKClient.__doc__ or "")
        self.assertIn("one-shot query helper", query.__doc__ or "")
        self.assertIn("Typed approval request", ApprovalRequest.__doc__ or "")
        self.assertIn("Compact terminal summary", TurnResult.__doc__ or "")


if __name__ == "__main__":
    unittest.main()

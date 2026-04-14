from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from codex_agent_sdk import (
    DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    DEFAULT_STARTUP_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_POLICY,
    AppServerConfig,
)
from codex_agent_sdk.generated.stable import (
    ApprovalsReviewer,
    AskForApproval,
    Personality,
    ReasoningEffort,
    ReasoningSummary,
    SandboxMode,
    SandboxPolicy,
    ServiceTier,
)
from codex_agent_sdk.options import CodexOptions, TimeoutPolicy

REPO_ROOT = Path(__file__).resolve().parents[1]


class TimeoutPolicyTests(unittest.TestCase):
    def test_default_timeout_policy_matches_documented_invariants(self) -> None:
        self.assertIsInstance(DEFAULT_TIMEOUT_POLICY, TimeoutPolicy)
        self.assertEqual(DEFAULT_TIMEOUT_POLICY.startup, DEFAULT_STARTUP_TIMEOUT_SECONDS)
        self.assertEqual(DEFAULT_TIMEOUT_POLICY.shutdown, DEFAULT_SHUTDOWN_TIMEOUT_SECONDS)
        self.assertIsNone(DEFAULT_TIMEOUT_POLICY.rpc_request)
        self.assertIsNone(DEFAULT_TIMEOUT_POLICY.turn_completion)
        self.assertIsNone(DEFAULT_TIMEOUT_POLICY.turn_event_stream)
        self.assertIsNone(DEFAULT_TIMEOUT_POLICY.approval_decision)

    def test_app_server_config_uses_timeout_policy_defaults(self) -> None:
        config = AppServerConfig()

        self.assertIsNone(config.cwd)
        self.assertEqual(config.extra_args, ())
        self.assertIsNone(config.env)
        self.assertEqual(config.startup_timeout, DEFAULT_TIMEOUT_POLICY.startup)
        self.assertEqual(config.shutdown_timeout, DEFAULT_TIMEOUT_POLICY.shutdown)
        self.assertFalse(config.experimental_api)
        self.assertEqual(config.opt_out_notification_methods, ())
        self.assertFalse(config.debug_logging)
        self.assertIsNone(config.debug_logger)

    def test_codex_options_defaults_to_explicit_none_values(self) -> None:
        options = CodexOptions()

        self.assertIsNone(options.model)
        self.assertIsNone(options.cwd)
        self.assertIsNone(options.approval_policy)
        self.assertIsNone(options.approvals_reviewer)
        self.assertIsNone(options.effort)
        self.assertIsNone(options.summary)
        self.assertIsNone(options.personality)
        self.assertIsNone(options.service_tier)
        self.assertIsNone(options.sandbox_mode)
        self.assertIsNone(options.sandbox_policy)
        self.assertIsNone(options.base_instructions)
        self.assertIsNone(options.developer_instructions)
        self.assertEqual(options.to_thread_start_kwargs(), {})
        self.assertEqual(options.to_thread_resume_kwargs(), {})
        self.assertEqual(options.to_thread_fork_kwargs(), {})
        self.assertEqual(options.to_turn_start_kwargs(), {})

    def test_codex_options_signature_keeps_transport_and_current_turn_fields_out(self) -> None:
        parameters = inspect.signature(CodexOptions).parameters

        self.assertIn("model", parameters)
        self.assertIn("sandbox_mode", parameters)
        self.assertIn("sandbox_policy", parameters)
        self.assertNotIn("output_schema", parameters)
        self.assertNotIn("env", parameters)
        self.assertNotIn("experimental_api", parameters)
        self.assertNotIn("opt_out_notification_methods", parameters)

    def test_codex_options_normalizes_user_facing_input_types(self) -> None:
        options = CodexOptions(
            approval_policy="on-request",
            approvals_reviewer="guardian_subagent",
            effort="high",
            summary="none",
            personality="pragmatic",
            service_tier="flex",
            sandbox_mode="workspace-write",
            sandbox_policy={
                "type": "workspaceWrite",
                "writableRoots": ["/repo"],
                "networkAccess": True,
            },
            base_instructions="Base prompt",
            developer_instructions="Developer prompt",
        )

        self.assertIsInstance(options.approval_policy, AskForApproval)
        assert options.approval_policy is not None
        self.assertEqual(options.approval_policy.model_dump(), "on-request")
        self.assertEqual(options.approvals_reviewer, ApprovalsReviewer.guardian_subagent)
        self.assertEqual(options.effort, ReasoningEffort.high)
        self.assertIsInstance(options.summary, ReasoningSummary)
        assert options.summary is not None
        self.assertEqual(options.summary.model_dump(), "none")
        self.assertEqual(options.personality, Personality.pragmatic)
        self.assertEqual(options.service_tier, ServiceTier.flex)
        self.assertEqual(options.sandbox_mode, SandboxMode.workspace_write)
        self.assertIsInstance(options.sandbox_policy, SandboxPolicy)
        assert options.sandbox_policy is not None
        self.assertEqual(
            options.sandbox_policy.model_dump(),
            {
                "type": "workspaceWrite",
                "writableRoots": ["/repo"],
                "networkAccess": True,
            },
        )
        self.assertEqual(options.base_instructions, "Base prompt")
        self.assertEqual(options.developer_instructions, "Developer prompt")

    def test_codex_options_merge_uses_last_non_none_precedence(self) -> None:
        client_defaults = CodexOptions(
            model="gpt-5.4",
            cwd="/repo",
            approval_policy="never",
            personality="friendly",
            sandbox_mode="read-only",
        )
        thread_defaults = CodexOptions(
            cwd="/repo/subdir",
            base_instructions="Base prompt",
            approval_policy="on-request",
        )
        turn_overrides = CodexOptions(
            summary="concise",
            sandbox_policy={"type": "workspaceWrite", "writableRoots": ["/repo/subdir"]},
        )

        merged = CodexOptions.merge(client_defaults, thread_defaults, turn_overrides)

        self.assertEqual(merged.model, "gpt-5.4")
        self.assertEqual(merged.cwd, "/repo/subdir")
        assert merged.approval_policy is not None
        self.assertEqual(merged.approval_policy.model_dump(), "on-request")
        self.assertEqual(merged.personality, Personality.friendly)
        self.assertEqual(merged.base_instructions, "Base prompt")
        assert merged.summary is not None
        self.assertEqual(merged.summary.model_dump(), "concise")
        assert merged.sandbox_policy is not None
        self.assertEqual(
            merged.sandbox_policy.model_dump(),
            {"type": "workspaceWrite", "writableRoots": ["/repo/subdir"]},
        )
        self.assertEqual(merged.merged_with(None), merged)

    def test_codex_options_thread_projection_uses_thread_supported_fields(self) -> None:
        options = CodexOptions(
            model="gpt-5.4",
            cwd="/repo",
            approval_policy="on-request",
            approvals_reviewer="user",
            personality="friendly",
            service_tier="fast",
            base_instructions="Base prompt",
            developer_instructions="Developer prompt",
            sandbox_policy={"type": "workspaceWrite", "writableRoots": ["/repo"]},
        )

        start_kwargs = options.to_thread_start_kwargs(ephemeral=True)
        resume_kwargs = options.to_thread_resume_kwargs()
        fork_kwargs = options.to_thread_fork_kwargs(ephemeral=False)

        self.assertEqual(start_kwargs["model"], "gpt-5.4")
        self.assertEqual(start_kwargs["cwd"], "/repo")
        self.assertEqual(start_kwargs["approval_policy"], options.approval_policy)
        self.assertEqual(
            start_kwargs["approvals_reviewer"],
            ApprovalsReviewer.user,
        )
        self.assertEqual(start_kwargs["personality"], Personality.friendly)
        self.assertEqual(start_kwargs["service_tier"], ServiceTier.fast)
        self.assertEqual(start_kwargs["base_instructions"], "Base prompt")
        self.assertEqual(start_kwargs["developer_instructions"], "Developer prompt")
        self.assertEqual(start_kwargs["sandbox"], SandboxMode.workspace_write)
        self.assertTrue(start_kwargs["ephemeral"])
        self.assertNotIn("sandbox_policy", start_kwargs)
        self.assertNotIn("effort", start_kwargs)
        self.assertNotIn("summary", start_kwargs)

        self.assertNotIn("ephemeral", resume_kwargs)
        self.assertIn("personality", resume_kwargs)
        self.assertEqual(fork_kwargs["sandbox"], SandboxMode.workspace_write)
        self.assertFalse(fork_kwargs["ephemeral"])
        self.assertNotIn("personality", fork_kwargs)

    def test_codex_options_turn_projection_uses_turn_supported_fields(self) -> None:
        options = CodexOptions(
            model="gpt-5.4",
            cwd="/repo",
            approval_policy="on-request",
            approvals_reviewer="guardian_subagent",
            effort="medium",
            summary="detailed",
            personality="pragmatic",
            service_tier="flex",
            sandbox_mode="read-only",
            base_instructions="ignored here",
            developer_instructions="ignored here",
        )

        turn_kwargs = options.to_turn_start_kwargs()

        self.assertEqual(turn_kwargs["model"], "gpt-5.4")
        self.assertEqual(turn_kwargs["cwd"], "/repo")
        self.assertEqual(turn_kwargs["approval_policy"], options.approval_policy)
        self.assertEqual(
            turn_kwargs["approvals_reviewer"],
            ApprovalsReviewer.guardian_subagent,
        )
        self.assertEqual(turn_kwargs["effort"], ReasoningEffort.medium)
        summary = turn_kwargs["summary"]
        assert isinstance(summary, ReasoningSummary)
        self.assertEqual(summary.model_dump(), "detailed")
        self.assertEqual(turn_kwargs["personality"], Personality.pragmatic)
        self.assertEqual(turn_kwargs["service_tier"], ServiceTier.flex)
        sandbox_policy = turn_kwargs["sandbox_policy"]
        assert isinstance(sandbox_policy, SandboxPolicy)
        self.assertEqual(sandbox_policy.model_dump(), {"type": "readOnly"})
        self.assertNotIn("base_instructions", turn_kwargs)
        self.assertNotIn("developer_instructions", turn_kwargs)
        self.assertNotIn("sandbox", turn_kwargs)

    def test_codex_options_doc_explains_layering_and_precedence(self) -> None:
        options_doc = REPO_ROOT / "docs" / "codex-options.md"
        content = options_doc.read_text(encoding="utf-8")

        self.assertTrue(options_doc.exists())
        self.assertIn("CodexOptions", content)
        self.assertIn("AppServerConfig", content)
        self.assertIn("client defaults", content)
        self.assertIn("thread defaults", content)
        self.assertIn("per-turn overrides", content)
        self.assertIn("output_schema", content)
        self.assertIn("experimental_api", content)
        self.assertIn("opt_out_notification_methods", content)


if __name__ == "__main__":
    unittest.main()

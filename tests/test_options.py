from __future__ import annotations

import unittest

from codex_agent_sdk import (
    AppServerConfig,
    DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    DEFAULT_STARTUP_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_POLICY,
)
from codex_agent_sdk.options import CodexOptions, TimeoutPolicy


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

        self.assertEqual(config.startup_timeout, DEFAULT_TIMEOUT_POLICY.startup)
        self.assertEqual(config.shutdown_timeout, DEFAULT_TIMEOUT_POLICY.shutdown)
        self.assertFalse(config.experimental_api)

    def test_codex_options_defaults_to_explicit_none_values(self) -> None:
        options = CodexOptions()

        self.assertIsNone(options.model)
        self.assertIsNone(options.cwd)
        self.assertIsNone(options.approval_policy)
        self.assertIsNone(options.sandbox_policy)
        self.assertIsNone(options.approvals_reviewer)
        self.assertIsNone(options.effort)
        self.assertIsNone(options.summary)
        self.assertIsNone(options.personality)


if __name__ == "__main__":
    unittest.main()

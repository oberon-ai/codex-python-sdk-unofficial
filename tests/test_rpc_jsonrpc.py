from __future__ import annotations

import unittest

from codex_agent_sdk import MessageDecodeError
from codex_agent_sdk.rpc import parse_jsonrpc_envelope


class ParseJsonRpcEnvelopeTests(unittest.TestCase):
    def test_parse_jsonrpc_envelope_accepts_top_level_object(self) -> None:
        envelope = parse_jsonrpc_envelope('{"id":1,"method":"thread/start"}')

        self.assertEqual(envelope["id"], 1)
        self.assertEqual(envelope["method"], "thread/start")

    def test_parse_jsonrpc_envelope_preserves_line_and_stderr_for_invalid_json(self) -> None:
        with self.assertRaises(MessageDecodeError) as exc_info:
            parse_jsonrpc_envelope(
                "{broken-json",
                stderr_tail="app-server emitted malformed stdout",
            )

        error = exc_info.exception
        self.assertEqual(error.line, "{broken-json")
        self.assertEqual(error.stderr_tail, "app-server emitted malformed stdout")
        self.assertIn("app-server emitted malformed stdout", str(error))

    def test_parse_jsonrpc_envelope_rejects_non_object_payloads(self) -> None:
        with self.assertRaises(MessageDecodeError) as exc_info:
            parse_jsonrpc_envelope('["not","an","object"]')

        error = exc_info.exception
        self.assertIsInstance(error.original_error, ValueError)
        self.assertEqual(error.line, '["not","an","object"]')


if __name__ == "__main__":
    unittest.main()

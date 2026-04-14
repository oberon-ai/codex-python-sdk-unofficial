from __future__ import annotations

import math
import unittest

from codex_agent_sdk import MessageDecodeError
from codex_agent_sdk.rpc import (
    JSON_RPC_VERSION,
    JsonRpcErrorObject,
    JsonRpcErrorResponse,
    JsonRpcNotification,
    JsonRpcRequest,
    JsonRpcSuccessResponse,
    classify_jsonrpc_envelope,
    coerce_jsonrpc_envelope,
    is_jsonrpc_error_response_envelope,
    is_jsonrpc_notification_envelope,
    is_jsonrpc_request_envelope,
    is_jsonrpc_response_envelope,
    is_jsonrpc_success_response_envelope,
    parse_jsonrpc_envelope,
    serialize_jsonrpc_envelope,
)


class ParseJsonRpcEnvelopeTests(unittest.TestCase):
    def test_parse_jsonrpc_envelope_models_request_without_jsonrpc_header(self) -> None:
        envelope = parse_jsonrpc_envelope('{"id":1,"method":"thread/start"}')

        assert isinstance(envelope, JsonRpcRequest)
        self.assertIsInstance(envelope, JsonRpcRequest)
        self.assertEqual(envelope.id, 1)
        self.assertEqual(envelope.method, "thread/start")
        self.assertFalse(envelope.has_params)
        self.assertEqual(envelope.jsonrpc, JSON_RPC_VERSION)
        self.assertEqual(classify_jsonrpc_envelope(envelope), "request")

    def test_parse_jsonrpc_envelope_models_notification(self) -> None:
        envelope = parse_jsonrpc_envelope(
            '{"method":"turn/started","params":{"threadId":"thread_1","turnId":"turn_1"}}'
        )

        assert isinstance(envelope, JsonRpcNotification)
        self.assertIsInstance(envelope, JsonRpcNotification)
        self.assertEqual(envelope.method, "turn/started")
        self.assertEqual(
            envelope.params,
            {"threadId": "thread_1", "turnId": "turn_1"},
        )
        self.assertTrue(envelope.has_params)
        self.assertTrue(is_jsonrpc_notification_envelope(envelope))

    def test_parse_jsonrpc_envelope_models_success_response(self) -> None:
        envelope = parse_jsonrpc_envelope('{"id":7,"result":{"threadId":"thread_7"}}')

        assert isinstance(envelope, JsonRpcSuccessResponse)
        self.assertIsInstance(envelope, JsonRpcSuccessResponse)
        self.assertEqual(envelope.id, 7)
        self.assertEqual(envelope.result, {"threadId": "thread_7"})
        self.assertTrue(is_jsonrpc_success_response_envelope(envelope))
        self.assertTrue(is_jsonrpc_response_envelope(envelope))

    def test_parse_jsonrpc_envelope_models_error_response_and_preserves_error_data(self) -> None:
        envelope = parse_jsonrpc_envelope(
            '{"id":7,"error":{"code":-32001,"message":"overloaded","data":{"retryAfterMs":250}}}'
        )

        assert isinstance(envelope, JsonRpcErrorResponse)
        self.assertIsInstance(envelope, JsonRpcErrorResponse)
        self.assertEqual(envelope.id, 7)
        self.assertEqual(envelope.error.code, -32001)
        self.assertEqual(envelope.error.message, "overloaded")
        self.assertEqual(envelope.error.data, {"retryAfterMs": 250})
        self.assertTrue(envelope.error.has_data)
        self.assertTrue(is_jsonrpc_error_response_envelope(envelope))
        self.assertTrue(is_jsonrpc_response_envelope(envelope))

    def test_parse_jsonrpc_envelope_accepts_explicit_jsonrpc_header_when_correct(self) -> None:
        envelope = parse_jsonrpc_envelope('{"jsonrpc":"2.0","method":"initialized","params":{}}')

        assert isinstance(envelope, JsonRpcNotification)
        self.assertIsInstance(envelope, JsonRpcNotification)
        self.assertEqual(envelope.jsonrpc, JSON_RPC_VERSION)

    def test_parse_jsonrpc_envelope_rejects_invalid_jsonrpc_header(self) -> None:
        with self.assertRaises(MessageDecodeError) as exc_info:
            parse_jsonrpc_envelope('{"jsonrpc":"1.0","id":1,"method":"thread/start"}')

        error = exc_info.exception
        self.assertIsInstance(error.original_error, ValueError)
        self.assertIn("jsonrpc field must be '2.0'", str(error.original_error))

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

    def test_parse_jsonrpc_envelope_rejects_invalid_response_shape(self) -> None:
        with self.assertRaises(MessageDecodeError) as exc_info:
            parse_jsonrpc_envelope('{"id":7,"result":{},"error":{"code":1,"message":"nope"}}')

        error = exc_info.exception
        self.assertIsInstance(error.original_error, ValueError)
        self.assertIn("exactly one of result or error", str(error.original_error))

    def test_coerce_jsonrpc_envelope_normalizes_raw_mappings(self) -> None:
        envelope = coerce_jsonrpc_envelope(
            {"id": "req_1", "error": {"code": -32601, "message": "missing"}}
        )

        assert isinstance(envelope, JsonRpcErrorResponse)
        self.assertIsInstance(envelope, JsonRpcErrorResponse)
        self.assertEqual(envelope.id, "req_1")
        self.assertEqual(envelope.error, JsonRpcErrorObject(code=-32601, message="missing"))

    def test_serialize_jsonrpc_envelope_omits_jsonrpc_by_default(self) -> None:
        line = serialize_jsonrpc_envelope(
            JsonRpcRequest(
                id=7,
                method="thread/start",
                params={"cwd": ".", "includeHidden": False},
                _params_present=True,
            )
        )

        self.assertEqual(
            line,
            '{"id":7,"method":"thread/start","params":{"cwd":".","includeHidden":false}}',
        )

    def test_serialize_jsonrpc_envelope_can_include_jsonrpc_header_explicitly(self) -> None:
        line = serialize_jsonrpc_envelope(
            JsonRpcNotification(method="initialized", params={}, _params_present=True),
            include_jsonrpc=True,
        )

        self.assertEqual(line, '{"method":"initialized","params":{},"jsonrpc":"2.0"}')

    def test_serialize_jsonrpc_envelope_accepts_raw_mappings(self) -> None:
        line = serialize_jsonrpc_envelope({"id": 1, "result": {"ok": True}})

        self.assertEqual(line, '{"id":1,"result":{"ok":true}}')

    def test_serialize_jsonrpc_envelope_rejects_nan_values(self) -> None:
        with self.assertRaises(ValueError):
            serialize_jsonrpc_envelope({"id": 1, "params": {"value": math.nan}})

    def test_classification_helpers_work_for_raw_frames(self) -> None:
        request = {"id": 1, "method": "thread/start"}
        notification = {"method": "thread/started", "params": {"threadId": "thread_1"}}
        success = {"id": 1, "result": {"threadId": "thread_1"}}
        error = {"id": 1, "error": {"code": -32001, "message": "overloaded"}}

        self.assertTrue(is_jsonrpc_request_envelope(request))
        self.assertTrue(is_jsonrpc_notification_envelope(notification))
        self.assertTrue(is_jsonrpc_success_response_envelope(success))
        self.assertTrue(is_jsonrpc_error_response_envelope(error))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from codex_agent_sdk import (
    AlreadyInitializedError,
    ApprovalCallbackError,
    ApprovalRequestExpiredError,
    CodexNotFoundError,
    CodexTimeoutError,
    DuplicateRequestIdError,
    DuplicateResponseError,
    DuplicateServerRequestIdError,
    JsonRpcInternalError,
    JsonRpcInvalidParamsError,
    JsonRpcInvalidRequestError,
    JsonRpcMethodNotFoundError,
    JsonRpcParseError,
    JsonRpcServerError,
    LateResponseError,
    NotificationSubscriptionOverflowError,
    NotInitializedError,
    RequestCorrelationError,
    RequestTimeoutError,
    ResponseValidationError,
    RetryableOverloadError,
    RetryBudgetExceededError,
    ServerRequestAlreadyRespondedError,
    ServerRequestStateError,
    ShutdownError,
    ShutdownTimeoutError,
    StartupError,
    StartupTimeoutError,
    TransportWriteError,
    UnknownResponseIdError,
    UnknownServerRequestIdError,
    is_retryable_error,
    map_jsonrpc_error,
)


class JsonRpcMappingTests(unittest.TestCase):
    def test_standard_jsonrpc_codes_map_to_specific_types(self) -> None:
        cases = [
            (-32700, JsonRpcParseError),
            (-32600, JsonRpcInvalidRequestError),
            (-32601, JsonRpcMethodNotFoundError),
            (-32602, JsonRpcInvalidParamsError),
            (-32603, JsonRpcInternalError),
        ]

        for code, expected_type in cases:
            with self.subTest(code=code):
                error = map_jsonrpc_error(code, "boom", method="thread/start", request_id="abc123")
                self.assertIsInstance(error, expected_type)
                self.assertEqual(error.method, "thread/start")
                self.assertEqual(error.request_id, "abc123")

    def test_retryable_overload_is_distinct_from_generic_server_error(self) -> None:
        error = map_jsonrpc_error(-32001, "Server overloaded; retry later.")

        self.assertIsInstance(error, RetryableOverloadError)
        self.assertNotIsInstance(error, RetryBudgetExceededError)
        self.assertTrue(is_retryable_error(error))

    def test_retry_budget_exhaustion_stays_retryable(self) -> None:
        error = map_jsonrpc_error(
            -32001,
            "Server overloaded; retry limit exceeded.",
            data={"codexErrorInfo": {"kind": "server_overloaded"}},
        )

        self.assertIsInstance(error, RetryBudgetExceededError)
        self.assertTrue(is_retryable_error(error))

    def test_handshake_errors_map_from_server_messages(self) -> None:
        self.assertIsInstance(map_jsonrpc_error(-32002, "Not initialized"), NotInitializedError)
        self.assertIsInstance(
            map_jsonrpc_error(-32002, "Already initialized"), AlreadyInitializedError
        )

    def test_generic_server_error_remains_non_retryable(self) -> None:
        error = map_jsonrpc_error(-32050, "internal worker pool fault")

        self.assertIsInstance(error, JsonRpcServerError)
        self.assertFalse(is_retryable_error(error))


class ExceptionStructureTests(unittest.TestCase):
    def test_startup_timeout_is_both_start_and_timeout_error(self) -> None:
        error = StartupTimeoutError(timeout_seconds=20.0, stderr_tail="trace line")

        self.assertIsInstance(error, StartupError)
        self.assertIsInstance(error, CodexTimeoutError)
        self.assertIn("trace line", str(error))
        self.assertEqual(error.timeout_seconds, 20.0)

    def test_shutdown_timeout_is_both_shutdown_and_timeout_error(self) -> None:
        error = ShutdownTimeoutError(timeout_seconds=5.0, stderr_tail="cleanup stuck")

        self.assertIsInstance(error, ShutdownError)
        self.assertIsInstance(error, CodexTimeoutError)
        self.assertIn("cleanup stuck", str(error))
        self.assertEqual(error.timeout_seconds, 5.0)

    def test_not_found_is_also_startup_error(self) -> None:
        error = CodexNotFoundError(
            "/missing/codex",
            command=("/missing/codex", "app-server", "--listen", "stdio://"),
            cwd="/tmp/project",
        )

        self.assertIsInstance(error, StartupError)
        self.assertIn("/missing/codex", str(error))
        self.assertIn("command=", str(error))
        self.assertIn("cwd=/tmp/project", str(error))

    def test_request_timeout_preserves_method_and_request_id(self) -> None:
        error = RequestTimeoutError(method="turn/start", timeout_seconds=12.5, request_id="req-7")

        self.assertEqual(error.method, "turn/start")
        self.assertEqual(error.request_id, "req-7")
        self.assertIn("turn/start", str(error))

    def test_approval_errors_preserve_context(self) -> None:
        original = RuntimeError("handler blew up")
        callback_error = ApprovalCallbackError("req-9", original_error=original)
        expired_error = ApprovalRequestExpiredError("req-9")

        self.assertIs(callback_error.original_error, original)
        self.assertIn("req-9", str(callback_error))
        self.assertIn("req-9", str(expired_error))

    def test_response_validation_error_keeps_method(self) -> None:
        error = ResponseValidationError("bad payload", method="turn/start", payload={"turn": None})

        self.assertEqual(error.method, "turn/start")
        self.assertEqual(error.payload, {"turn": None})
        self.assertIn("turn/start", str(error))

    def test_transport_write_error_preserves_context(self) -> None:
        original = BrokenPipeError("broken pipe")
        error = TransportWriteError(
            "failed to write request to app-server transport",
            stderr_tail="transport stderr",
            exit_code=13,
            original_error=original,
        )

        self.assertIs(error.original_error, original)
        self.assertEqual(error.exit_code, 13)
        self.assertEqual(error.stderr_tail, "transport stderr")
        self.assertIn("exit_code=13", str(error))
        self.assertIn("transport stderr", str(error))

    def test_request_correlation_errors_preserve_request_context(self) -> None:
        duplicate_request = DuplicateRequestIdError("req-1", method="thread/start")
        unknown_response = UnknownResponseIdError("req-missing")
        duplicate_response = DuplicateResponseError("req-2", method="thread/resume")
        late_response = LateResponseError(
            "req-3",
            release_reason="timed_out",
            method="thread/start",
        )

        for error in (
            duplicate_request,
            unknown_response,
            duplicate_response,
            late_response,
        ):
            with self.subTest(error=error.__class__.__name__):
                self.assertIsInstance(error, RequestCorrelationError)
                self.assertIn("req-", str(error))

        self.assertEqual(late_response.release_reason, "timed_out")
        self.assertEqual(late_response.method, "thread/start")

    def test_server_request_state_errors_preserve_request_context(self) -> None:
        duplicate_request = DuplicateServerRequestIdError("req-7", method="item/tool/call")
        unknown_request = UnknownServerRequestIdError("req-8")
        duplicate_response = ServerRequestAlreadyRespondedError(
            "req-9",
            method="item/fileChange/requestApproval",
        )

        for error in (duplicate_request, unknown_request, duplicate_response):
            with self.subTest(error=error.__class__.__name__):
                self.assertIsInstance(error, ServerRequestStateError)
                self.assertIsInstance(error, RequestCorrelationError)
                self.assertIn("req-", str(error))

    def test_notification_subscription_overflow_error_preserves_filter_context(self) -> None:
        error = NotificationSubscriptionOverflowError(
            max_queue_size=32,
            method="turn/started",
            thread_id="thread_1",
            turn_id="turn_1",
        )

        self.assertEqual(error.max_queue_size, 32)
        self.assertEqual(error.method, "turn/started")
        self.assertEqual(error.thread_id, "thread_1")
        self.assertEqual(error.turn_id, "turn_1")
        self.assertIn("max_queue_size=32", str(error))
        self.assertIn("thread_id=thread_1", str(error))


if __name__ == "__main__":
    unittest.main()

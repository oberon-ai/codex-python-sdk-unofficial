from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from codex_agent_sdk import (
    DEFAULT_OVERLOAD_RETRY_POLICY,
    OverloadRetryPolicy,
    RetryableOverloadError,
    RetryBudgetExceededError,
    retry_on_overload,
)
from codex_agent_sdk.errors import JsonRpcInternalError


async def _record_sleep(delay_seconds: float, sink: list[float]) -> None:
    sink.append(delay_seconds)


class _CancelledSleep:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, delay_seconds: float) -> None:
        self.calls += 1
        raise asyncio.CancelledError()


def test_default_overload_retry_policy_is_public_and_stable() -> None:
    assert isinstance(DEFAULT_OVERLOAD_RETRY_POLICY, OverloadRetryPolicy)
    assert DEFAULT_OVERLOAD_RETRY_POLICY.max_attempts == 4
    assert DEFAULT_OVERLOAD_RETRY_POLICY.initial_delay_seconds == 0.25
    assert DEFAULT_OVERLOAD_RETRY_POLICY.max_delay_seconds == 2.0
    assert DEFAULT_OVERLOAD_RETRY_POLICY.backoff_multiplier == 2.0
    assert DEFAULT_OVERLOAD_RETRY_POLICY.jitter_ratio == 0.2


@pytest.mark.parametrize(
    ("factory", "expected_message"),
    [
        (lambda: OverloadRetryPolicy(max_attempts=0), "max_attempts"),
        (lambda: OverloadRetryPolicy(initial_delay_seconds=-0.1), "initial_delay_seconds"),
        (lambda: OverloadRetryPolicy(max_delay_seconds=-0.1), "max_delay_seconds"),
        (
            lambda: OverloadRetryPolicy(initial_delay_seconds=2.0, max_delay_seconds=1.0),
            "max_delay_seconds must be >= initial_delay_seconds",
        ),
        (lambda: OverloadRetryPolicy(backoff_multiplier=0.5), "backoff_multiplier"),
        (lambda: OverloadRetryPolicy(jitter_ratio=1.5), "jitter_ratio"),
    ],
)
def test_overload_retry_policy_validates_invariants(
    factory: Callable[[], OverloadRetryPolicy],
    expected_message: str,
) -> None:
    with pytest.raises(ValueError, match=expected_message):
        factory()


@pytest.mark.asyncio
async def test_retry_on_overload_retries_with_backoff_and_jitter() -> None:
    attempts = 0
    sleep_calls: list[float] = []
    jitter_values = iter((0.0, 1.0))
    retry_policy = OverloadRetryPolicy(
        max_attempts=4,
        initial_delay_seconds=0.5,
        max_delay_seconds=5.0,
        backoff_multiplier=2.0,
        jitter_ratio=0.25,
    )

    async def _operation() -> dict[str, bool]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RetryableOverloadError(
                -32001,
                "Server overloaded; retry later.",
                method="thread/read",
                request_id=7,
            )
        return {"ok": True}

    result = await retry_on_overload(
        _operation,
        retry_policy=retry_policy,
        sleep=lambda delay_seconds: _record_sleep(delay_seconds, sleep_calls),
        random_jitter=lambda: next(jitter_values),
    )

    assert result == {"ok": True}
    assert attempts == 3
    assert sleep_calls == pytest.approx([0.375, 1.25])


@pytest.mark.asyncio
async def test_retry_on_overload_raises_budget_exhausted_with_last_error_context() -> None:
    attempts = 0
    sleep_calls: list[float] = []
    retry_policy = OverloadRetryPolicy(
        max_attempts=3,
        initial_delay_seconds=0.1,
        max_delay_seconds=1.0,
        backoff_multiplier=2.0,
        jitter_ratio=0.0,
    )

    async def _operation() -> object:
        nonlocal attempts
        attempts += 1
        raise RetryableOverloadError(
            -32001,
            "Server overloaded; retry later.",
            data={"retryAfterMs": 250},
            method="thread/read",
            request_id="req-9",
        )

    with pytest.raises(RetryBudgetExceededError) as exc_info:
        await retry_on_overload(
            _operation,
            retry_policy=retry_policy,
            sleep=lambda delay_seconds: _record_sleep(delay_seconds, sleep_calls),
        )

    error = exc_info.value
    assert attempts == 3
    assert sleep_calls == [0.1, 0.2]
    assert error.code == -32001
    assert error.method == "thread/read"
    assert error.request_id == "req-9"
    assert error.data == {"retryAfterMs": 250}
    assert "local overload retry budget exhausted after 3 attempts" in str(error)
    assert isinstance(exc_info.value.__cause__, RetryableOverloadError)


@pytest.mark.asyncio
async def test_retry_on_overload_does_not_retry_non_retryable_error() -> None:
    attempts = 0
    sleep_calls: list[float] = []

    async def _operation() -> object:
        nonlocal attempts
        attempts += 1
        raise JsonRpcInternalError(-32603, "boom", method="thread/start")

    with pytest.raises(JsonRpcInternalError):
        await retry_on_overload(
            _operation,
            sleep=lambda delay_seconds: _record_sleep(delay_seconds, sleep_calls),
        )

    assert attempts == 1
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_retry_on_overload_does_not_retry_server_budget_exhaustion() -> None:
    attempts = 0

    async def _operation() -> object:
        nonlocal attempts
        attempts += 1
        raise RetryBudgetExceededError(
            -32001,
            "Server overloaded; retry budget exhausted.",
            method="thread/read",
            request_id="req-1",
        )

    with pytest.raises(RetryBudgetExceededError):
        await retry_on_overload(_operation)

    assert attempts == 1


@pytest.mark.asyncio
async def test_retry_on_overload_preserves_caller_cancellation_during_backoff() -> None:
    cancelled_sleep = _CancelledSleep()

    async def _operation() -> object:
        raise RetryableOverloadError(-32001, "Server overloaded; retry later.")

    with pytest.raises(asyncio.CancelledError):
        await retry_on_overload(
            _operation,
            retry_policy=OverloadRetryPolicy(max_attempts=3, jitter_ratio=0.0),
            sleep=cancelled_sleep,
        )

    assert cancelled_sleep.calls == 1

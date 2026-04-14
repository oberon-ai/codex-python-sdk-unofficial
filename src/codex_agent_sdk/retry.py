"""Opt-in overload retry helpers for low-risk app-server operations."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from .errors import RetryableOverloadError, RetryBudgetExceededError

DEFAULT_OVERLOAD_MAX_ATTEMPTS = 4
DEFAULT_OVERLOAD_INITIAL_DELAY_SECONDS = 0.25
DEFAULT_OVERLOAD_MAX_DELAY_SECONDS = 2.0
DEFAULT_OVERLOAD_BACKOFF_MULTIPLIER = 2.0
DEFAULT_OVERLOAD_JITTER_RATIO = 0.2

_T = TypeVar("_T")
_AsyncOperation = Callable[[], Awaitable[_T]]
_AsyncSleep = Callable[[float], Awaitable[object]]
_RandomJitter = Callable[[], float]


@dataclass(frozen=True, slots=True)
class OverloadRetryPolicy:
    """Configuration for opt-in retry after transient app-server overload."""

    max_attempts: int = DEFAULT_OVERLOAD_MAX_ATTEMPTS
    initial_delay_seconds: float = DEFAULT_OVERLOAD_INITIAL_DELAY_SECONDS
    max_delay_seconds: float = DEFAULT_OVERLOAD_MAX_DELAY_SECONDS
    backoff_multiplier: float = DEFAULT_OVERLOAD_BACKOFF_MULTIPLIER
    jitter_ratio: float = DEFAULT_OVERLOAD_JITTER_RATIO

    def __post_init__(self) -> None:
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if self.initial_delay_seconds < 0:
            raise ValueError("initial_delay_seconds must be non-negative")
        if self.max_delay_seconds < 0:
            raise ValueError("max_delay_seconds must be non-negative")
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("max_delay_seconds must be >= initial_delay_seconds")
        if self.backoff_multiplier < 1.0:
            raise ValueError("backoff_multiplier must be >= 1.0")
        if not 0.0 <= self.jitter_ratio <= 1.0:
            raise ValueError("jitter_ratio must be between 0.0 and 1.0")


DEFAULT_OVERLOAD_RETRY_POLICY = OverloadRetryPolicy()


async def retry_on_overload(
    operation: _AsyncOperation[_T],
    *,
    retry_policy: OverloadRetryPolicy | None = None,
    sleep: _AsyncSleep = asyncio.sleep,
    random_jitter: _RandomJitter | None = None,
) -> _T:
    """Retry one async operation when the app-server reports transient overload.

    This helper is intentionally opt-in. Callers should use it for startup flows
    or read-only operations where replay is safe, and avoid it for mutating or
    approval-sensitive calls unless they have their own idempotency guarantees.
    """

    policy = DEFAULT_OVERLOAD_RETRY_POLICY if retry_policy is None else retry_policy
    jitter = random.random if random_jitter is None else random_jitter
    last_error: RetryableOverloadError | None = None

    for attempt_number in range(1, policy.max_attempts + 1):
        try:
            return await operation()
        except RetryBudgetExceededError:
            raise
        except RetryableOverloadError as exc:
            last_error = exc
            if attempt_number >= policy.max_attempts:
                budget_error = RetryBudgetExceededError(
                    exc.code,
                    (
                        f"{exc.rpc_message}; local overload retry budget exhausted "
                        f"after {policy.max_attempts} attempts"
                    ),
                    data=exc.data,
                    method=exc.method,
                    request_id=exc.request_id,
                )
                raise budget_error from exc

            delay_seconds = _compute_retry_delay_seconds(
                attempt_number=attempt_number,
                retry_policy=policy,
                random_jitter=jitter,
            )
            await sleep(delay_seconds)

    assert last_error is not None
    raise last_error


def _compute_retry_delay_seconds(
    *,
    attempt_number: int,
    retry_policy: OverloadRetryPolicy,
    random_jitter: _RandomJitter,
) -> float:
    if attempt_number <= 0:
        raise ValueError("attempt_number must be positive")

    base_delay_seconds = min(
        retry_policy.max_delay_seconds,
        retry_policy.initial_delay_seconds
        * (retry_policy.backoff_multiplier ** (attempt_number - 1)),
    )
    if retry_policy.jitter_ratio == 0.0 or base_delay_seconds == 0.0:
        return base_delay_seconds

    jitter_sample = min(1.0, max(0.0, random_jitter()))
    jitter_multiplier = ((jitter_sample * 2.0) - 1.0) * retry_policy.jitter_ratio
    return max(0.0, base_delay_seconds * (1.0 + jitter_multiplier))


__all__ = [
    "DEFAULT_OVERLOAD_BACKOFF_MULTIPLIER",
    "DEFAULT_OVERLOAD_INITIAL_DELAY_SECONDS",
    "DEFAULT_OVERLOAD_JITTER_RATIO",
    "DEFAULT_OVERLOAD_MAX_ATTEMPTS",
    "DEFAULT_OVERLOAD_MAX_DELAY_SECONDS",
    "DEFAULT_OVERLOAD_RETRY_POLICY",
    "OverloadRetryPolicy",
    "retry_on_overload",
]

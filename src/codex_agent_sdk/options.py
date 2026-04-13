from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

DEFAULT_STARTUP_TIMEOUT_SECONDS = 20.0
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class TimeoutPolicy:
    """Default local timeout policy for major SDK operation classes.

    A value of ``None`` means the SDK waits until completion or caller cancellation.
    """

    startup: float = DEFAULT_STARTUP_TIMEOUT_SECONDS
    shutdown: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
    rpc_request: float | None = None
    turn_completion: float | None = None
    turn_event_stream: float | None = None
    approval_decision: float | None = None


DEFAULT_TIMEOUT_POLICY = TimeoutPolicy()


@dataclass(slots=True)
class AppServerConfig:
    """Process and protocol bootstrap configuration for ``codex app-server``."""

    codex_bin: str | None = None
    extra_args: tuple[str, ...] = ()
    env: Mapping[str, str] | None = None
    startup_timeout: float = DEFAULT_TIMEOUT_POLICY.startup
    shutdown_timeout: float = DEFAULT_TIMEOUT_POLICY.shutdown
    client_name: str = "codex_agent_sdk"
    client_title: str = "Codex Agent SDK"
    client_version: str = "0.0.0"
    experimental_api: bool = False


@dataclass(slots=True)
class CodexOptions:
    """High-level thread and turn defaults exposed by the SDK."""

    model: str | None = None
    cwd: str | None = None
    approval_policy: str | None = None
    sandbox_policy: str | None = None
    approvals_reviewer: str | None = None
    effort: str | None = None
    summary: str | None = None
    personality: str | None = None

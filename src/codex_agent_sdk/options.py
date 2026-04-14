from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeVar

from pydantic import BaseModel

from .generated.stable import (
    ApprovalsReviewer,
    AskForApproval,
    Personality,
    ReasoningEffort,
    ReasoningSummary,
    SandboxMode,
    SandboxPolicy,
    ServiceTier,
)

DEFAULT_STARTUP_TIMEOUT_SECONDS = 20.0
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 5.0

EnumValueT = TypeVar("EnumValueT", bound=Enum)
WireModelT = TypeVar("WireModelT", bound=BaseModel)
NonNoneValueT = TypeVar("NonNoneValueT")


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
    cwd: str | None = None
    env: Mapping[str, str] | None = None
    startup_timeout: float = DEFAULT_TIMEOUT_POLICY.startup
    shutdown_timeout: float = DEFAULT_TIMEOUT_POLICY.shutdown
    client_name: str = "codex_agent_sdk"
    client_title: str = "Codex Agent SDK"
    client_version: str = "0.0.0"
    experimental_api: bool = False
    opt_out_notification_methods: tuple[str, ...] = ()
    debug_logging: bool = False
    debug_logger: logging.Logger | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True, init=False)
class CodexOptions:
    """High-level defaults for thread and turn behavior.

    ``CodexOptions`` intentionally models sticky Codex behavior defaults rather
    than transport/bootstrap concerns:

    - process launch, subprocess environment, and initialization capabilities
      belong on :class:`AppServerConfig`
    - current-turn-only values such as ``output_schema`` stay on the turn call
      instead of becoming sticky defaults here

    The stored field values are normalized to the generated stable SDK types so
    later client code can use them directly without re-parsing strings or
    mappings.
    """

    model: str | None
    cwd: str | None
    approval_policy: AskForApproval | None
    approvals_reviewer: ApprovalsReviewer | None
    effort: ReasoningEffort | None
    summary: ReasoningSummary | None
    personality: Personality | None
    service_tier: ServiceTier | None
    sandbox_mode: SandboxMode | None
    sandbox_policy: SandboxPolicy | None
    base_instructions: str | None
    developer_instructions: str | None

    def __init__(
        self,
        *,
        model: str | None = None,
        cwd: str | None = None,
        approval_policy: AskForApproval | str | Mapping[str, object] | None = None,
        approvals_reviewer: ApprovalsReviewer | str | None = None,
        effort: ReasoningEffort | str | None = None,
        summary: ReasoningSummary | str | None = None,
        personality: Personality | str | None = None,
        service_tier: ServiceTier | str | None = None,
        sandbox_mode: SandboxMode | str | None = None,
        sandbox_policy: SandboxPolicy | Mapping[str, object] | None = None,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
    ) -> None:
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(
            self,
            "approval_policy",
            _coerce_wire_model(AskForApproval, approval_policy),
        )
        object.__setattr__(
            self,
            "approvals_reviewer",
            _coerce_enum(ApprovalsReviewer, approvals_reviewer),
        )
        object.__setattr__(self, "effort", _coerce_enum(ReasoningEffort, effort))
        object.__setattr__(
            self,
            "summary",
            _coerce_wire_model(ReasoningSummary, summary),
        )
        object.__setattr__(self, "personality", _coerce_enum(Personality, personality))
        object.__setattr__(
            self,
            "service_tier",
            _coerce_enum(ServiceTier, service_tier),
        )
        object.__setattr__(
            self,
            "sandbox_mode",
            _coerce_enum(SandboxMode, sandbox_mode),
        )
        object.__setattr__(
            self,
            "sandbox_policy",
            _coerce_wire_model(SandboxPolicy, sandbox_policy),
        )
        object.__setattr__(self, "base_instructions", base_instructions)
        object.__setattr__(self, "developer_instructions", developer_instructions)

    @classmethod
    def merge(cls, *layers: CodexOptions | None) -> CodexOptions:
        """Merge multiple option layers using last-non-``None`` precedence.

        This is the intended precedence rule for high-level SDK flows:

        1. client defaults
        2. thread lifecycle defaults
        3. per-turn overrides
        """

        present_layers = [layer for layer in layers if layer is not None]
        return cls(
            model=_last_non_none(layer.model for layer in present_layers),
            cwd=_last_non_none(layer.cwd for layer in present_layers),
            approval_policy=_last_non_none(layer.approval_policy for layer in present_layers),
            approvals_reviewer=_last_non_none(layer.approvals_reviewer for layer in present_layers),
            effort=_last_non_none(layer.effort for layer in present_layers),
            summary=_last_non_none(layer.summary for layer in present_layers),
            personality=_last_non_none(layer.personality for layer in present_layers),
            service_tier=_last_non_none(layer.service_tier for layer in present_layers),
            sandbox_mode=_last_non_none(layer.sandbox_mode for layer in present_layers),
            sandbox_policy=_last_non_none(layer.sandbox_policy for layer in present_layers),
            base_instructions=_last_non_none(layer.base_instructions for layer in present_layers),
            developer_instructions=_last_non_none(
                layer.developer_instructions for layer in present_layers
            ),
        )

    def merged_with(self, overrides: CodexOptions | None) -> CodexOptions:
        """Return a copy with another option layer overlaid on top of this one."""

        return type(self).merge(self, overrides)

    @property
    def effective_sandbox_mode(self) -> SandboxMode | None:
        """Return the coarse thread-level sandbox mode for lifecycle calls."""

        if self.sandbox_mode is not None:
            return self.sandbox_mode
        return _sandbox_mode_from_policy(self.sandbox_policy)

    @property
    def effective_sandbox_policy(self) -> SandboxPolicy | None:
        """Return the richer turn-level sandbox policy for turn-start calls."""

        if self.sandbox_policy is not None:
            return self.sandbox_policy
        return _sandbox_policy_from_mode(self.sandbox_mode)

    def to_thread_start_kwargs(
        self,
        *,
        ephemeral: bool | None = None,
    ) -> dict[str, object]:
        """Project the stored defaults onto ``thread/start`` kwargs."""

        return self._thread_kwargs(include_personality=True, ephemeral=ephemeral)

    def to_thread_resume_kwargs(self) -> dict[str, object]:
        """Project the stored defaults onto ``thread/resume`` kwargs."""

        return self._thread_kwargs(include_personality=True, ephemeral=None)

    def to_thread_fork_kwargs(
        self,
        *,
        ephemeral: bool | None = None,
    ) -> dict[str, object]:
        """Project the stored defaults onto ``thread/fork`` kwargs.

        The current stable protocol does not accept ``personality`` on
        ``thread/fork``, so that field is intentionally excluded here even when
        it is present on the options object.
        """

        return self._thread_kwargs(include_personality=False, ephemeral=ephemeral)

    def to_turn_start_kwargs(self) -> dict[str, object]:
        """Project the stored defaults onto ``turn/start`` kwargs."""

        kwargs: dict[str, object] = {}
        if self.model is not None:
            kwargs["model"] = self.model
        if self.cwd is not None:
            kwargs["cwd"] = self.cwd
        if self.approval_policy is not None:
            kwargs["approval_policy"] = self.approval_policy
        if self.approvals_reviewer is not None:
            kwargs["approvals_reviewer"] = self.approvals_reviewer
        if self.effort is not None:
            kwargs["effort"] = self.effort
        if self.summary is not None:
            kwargs["summary"] = self.summary
        if self.personality is not None:
            kwargs["personality"] = self.personality
        if self.service_tier is not None:
            kwargs["service_tier"] = self.service_tier
        sandbox_policy = self.effective_sandbox_policy
        if sandbox_policy is not None:
            kwargs["sandbox_policy"] = sandbox_policy
        return kwargs

    def _thread_kwargs(
        self,
        *,
        include_personality: bool,
        ephemeral: bool | None,
    ) -> dict[str, object]:
        kwargs: dict[str, object] = {}
        if self.model is not None:
            kwargs["model"] = self.model
        if self.cwd is not None:
            kwargs["cwd"] = self.cwd
        if self.approval_policy is not None:
            kwargs["approval_policy"] = self.approval_policy
        if self.approvals_reviewer is not None:
            kwargs["approvals_reviewer"] = self.approvals_reviewer
        if self.base_instructions is not None:
            kwargs["base_instructions"] = self.base_instructions
        if self.developer_instructions is not None:
            kwargs["developer_instructions"] = self.developer_instructions
        if include_personality and self.personality is not None:
            kwargs["personality"] = self.personality
        if self.service_tier is not None:
            kwargs["service_tier"] = self.service_tier
        sandbox_mode = self.effective_sandbox_mode
        if sandbox_mode is not None:
            kwargs["sandbox"] = sandbox_mode
        if ephemeral is not None:
            kwargs["ephemeral"] = ephemeral
        return kwargs


def _coerce_enum(
    enum_type: type[EnumValueT],
    value: EnumValueT | str | None,
) -> EnumValueT | None:
    if value is None or isinstance(value, enum_type):
        return value
    return enum_type(value)


def _coerce_wire_model(
    model_type: type[WireModelT],
    value: WireModelT | str | Mapping[str, object] | None,
) -> WireModelT | None:
    if value is None or isinstance(value, model_type):
        return value
    return model_type.model_validate(value)


def _last_non_none(values: Iterable[NonNoneValueT | None]) -> NonNoneValueT | None:
    last: NonNoneValueT | None = None
    for value in values:
        if value is not None:
            last = value
    return last


def _sandbox_mode_from_policy(policy: SandboxPolicy | None) -> SandboxMode | None:
    if policy is None:
        return None

    policy_type = policy.model_dump().get("type")
    if policy_type == "dangerFullAccess":
        return SandboxMode.danger_full_access
    if policy_type == "readOnly":
        return SandboxMode.read_only
    if policy_type == "workspaceWrite":
        return SandboxMode.workspace_write
    return None


def _sandbox_policy_from_mode(mode: SandboxMode | None) -> SandboxPolicy | None:
    if mode is None:
        return None
    if mode is SandboxMode.danger_full_access:
        return SandboxPolicy.model_validate({"type": "dangerFullAccess"})
    if mode is SandboxMode.read_only:
        return SandboxPolicy.model_validate({"type": "readOnly"})
    return SandboxPolicy.model_validate({"type": "workspaceWrite"})

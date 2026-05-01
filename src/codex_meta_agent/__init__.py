"""Automation helpers for tracking the upstream ``openai/codex`` repository.

This package is intentionally separate from ``codex_agent_sdk`` itself. It
uses the SDK as an orchestration tool for repository maintenance workflows such
as upstream drift detection, release-tracking updates, and stable-release planning.
"""

from __future__ import annotations

from .release_metadata import (
    DEFAULT_RELEASE_TAG_PREFIX,
    build_release_tag,
    normalize_release_version,
)

_VERSION_TRACKER_EXPORT_NAMES = {
    "BackportCompareStats",
    "BackportDiffStats",
    "BackportEffortThresholds",
    "DEFAULT_CONTEXT_DIR",
    "DEFAULT_BACKPORT_BRANCH_PREFIX",
    "DEFAULT_BACKPORT_TAG_PREFIX",
    "DEFAULT_REPORT_PATH",
    "DEFAULT_STATE_PATH",
    "DEFAULT_TRACKING_BRANCH_PREFIX",
    "DEFAULT_UPSTREAM_REPOSITORY",
    "DEFAULT_VERIFICATION_COMMANDS",
    "GitHubCompareCommit",
    "GitHubCompareFile",
    "GitHubCompareResult",
    "GitHubRelease",
    "TrackerResponse",
    "TrackingState",
    "UpstreamSnapshot",
    "UpstreamTrackingTarget",
    "VersionTracker",
    "VersionTrackerConfig",
    "VersionTrackerResult",
    "HistoricalBackportEntry",
    "assess_backport_effort",
    "build_backport_branch",
    "build_backport_tag",
    "build_tracking_branch",
    "collect_compare_stats",
    "collect_diff_stats",
    "execute_historical_backports",
    "historical_releases_from_state",
    "main",
    "parse_tracker_response",
    "plan_historical_backports",
    "render_tracker_prompt",
}


def __getattr__(name: str) -> object:
    if name in {
        "BackportCompareStats",
        "BackportDiffStats",
        "BackportEffortThresholds",
        "DEFAULT_BACKPORT_BRANCH_PREFIX",
        "DEFAULT_BACKPORT_TAG_PREFIX",
        "DEFAULT_REPORT_PATH",
        "HistoricalBackportEntry",
        "assess_backport_effort",
        "build_backport_branch",
        "build_backport_tag",
        "collect_compare_stats",
        "collect_diff_stats",
        "execute_historical_backports",
        "historical_releases_from_state",
        "plan_historical_backports",
    }:
        from . import backport_history as _backport_history

        value = getattr(_backport_history, name)
        globals()[name] = value
        return value
    if name in _VERSION_TRACKER_EXPORT_NAMES:
        from . import version_tracker as _version_tracker

        value = getattr(_version_tracker, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BackportCompareStats",
    "BackportDiffStats",
    "BackportEffortThresholds",
    "DEFAULT_BACKPORT_BRANCH_PREFIX",
    "DEFAULT_BACKPORT_TAG_PREFIX",
    "DEFAULT_REPORT_PATH",
    "DEFAULT_CONTEXT_DIR",
    "DEFAULT_RELEASE_TAG_PREFIX",
    "DEFAULT_STATE_PATH",
    "DEFAULT_UPSTREAM_REPOSITORY",
    "DEFAULT_VERIFICATION_COMMANDS",
    "DEFAULT_TRACKING_BRANCH_PREFIX",
    "GitHubCompareCommit",
    "GitHubCompareFile",
    "GitHubCompareResult",
    "GitHubRelease",
    "HistoricalBackportEntry",
    "TrackingState",
    "TrackerResponse",
    "UpstreamSnapshot",
    "UpstreamTrackingTarget",
    "VersionTracker",
    "VersionTrackerConfig",
    "VersionTrackerResult",
    "assess_backport_effort",
    "build_backport_branch",
    "build_backport_tag",
    "build_release_tag",
    "build_tracking_branch",
    "collect_compare_stats",
    "collect_diff_stats",
    "execute_historical_backports",
    "historical_releases_from_state",
    "main",
    "normalize_release_version",
    "parse_tracker_response",
    "plan_historical_backports",
    "render_tracker_prompt",
]

"""Automation helpers for tracking the upstream ``openai/codex`` repository.

This package is intentionally separate from ``codex_agent_sdk`` itself. It
uses the SDK as an orchestration tool for repository maintenance workflows such
as upstream drift detection, release-tracking updates, and stable-release planning.
"""

from .version_tracker import (
    DEFAULT_CONTEXT_DIR,
    DEFAULT_RELEASE_TAG_PREFIX,
    DEFAULT_STATE_PATH,
    DEFAULT_TRACKING_BRANCH_PREFIX,
    DEFAULT_UPSTREAM_REPOSITORY,
    DEFAULT_VERIFICATION_COMMANDS,
    GitHubCompareCommit,
    GitHubCompareFile,
    GitHubCompareResult,
    GitHubRelease,
    TrackerResponse,
    TrackingState,
    UpstreamSnapshot,
    UpstreamTrackingTarget,
    VersionTracker,
    VersionTrackerConfig,
    VersionTrackerResult,
    build_release_tag,
    build_tracking_branch,
    main,
    normalize_release_version,
    parse_tracker_response,
    render_tracker_prompt,
)

__all__ = [
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
    "TrackingState",
    "TrackerResponse",
    "UpstreamSnapshot",
    "UpstreamTrackingTarget",
    "VersionTracker",
    "VersionTrackerConfig",
    "VersionTrackerResult",
    "build_release_tag",
    "build_tracking_branch",
    "main",
    "normalize_release_version",
    "parse_tracker_response",
    "render_tracker_prompt",
]

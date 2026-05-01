"""Plan or execute dead-end backports for historical Codex releases."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol

from .release_metadata import normalize_release_version
from .version_tracker import (
    DEFAULT_STATE_PATH,
    DEFAULT_UPSTREAM_REPOSITORY,
    GitHubApiClient,
    GitHubCompareResult,
    GitHubRelease,
    TrackingState,
    VersionTracker,
    VersionTrackerConfig,
    build_tracking_branch,
)

DEFAULT_BACKPORT_BRANCH_PREFIX = "puck/backport-release--"
DEFAULT_BACKPORT_TAG_PREFIX = "backport-v"
DEFAULT_REPORT_PATH = Path(".codex-meta-agent/backport-history-report.json")


@dataclass(frozen=True, slots=True)
class BackportEffortThresholds:
    compare_commit_count: int = 12
    compare_file_count: int = 20
    compare_line_delta: int = 1200
    diff_file_count: int = 15
    diff_line_delta: int = 800


DEFAULT_EFFORT_THRESHOLDS = BackportEffortThresholds()


@dataclass(frozen=True, slots=True)
class BackportCompareStats:
    commits: int
    files_changed: int
    additions: int
    deletions: int
    line_delta: int
    compare_url: str


@dataclass(frozen=True, slots=True)
class BackportDiffStats:
    files_changed: int
    insertions: int
    deletions: int
    line_delta: int


@dataclass(frozen=True, slots=True)
class HistoricalBackportEntry:
    release_tag: str
    release_version: str
    published_at: str
    branch_name: str
    tag_name: str
    compare_stats: BackportCompareStats
    major: bool
    notes: tuple[str, ...]
    status: str = "planned"
    diff_stats: BackportDiffStats | None = None
    prompt_path: str | None = None
    response_path: str | None = None
    worktree_path: str | None = None
    error: str | None = None


class BackportHistoryGitHubClient(Protocol):
    def list_releases(
        self,
        *,
        stable_only: bool = True,
        limit: int | None = None,
    ) -> tuple[GitHubRelease, ...]: ...

    def compare_commits(self, base: str, head: str) -> GitHubCompareResult: ...


def build_backport_branch(
    upstream_tag: str,
    *,
    prefix: str = DEFAULT_BACKPORT_BRANCH_PREFIX,
) -> str:
    return build_tracking_branch(upstream_tag, prefix=prefix)


def build_backport_tag(
    upstream_tag: str,
    *,
    prefix: str = DEFAULT_BACKPORT_TAG_PREFIX,
) -> str:
    return f"{prefix}{normalize_release_version(upstream_tag)}"


def collect_compare_stats(compare: GitHubCompareResult) -> BackportCompareStats:
    additions = sum(file.additions for file in compare.files)
    deletions = sum(file.deletions for file in compare.files)
    return BackportCompareStats(
        commits=compare.total_commits,
        files_changed=len(compare.files),
        additions=additions,
        deletions=deletions,
        line_delta=additions + deletions,
        compare_url=compare.html_url,
    )


def assess_backport_effort(
    compare_stats: BackportCompareStats,
    *,
    diff_stats: BackportDiffStats | None = None,
    thresholds: BackportEffortThresholds | None = None,
) -> tuple[bool, tuple[str, ...]]:
    if thresholds is None:
        thresholds = DEFAULT_EFFORT_THRESHOLDS
    notes: list[str] = []
    if compare_stats.commits >= thresholds.compare_commit_count:
        notes.append(
            "upstream compare spans "
            f"{compare_stats.commits} commits (threshold {thresholds.compare_commit_count})"
        )
    if compare_stats.files_changed >= thresholds.compare_file_count:
        notes.append(
            "upstream compare touches "
            f"{compare_stats.files_changed} files (threshold {thresholds.compare_file_count})"
        )
    if compare_stats.line_delta >= thresholds.compare_line_delta:
        notes.append(
            "upstream compare changes "
            f"{compare_stats.line_delta} lines (threshold {thresholds.compare_line_delta})"
        )
    if diff_stats is not None and diff_stats.files_changed >= thresholds.diff_file_count:
        notes.append(
            "prepared backport changes "
            f"{diff_stats.files_changed} files from main (threshold {thresholds.diff_file_count})"
        )
    if diff_stats is not None and diff_stats.line_delta >= thresholds.diff_line_delta:
        notes.append(
            "prepared backport changes "
            f"{diff_stats.line_delta} lines from main (threshold {thresholds.diff_line_delta})"
        )
    return bool(notes), tuple(notes)


def historical_releases_from_state(
    current_release: GitHubRelease,
    releases: tuple[GitHubRelease, ...],
    *,
    oldest_version: str | None = None,
    limit: int | None = None,
) -> tuple[GitHubRelease, ...]:
    selected = [
        release
        for release in releases
        if release.tag_name != current_release.tag_name
        and release.published_at < current_release.published_at
    ]
    selected.sort(key=lambda release: release.published_at, reverse=True)

    if oldest_version is not None:
        normalized_oldest = normalize_release_version(oldest_version)
        bounded: list[GitHubRelease] = []
        for release in selected:
            bounded.append(release)
            if normalize_release_version(release.tag_name) == normalized_oldest:
                break
        selected = bounded

    if limit is not None:
        selected = selected[:limit]
    return tuple(selected)


def plan_historical_backports(
    current_state: TrackingState,
    github_client: BackportHistoryGitHubClient,
    *,
    oldest_version: str | None = None,
    limit: int | None = None,
    thresholds: BackportEffortThresholds | None = None,
) -> tuple[HistoricalBackportEntry, ...]:
    if thresholds is None:
        thresholds = DEFAULT_EFFORT_THRESHOLDS
    releases = github_client.list_releases(stable_only=True)
    planned_releases = historical_releases_from_state(
        current_state.last_seen_release,
        releases,
        oldest_version=oldest_version,
        limit=limit,
    )
    entries: list[HistoricalBackportEntry] = []
    current_tag = current_state.last_seen_release.tag_name
    for release in planned_releases:
        compare = github_client.compare_commits(release.tag_name, current_tag)
        compare_stats = collect_compare_stats(compare)
        major, notes = assess_backport_effort(compare_stats, thresholds=thresholds)
        entries.append(
            HistoricalBackportEntry(
                release_tag=release.tag_name,
                release_version=normalize_release_version(release.tag_name),
                published_at=release.published_at,
                branch_name=build_backport_branch(release.tag_name),
                tag_name=build_backport_tag(release.tag_name),
                compare_stats=compare_stats,
                major=major,
                notes=notes,
            )
        )
    return tuple(entries)


def execute_historical_backports(
    entries: tuple[HistoricalBackportEntry, ...],
    *,
    repo_root: Path,
    main_ref: str,
    github_token: str | None = None,
    model: str = "gpt-5.4",
    codex_bin: str = "codex",
    skip_verification: bool = False,
    keep_worktrees: bool = False,
    thresholds: BackportEffortThresholds | None = None,
) -> tuple[HistoricalBackportEntry, ...]:
    if thresholds is None:
        thresholds = DEFAULT_EFFORT_THRESHOLDS
    _maybe_fetch_ref(repo_root, main_ref)
    executed: list[HistoricalBackportEntry] = []
    for entry in entries:
        worktree_path = Path(
            tempfile.mkdtemp(prefix=f"codex-backport-{entry.release_version.replace('.', '-')}-")
        )
        _run_command(
            ["git", "worktree", "add", "--detach", str(worktree_path), main_ref],
            cwd=repo_root,
        )
        executed_entry: HistoricalBackportEntry | None = None
        try:
            tracker = VersionTracker(
                VersionTrackerConfig(
                    repo_root=worktree_path,
                    upstream_repository=DEFAULT_UPSTREAM_REPOSITORY,
                    tracking_branch_prefix=DEFAULT_BACKPORT_BRANCH_PREFIX,
                    target_version=entry.release_version,
                    model=model,
                    codex_bin=codex_bin,
                    github_token=github_token,
                    run_verification=not skip_verification,
                )
            )
            result = tracker.run()
            diff_stats = collect_diff_stats(worktree_path)
            major, notes = assess_backport_effort(
                entry.compare_stats,
                diff_stats=diff_stats,
                thresholds=thresholds,
            )
            executed_entry = replace(
                entry,
                major=major,
                notes=notes,
                status="prepared",
                diff_stats=diff_stats,
                prompt_path=str(result.prompt_path),
                response_path=str(result.response_path) if result.response_path else None,
                worktree_path=str(worktree_path),
            )
        except Exception as exc:
            executed_entry = replace(
                entry,
                status="failed",
                notes=entry.notes + (f"backport preparation failed: {exc}",),
                worktree_path=str(worktree_path),
                error=str(exc),
            )
        finally:
            if not keep_worktrees:
                _run_command(
                    ["git", "worktree", "remove", "--force", str(worktree_path)],
                    cwd=repo_root,
                )
        assert executed_entry is not None
        if not keep_worktrees:
            executed_entry = replace(executed_entry, worktree_path=None)
        executed.append(executed_entry)
    return tuple(executed)


def collect_diff_stats(repo_root: Path) -> BackportDiffStats:
    completed = _capture_command(["git", "diff", "--numstat"], cwd=repo_root)
    files_changed = 0
    insertions = 0
    deletions = 0
    for line in completed.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files_changed += 1
        if parts[0].isdigit():
            insertions += int(parts[0])
        if parts[1].isdigit():
            deletions += int(parts[1])
    return BackportDiffStats(
        files_changed=files_changed,
        insertions=insertions,
        deletions=deletions,
        line_delta=insertions + deletions,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Walk historical stable Codex releases from the repository's current main-tracked "
            "state backward in time, optionally preparing each backport from a fresh main worktree."
        )
    )
    parser.add_argument("--repo-root", default=".", help="Repository root to inspect.")
    parser.add_argument(
        "--state-path",
        default=str(DEFAULT_STATE_PATH),
        help="Repository-relative path to the committed upstream tracking state file.",
    )
    parser.add_argument(
        "--main-ref",
        default="origin/main",
        help="Git ref to use as the clean starting point for each prepared backport.",
    )
    parser.add_argument(
        "--oldest-version",
        help="Optional oldest release version to include before stopping the walk.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of historical releases to include.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually prepare each historical backport in a fresh worktree from main.",
    )
    parser.add_argument(
        "--skip-verification",
        action="store_true",
        help="Skip repository verification when --execute is used.",
    )
    parser.add_argument(
        "--keep-worktrees",
        action="store_true",
        help="Keep prepared temporary worktrees on disk for inspection.",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.4",
        help="Codex model name to use when --execute is enabled.",
    )
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Codex CLI binary name to use when --execute is enabled.",
    )
    parser.add_argument(
        "--report-path",
        default=str(DEFAULT_REPORT_PATH),
        help="Repository-relative path for the JSON report this command writes.",
    )
    parser.add_argument(
        "--major-compare-commits",
        type=int,
        default=DEFAULT_EFFORT_THRESHOLDS.compare_commit_count,
        help="Commit-count threshold for flagging a backport as major.",
    )
    parser.add_argument(
        "--major-compare-files",
        type=int,
        default=DEFAULT_EFFORT_THRESHOLDS.compare_file_count,
        help="Changed-file threshold for flagging a backport as major from upstream compare data.",
    )
    parser.add_argument(
        "--major-compare-lines",
        type=int,
        default=DEFAULT_EFFORT_THRESHOLDS.compare_line_delta,
        help="Changed-line threshold for flagging a backport as major from upstream compare data.",
    )
    parser.add_argument(
        "--major-diff-files",
        type=int,
        default=DEFAULT_EFFORT_THRESHOLDS.diff_file_count,
        help="Changed-file threshold for flagging a prepared backport as major.",
    )
    parser.add_argument(
        "--major-diff-lines",
        type=int,
        default=DEFAULT_EFFORT_THRESHOLDS.diff_line_delta,
        help="Changed-line threshold for flagging a prepared backport as major.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    state_path = repo_root / args.state_path
    if not state_path.exists():
        raise SystemExit(f"tracking state file does not exist: {state_path}")
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"tracking state file must contain a JSON object: {state_path}")
    current_state = TrackingState.from_dict(payload)
    thresholds = BackportEffortThresholds(
        compare_commit_count=args.major_compare_commits,
        compare_file_count=args.major_compare_files,
        compare_line_delta=args.major_compare_lines,
        diff_file_count=args.major_diff_files,
        diff_line_delta=args.major_diff_lines,
    )

    github_client = GitHubApiClient(
        DEFAULT_UPSTREAM_REPOSITORY,
        token=_env("GITHUB_TOKEN"),
    )
    entries = plan_historical_backports(
        current_state,
        github_client,
        oldest_version=args.oldest_version,
        limit=args.limit,
        thresholds=thresholds,
    )
    if args.execute and entries:
        entries = execute_historical_backports(
            entries,
            repo_root=repo_root,
            main_ref=args.main_ref,
            github_token=_env("GITHUB_TOKEN"),
            model=args.model,
            codex_bin=args.codex_bin,
            skip_verification=args.skip_verification,
            keep_worktrees=args.keep_worktrees,
            thresholds=thresholds,
        )

    report_path = repo_root / args.report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "current_release_tag": current_state.last_seen_release.tag_name,
        "execute": args.execute,
        "main_ref": args.main_ref,
        "count": len(entries),
        "entries": [asdict(entry) for entry in entries],
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        "Historical backport report written to "
        f"{report_path} for {len(entries)} releases from "
        f"{current_state.last_seen_release.tag_name}."
    )
    for entry in entries:
        major_marker = " major" if entry.major else ""
        print(
            f"- {entry.release_version} ({entry.release_tag}) -> {entry.branch_name}{major_marker}"
        )
        for note in entry.notes:
            print(f"  note: {note}")
    return 0


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return value


def _maybe_fetch_ref(repo_root: Path, ref: str) -> None:
    if "/" not in ref:
        return
    remote, branch = ref.split("/", 1)
    if not remote or not branch:
        return
    _run_command(["git", "fetch", remote, branch], cwd=repo_root)


def _run_command(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _capture_command(command: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


__all__ = [
    "BackportCompareStats",
    "BackportDiffStats",
    "BackportEffortThresholds",
    "DEFAULT_BACKPORT_BRANCH_PREFIX",
    "DEFAULT_EFFORT_THRESHOLDS",
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
    "main",
    "plan_historical_backports",
]

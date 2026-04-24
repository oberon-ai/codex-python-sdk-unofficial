from __future__ import annotations

from codex_meta_agent.backport_history import (
    BackportDiffStats,
    BackportEffortThresholds,
    assess_backport_effort,
    build_backport_branch,
    build_backport_tag,
    collect_compare_stats,
    historical_releases_from_state,
    plan_historical_backports,
)
from codex_meta_agent.version_tracker import (
    GitHubCompareFile,
    GitHubCompareResult,
    GitHubRelease,
    TrackingState,
    UpstreamSnapshot,
)


CURRENT_RELEASE = GitHubRelease(
    tag_name="rust-v0.119.0",
    name="0.119.0",
    html_url="https://github.com/openai/codex/releases/tag/rust-v0.119.0",
    published_at="2026-04-10T22:44:21Z",
    target_commitish="main",
    body="",
)
RELEASE_118 = GitHubRelease(
    tag_name="rust-v0.118.0",
    name="0.118.0",
    html_url="https://github.com/openai/codex/releases/tag/rust-v0.118.0",
    published_at="2026-03-31T17:02:18Z",
    target_commitish="main",
    body="",
)
RELEASE_117 = GitHubRelease(
    tag_name="rust-v0.117.0",
    name="0.117.0",
    html_url="https://github.com/openai/codex/releases/tag/rust-v0.117.0",
    published_at="2026-03-26T22:27:39Z",
    target_commitish="main",
    body="",
)
RELEASE_116 = GitHubRelease(
    tag_name="rust-v0.116.0",
    name="0.116.0",
    html_url="https://github.com/openai/codex/releases/tag/rust-v0.116.0",
    published_at="2026-03-19T17:51:35Z",
    target_commitish="main",
    body="",
)


class FakeHistoryGitHubClient:
    def __init__(
        self,
        *,
        releases: tuple[GitHubRelease, ...],
        compares: dict[tuple[str, str], GitHubCompareResult],
    ) -> None:
        self._releases = releases
        self._compares = compares
        self.compare_calls: list[tuple[str, str]] = []

    def list_releases(
        self,
        *,
        stable_only: bool = True,
        limit: int | None = None,
    ) -> tuple[GitHubRelease, ...]:
        del stable_only, limit
        return self._releases

    def compare_commits(self, base: str, head: str) -> GitHubCompareResult:
        self.compare_calls.append((base, head))
        return self._compares[(base, head)]


def _compare(
    *,
    base: str,
    head: str,
    total_commits: int,
    files_changed: int,
    additions: int,
    deletions: int,
) -> GitHubCompareResult:
    files: list[GitHubCompareFile] = []
    for index in range(files_changed):
        file_additions = additions // files_changed if files_changed else 0
        file_deletions = deletions // files_changed if files_changed else 0
        if files_changed and index < additions % files_changed:
            file_additions += 1
        if files_changed and index < deletions % files_changed:
            file_deletions += 1
        files.append(
            GitHubCompareFile(
                filename=f"sdk/python/file_{index}.py",
                status="modified",
                additions=file_additions,
                deletions=file_deletions,
                changes=file_additions + file_deletions,
                raw_url=None,
            )
        )
    return GitHubCompareResult(
        html_url=f"https://github.com/openai/codex/compare/{base}...{head}",
        base_sha="base",
        head_sha="head",
        ahead_by=total_commits,
        total_commits=total_commits,
        commits=(),
        files=tuple(files),
    )


def test_backport_branch_and_tag_use_expected_naming() -> None:
    assert build_backport_branch("rust-v0.118.0") == "puck/backport-release--v0.118.0"
    assert build_backport_tag("rust-v0.118.0") == "backport-v0.118.0"


def test_historical_releases_from_state_walks_backward_from_current_release() -> None:
    releases = (CURRENT_RELEASE, RELEASE_118, RELEASE_117, RELEASE_116)

    selected = historical_releases_from_state(
        CURRENT_RELEASE,
        releases,
        oldest_version="0.117.0",
    )

    assert selected == (RELEASE_118, RELEASE_117)


def test_assess_backport_effort_marks_large_upstream_or_local_deltas() -> None:
    compare = _compare(
        base=RELEASE_116.tag_name,
        head=CURRENT_RELEASE.tag_name,
        total_commits=18,
        files_changed=28,
        additions=900,
        deletions=500,
    )
    major, notes = assess_backport_effort(
        collect_compare_stats(compare),
        diff_stats=BackportDiffStats(
            files_changed=3,
            insertions=120,
            deletions=80,
            line_delta=200,
        ),
        thresholds=BackportEffortThresholds(
            compare_commit_count=12,
            compare_file_count=20,
            compare_line_delta=1200,
            diff_file_count=15,
            diff_line_delta=800,
        ),
    )

    assert major is True
    assert any("18 commits" in note for note in notes)
    assert any("28 files" in note for note in notes)
    assert any("1400 lines" in note for note in notes)


def test_plan_historical_backports_includes_major_notes_for_large_backports() -> None:
    current_state = TrackingState.bootstrap(
        UpstreamSnapshot(repository="openai/codex", latest_release=CURRENT_RELEASE),
        updated_at="2026-04-20T00:00:00Z",
    )
    compares = {
        (RELEASE_118.tag_name, CURRENT_RELEASE.tag_name): _compare(
            base=RELEASE_118.tag_name,
            head=CURRENT_RELEASE.tag_name,
            total_commits=4,
            files_changed=6,
            additions=120,
            deletions=80,
        ),
        (RELEASE_117.tag_name, CURRENT_RELEASE.tag_name): _compare(
            base=RELEASE_117.tag_name,
            head=CURRENT_RELEASE.tag_name,
            total_commits=16,
            files_changed=22,
            additions=900,
            deletions=500,
        ),
    }
    github_client = FakeHistoryGitHubClient(
        releases=(CURRENT_RELEASE, RELEASE_118, RELEASE_117),
        compares=compares,
    )

    entries = plan_historical_backports(
        current_state,
        github_client,
        limit=2,
    )

    assert [entry.release_version for entry in entries] == ["0.118.0", "0.117.0"]
    assert github_client.compare_calls == [
        (RELEASE_118.tag_name, CURRENT_RELEASE.tag_name),
        (RELEASE_117.tag_name, CURRENT_RELEASE.tag_name),
    ]
    assert entries[0].major is False
    assert entries[1].major is True
    assert entries[1].branch_name == "puck/backport-release--v0.117.0"
    assert entries[1].tag_name == "backport-v0.117.0"
    assert any("16 commits" in note for note in entries[1].notes)

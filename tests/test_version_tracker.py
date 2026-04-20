from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from codex_meta_agent.version_tracker import (
    GitHubApiClient,
    GitHubCompareCommit,
    GitHubCompareFile,
    GitHubCompareResult,
    GitHubRelease,
    TrackerResponse,
    TrackingState,
    UpstreamSnapshot,
    VersionTracker,
    VersionTrackerConfig,
    build_release_tag,
    build_tracking_branch,
    normalize_release_version,
    parse_tracker_response,
    render_tracker_prompt,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


LATEST_RELEASE = GitHubRelease(
    tag_name="rust-v0.120.0",
    name="0.120.0",
    html_url="https://github.com/openai/codex/releases/tag/rust-v0.120.0",
    published_at="2026-04-11T02:53:49Z",
    target_commitish="main",
    body="## Notes\n- Added something useful.",
)
SNAPSHOT = UpstreamSnapshot(
    repository="openai/codex",
    latest_release=LATEST_RELEASE,
)
COMPARE = GitHubCompareResult(
    html_url="https://github.com/openai/codex/compare/rust-v0.119.0...rust-v0.120.0",
    base_sha="old111",
    head_sha="new222",
    ahead_by=2,
    total_commits=2,
    commits=(
        GitHubCompareCommit(
            sha="commit11111111",
            html_url="https://github.com/openai/codex/commit/commit11111111",
            committed_at="2026-04-14T10:00:00Z",
            message="touch sdk/python",
        ),
        GitHubCompareCommit(
            sha="commit22222222",
            html_url="https://github.com/openai/codex/commit/commit22222222",
            committed_at="2026-04-14T11:00:00Z",
            message="touch protocol schema",
        ),
    ),
    files=(
        GitHubCompareFile(
            filename="sdk/python/src/codex_app_server/client.py",
            status="modified",
            additions=10,
            deletions=2,
            changes=12,
            raw_url="https://raw.example/client.py",
        ),
        GitHubCompareFile(
            filename="codex-rs/app-server-protocol/schema/json/codex_app_server_protocol.v2.schemas.json",
            status="modified",
            additions=5,
            deletions=1,
            changes=6,
            raw_url="https://raw.example/schema.json",
        ),
    ),
)


class FakeGitHubClient:
    def __init__(
        self,
        *,
        snapshot: UpstreamSnapshot,
        compare: GitHubCompareResult | None = None,
        releases_by_tag: dict[str, GitHubRelease] | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.compare = compare
        self.downloaded_paths: list[tuple[str, str]] = []
        self.compare_calls: list[tuple[str, str]] = []
        self.releases_by_tag = {snapshot.latest_release.tag_name: snapshot.latest_release}
        if releases_by_tag is not None:
            self.releases_by_tag.update(releases_by_tag)

    def fetch_latest_release(self) -> GitHubRelease:
        return self.snapshot.latest_release

    def fetch_release_by_tag(self, tag: str) -> GitHubRelease:
        try:
            return self.releases_by_tag[tag]
        except KeyError as exc:
            raise RuntimeError(f"unknown release tag: {tag}") from exc

    def compare_commits(self, base: str, head: str) -> GitHubCompareResult:
        assert self.compare is not None
        self.compare_calls.append((base, head))
        return self.compare

    def download_text(self, ref: str, upstream_path: str) -> str:
        self.downloaded_paths.append((ref, upstream_path))
        return f"downloaded from {ref}: {upstream_path}\n"


def write_pyproject(path: Path, version: str) -> None:
    path.write_text(
        "\n".join(
            [
                "[build-system]",
                'requires = ["uv_build>=0.10.10,<0.11.0"]',
                'build-backend = "uv_build"',
                "",
                "[project]",
                'name = "codex-python-sdk-unofficial"',
                f'version = "{version}"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_render_tracker_prompt_includes_release_sync_contract(tmp_path: Path) -> None:
    prompt = render_tracker_prompt(
        repo_root=tmp_path,
        snapshot=SNAPSHOT,
        release_version="0.120.0",
        release_branch="puck/frontier-realese--v0.120.0",
        prior_state=None,
        compare=COMPARE,
        tracking_targets=VersionTrackerConfig().tracking_targets,
        context_paths=(
            tmp_path / ".codex-meta-agent" / "upstream" / "rust-v0.120.0" / "sdk/python/README.md",
        ),
        state_path=tmp_path / ".github" / "codex-upstream-state.json",
    )

    assert "Codex Meta-Agent Release Sync" in prompt
    assert "openai/codex" in prompt
    assert "puck/frontier-realese--v0.120.0" in prompt
    assert "v0.120.0" in prompt
    assert "sdk/python/" in prompt
    assert "codex-rs/app-server-protocol/" in prompt
    assert ".github/codex-upstream-state.json" in prompt
    assert "Reply as JSON matching this schema exactly" in prompt


def test_parse_tracker_response_accepts_expected_json_shape() -> None:
    response = parse_tracker_response(
        json.dumps(
            {
                "summary": "Updated docs and state.",
                "changed_paths": ["README.md", "docs/upstream-tracking.md"],
                "verification_commands": ["uv run pytest -q"],
                "assumptions": ["GitHub Actions can push tracking branches."],
                "release_readiness_notes": "Ready for the main-branch release workflow.",
            }
        )
    )

    assert response == TrackerResponse(
        summary="Updated docs and state.",
        changed_paths=("README.md", "docs/upstream-tracking.md"),
        verification_commands=("uv run pytest -q",),
        assumptions=("GitHub Actions can push tracking branches.",),
        release_readiness_notes="Ready for the main-branch release workflow.",
    )


def test_parse_tracker_response_rejects_non_string_lists() -> None:
    with pytest.raises(ValueError):
        parse_tracker_response(
            json.dumps(
                {
                    "summary": "bad",
                    "changed_paths": ["README.md", 123],
                    "verification_commands": [],
                    "assumptions": [],
                    "release_readiness_notes": "bad",
                }
            )
        )


def test_release_metadata_uses_codex_semver() -> None:
    assert normalize_release_version("rust-v0.120.0") == "0.120.0"
    assert build_release_tag("rust-v0.120.0") == "v0.120.0"
    assert build_tracking_branch("rust-v0.120.0") == "puck/frontier-realese--v0.120.0"
    assert (
        build_tracking_branch("rust-v0.120.0", prefix="puck/flegacy-release--")
        == "puck/flegacy-release--v0.120.0"
    )


def test_version_tracker_no_drift_skips_codex_and_leaves_state_untouched(tmp_path: Path) -> None:
    repo_root = tmp_path
    state = TrackingState.bootstrap(
        SNAPSHOT,
        updated_at="2026-04-14T00:00:00Z",
    )
    state_path = repo_root / ".github" / "codex-upstream-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    original_text = json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n"
    state_path.write_text(original_text, encoding="utf-8")
    github_output_path = repo_root / "github-output.txt"
    github_client = FakeGitHubClient(snapshot=SNAPSHOT)

    def failing_codex_runner(prompt: str) -> TrackerResponse:
        raise AssertionError(f"Codex should not be invoked when no drift exists: {prompt!r}")

    tracker = VersionTracker(
        VersionTrackerConfig(
            repo_root=repo_root,
            github_output_path=github_output_path,
            run_verification=False,
        ),
        github_client=cast(GitHubApiClient, github_client),
        codex_runner=failing_codex_runner,
        command_runner=lambda command, cwd: pytest.fail(
            "Verification should not run when no drift exists."
        ),
        now_factory=lambda: datetime(2026, 4, 15, 4, 6, 20, tzinfo=UTC),
    )

    result = tracker.run()

    assert result.changed is False
    assert result.release_needed is False
    assert state_path.read_text(encoding="utf-8") == original_text
    assert "changed=false" in github_output_path.read_text(encoding="utf-8")


def test_version_tracker_drift_updates_state_version_and_branch_metadata(tmp_path: Path) -> None:
    repo_root = tmp_path
    previous_snapshot = UpstreamSnapshot(
        repository=SNAPSHOT.repository,
        latest_release=GitHubRelease(
            tag_name="rust-v0.119.0",
            name="0.119.0",
            html_url="https://github.com/openai/codex/releases/tag/rust-v0.119.0",
            published_at="2026-04-01T00:00:00Z",
            target_commitish="main",
            body="",
        ),
    )
    previous_state = TrackingState.bootstrap(
        previous_snapshot,
        updated_at="2026-04-10T00:00:00Z",
    )
    state_path = repo_root / ".github" / "codex-upstream-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(previous_state.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_pyproject(repo_root / "pyproject.toml", "0.119.0")
    github_output_path = repo_root / "github-output.txt"
    github_client = FakeGitHubClient(snapshot=SNAPSHOT, compare=COMPARE)
    prompts: list[str] = []

    def codex_runner(prompt: str) -> TrackerResponse:
        prompts.append(prompt)
        return TrackerResponse(
            summary="Updated the docs and tracking state.",
            changed_paths=("README.md", "docs/upstream-tracking.md"),
            verification_commands=("uv run pytest -q",),
            assumptions=("tracking should only follow upstream releases",),
            release_readiness_notes="Ready for the main-branch release workflow.",
        )

    tracker = VersionTracker(
        VersionTrackerConfig(
            repo_root=repo_root,
            github_output_path=github_output_path,
            run_verification=False,
        ),
        github_client=cast(GitHubApiClient, github_client),
        codex_runner=codex_runner,
        command_runner=lambda command, cwd: pytest.fail(
            "Verification should be disabled for this focused test."
        ),
        now_factory=lambda: datetime(2026, 4, 15, 4, 6, 20, tzinfo=UTC),
    )

    result = tracker.run()
    updated_payload = json.loads(state_path.read_text(encoding="utf-8"))
    response_path = repo_root / ".codex-meta-agent" / "tracker-response.json"
    output_text = github_output_path.read_text(encoding="utf-8")

    assert prompts, "Codex should be invoked when release drift exists."
    assert result.changed is True
    assert result.release_needed is True
    assert result.release_version == "0.120.0"
    assert result.release_branch == "puck/frontier-realese--v0.120.0"
    assert updated_payload["last_seen_release"]["tag_name"] == SNAPSHOT.latest_release.tag_name
    assert "last_seen_main" not in updated_payload
    assert response_path.exists()
    assert 'version = "0.120.0"' in (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    assert "release_needed=true" in output_text
    assert "release_tag=v0.120.0" in output_text
    assert "release_branch=puck/frontier-realese--v0.120.0" in output_text
    assert github_client.compare_calls == [("rust-v0.119.0", "rust-v0.120.0")]
    assert github_client.downloaded_paths == [
        ("rust-v0.120.0", "sdk/python/src/codex_app_server/client.py"),
        (
            "rust-v0.120.0",
            "codex-rs/app-server-protocol/schema/json/codex_app_server_protocol.v2.schemas.json",
        ),
    ]


def test_version_tracker_target_version_backfills_prior_release_from_clean_main(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path
    current_state = TrackingState.bootstrap(
        SNAPSHOT,
        updated_at="2026-04-15T00:00:00Z",
    )
    state_path = repo_root / ".github" / "codex-upstream-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(current_state.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_pyproject(repo_root / "pyproject.toml", "0.120.0")

    prior_release = GitHubRelease(
        tag_name="rust-v0.119.0",
        name="0.119.0",
        html_url="https://github.com/openai/codex/releases/tag/rust-v0.119.0",
        published_at="2026-04-01T00:00:00Z",
        target_commitish="main",
        body="",
    )
    github_client = FakeGitHubClient(
        snapshot=SNAPSHOT,
        compare=COMPARE,
        releases_by_tag={prior_release.tag_name: prior_release},
    )
    prompts: list[str] = []

    def codex_runner(prompt: str) -> TrackerResponse:
        prompts.append(prompt)
        return TrackerResponse(
            summary="Backfilled the prior tracked release.",
            changed_paths=("README.md",),
            verification_commands=("uv run pytest -q",),
            assumptions=("the checkout starts from a clean main branch",),
            release_readiness_notes="Ready for the requested prior-release branch.",
        )

    tracker = VersionTracker(
        VersionTrackerConfig(
            repo_root=repo_root,
            target_version="0.119.0",
            tracking_branch_prefix="puck/flegacy-release--",
            run_verification=False,
        ),
        github_client=cast(GitHubApiClient, github_client),
        codex_runner=codex_runner,
        command_runner=lambda command, cwd: pytest.fail(
            "Verification should be disabled for this focused test."
        ),
        now_factory=lambda: datetime(2026, 4, 16, 1, 2, 3, tzinfo=UTC),
    )

    result = tracker.run()
    updated_payload = json.loads(state_path.read_text(encoding="utf-8"))
    prompt_text = (repo_root / ".codex-meta-agent" / "tracker-brief.md").read_text(
        encoding="utf-8"
    )

    assert prompts, "Codex should be invoked when targeting a different release."
    assert result.release_version == "0.119.0"
    assert result.release_branch == "puck/flegacy-release--v0.119.0"
    assert updated_payload["last_seen_release"]["tag_name"] == "rust-v0.119.0"
    assert 'version = "0.119.0"' in (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    assert github_client.compare_calls == [("rust-v0.119.0", "rust-v0.120.0")]
    assert github_client.downloaded_paths == [
        ("rust-v0.119.0", "sdk/python/src/codex_app_server/client.py"),
        (
            "rust-v0.119.0",
            "codex-rs/app-server-protocol/schema/json/codex_app_server_protocol.v2.schemas.json",
        ),
    ]
    assert "targeted backfill run from clean `main`" in prompt_text
    assert "selected stable release tag: `rust-v0.119.0`" in prompt_text


def test_version_tracker_workflow_declares_daily_schedule_and_tracking_branch_push() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "version-tracker.yml").read_text(
        encoding="utf-8"
    )

    assert 'cron: "17 6 * * *"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "base_ref:" in workflow
    assert "target_version:" in workflow
    assert "tracking_branch_prefix:" in workflow
    assert "skip_verification:" in workflow
    assert "contents: write" in workflow
    assert "pull-requests: write" in workflow
    assert "path: controller" in workflow
    assert "path: target" in workflow
    assert "CODEX_HOME: ${{ github.workspace }}/.codex-runtime" in workflow
    assert "ref: main" in workflow
    assert 'base_ref="${BASE_REF:-main}"' in workflow
    assert 'git checkout --detach "origin/$base_ref"' in workflow
    assert 'git checkout --detach FETCH_HEAD' in workflow
    assert "npm install --global @openai/codex" in workflow
    assert "codex login --with-api-key" in workflow
    assert "working-directory: ${{ env.CONTROLLER_PATH }}" in workflow
    assert "working-directory: ${{ env.TARGET_PATH }}" in workflow
    assert "uv run python -m codex_meta_agent" in workflow
    assert '--repo-root "$TARGET_REPO"' in workflow
    assert '--tracking-branch-prefix "$tracking_branch_prefix"' in workflow
    assert 'cmd+=(--target-version "$TARGET_VERSION")' in workflow
    assert 'cmd+=(--skip-verification)' in workflow
    assert 'git push origin "HEAD:${{ steps.tracker.outputs.release_branch }}"' in workflow
    assert 'gh pr create \\' in workflow
    assert "Frontier release v${{ steps.tracker.outputs.release_version }}" in workflow
    assert "Legacy release v${{ steps.tracker.outputs.release_version }}" in workflow
    assert 'git config user.name "$author_name"' in workflow
    assert "HEAD:main" not in workflow


def test_legacy_release_workflow_dispatches_targeted_backfill() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "legacy-release.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "base_ref:" in workflow
    assert "target_version:" in workflow
    assert "skip_verification:" in workflow
    assert "path: controller" in workflow
    assert "path: target" in workflow
    assert "CODEX_HOME: ${{ github.workspace }}/.codex-runtime" in workflow
    assert 'base_ref="${BASE_REF:-main}"' in workflow
    assert 'git checkout --detach FETCH_HEAD' in workflow
    assert "codex login --with-api-key" in workflow
    assert "working-directory: ${{ env.CONTROLLER_PATH }}" in workflow
    assert "working-directory: ${{ env.TARGET_PATH }}" in workflow
    assert '--repo-root "$TARGET_REPO"' in workflow
    assert "--target-version \"$TARGET_VERSION\"" in workflow
    assert '--tracking-branch-prefix "puck/flegacy-release--"' in workflow
    assert 'git push origin "HEAD:${{ steps.tracker.outputs.release_branch }}"' in workflow
    assert 'gh pr create \\' in workflow
    assert "Legacy release v${{ steps.tracker.outputs.release_version }}" in workflow


def test_publish_workflow_releases_and_publishes_from_main() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "publish-pypi.yml").read_text(
        encoding="utf-8"
    )

    assert "branches:" in workflow
    assert '- "main"' in workflow
    assert "codex_meta_agent.release_metadata" in workflow
    assert "codex_meta_agent.version_tracker" not in workflow
    assert "prepare_release:" in workflow
    assert "gh release create" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "actions/download-artifact@v4" in workflow
    assert "resolve_publish_mode:" in workflow
    assert "Resolve PyPI publish mode" in workflow
    assert "publish_via_token:" in workflow
    assert "publish_mode=api-token" in workflow
    assert "Publish to PyPI with API token" in workflow
    assert "PYPI_API_TOKEN" in workflow
    assert "publish_via_trusted_publishing:" in workflow
    assert "publish_mode=trusted" in workflow
    assert "Publish to PyPI with trusted publishing" in workflow
    assert "https://pypi.org/project/codex-python-sdk-unofficial/json" in workflow


def test_committed_tracking_state_matches_expected_shape() -> None:
    payload = json.loads(
        (REPO_ROOT / ".github" / "codex-upstream-state.json").read_text(encoding="utf-8")
    )

    assert payload["upstream_repository"] == "openai/codex"
    assert payload["schema_version"] == "2.0"
    assert payload["last_seen_release"]["tag_name"].startswith("rust-v")
    assert "tracked_branch" not in payload
    assert "last_seen_main" not in payload

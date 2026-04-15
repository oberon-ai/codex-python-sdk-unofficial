"""Scheduled upstream tracking automation built on top of ``codex_agent_sdk``."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codex_agent_sdk import AppServerConfig, CodexOptions, SyncCodexSDKClient

DEFAULT_UPSTREAM_REPOSITORY = "openai/codex"
DEFAULT_UPSTREAM_BRANCH = "main"
DEFAULT_STATE_PATH = Path(".github/codex-upstream-state.json")
DEFAULT_CONTEXT_DIR = Path(".codex-meta-agent")
DEFAULT_RELEASE_TAG_PREFIX = "upstream-"
DEFAULT_VERIFICATION_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("uv", "run", "pytest", "-q"),
    ("uv", "run", "mypy"),
    ("uv", "run", "ruff", "check", "."),
    ("uv", "run", "ruff", "format", "--check", "."),
    ("uv", "build"),
)
TRACKER_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "changed_paths": {
            "type": "array",
            "items": {"type": "string"},
        },
        "verification_commands": {
            "type": "array",
            "items": {"type": "string"},
        },
        "assumptions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "release_readiness_notes": {"type": "string"},
    },
    "required": [
        "summary",
        "changed_paths",
        "verification_commands",
        "assumptions",
        "release_readiness_notes",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class GitHubRepositoryHead:
    sha: str
    html_url: str
    committed_at: str
    message: str


@dataclass(frozen=True, slots=True)
class GitHubRelease:
    tag_name: str
    name: str
    html_url: str
    published_at: str
    target_commitish: str
    body: str


@dataclass(frozen=True, slots=True)
class GitHubCompareCommit:
    sha: str
    html_url: str
    committed_at: str
    message: str


@dataclass(frozen=True, slots=True)
class GitHubCompareFile:
    filename: str
    status: str
    additions: int
    deletions: int
    changes: int
    raw_url: str | None = None


@dataclass(frozen=True, slots=True)
class GitHubCompareResult:
    html_url: str
    base_sha: str
    head_sha: str
    ahead_by: int
    total_commits: int
    commits: tuple[GitHubCompareCommit, ...]
    files: tuple[GitHubCompareFile, ...]


@dataclass(frozen=True, slots=True)
class UpstreamTrackingTarget:
    upstream_prefix: str
    local_paths: tuple[str, ...]
    rationale: str


DEFAULT_TRACKING_TARGETS: tuple[UpstreamTrackingTarget, ...] = (
    UpstreamTrackingTarget(
        upstream_prefix="sdk/python/",
        local_paths=(
            "README.md",
            "docs/",
            "examples/",
            "src/codex_agent_sdk/",
            "tests/",
        ),
        rationale=(
            "Upstream Python SDK surface changes usually map onto the local public API, docs, "
            "examples, and compatibility tests."
        ),
    ),
    UpstreamTrackingTarget(
        upstream_prefix="codex-rs/app-server-protocol/",
        local_paths=(
            "scripts/generate_protocol_models.py",
            "scripts/vendor_protocol_schema.py",
            "src/codex_agent_sdk/generated/",
            "src/codex_agent_sdk/protocol/",
            "tests/fixtures/schema_snapshots/",
            "tests/test_generated_protocol_models.py",
        ),
        rationale=(
            "Protocol changes usually require vendored schema updates, regenerated models, "
            "adapter fixes, and fixture refreshes."
        ),
    ),
    UpstreamTrackingTarget(
        upstream_prefix="codex-rs/app-server/",
        local_paths=(
            "README.md",
            "docs/api.md",
            "src/codex_agent_sdk/client.py",
            "src/codex_agent_sdk/query.py",
            "tests/test_app_server_client.py",
            "tests/test_sdk_client.py",
        ),
        rationale=(
            "App-server behavior changes usually affect the transport/client layers and their "
            "usage docs."
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class UpstreamSnapshot:
    repository: str
    branch: str
    main: GitHubRepositoryHead
    latest_release: GitHubRelease


@dataclass(frozen=True, slots=True)
class TrackingState:
    schema_version: str
    upstream_repository: str
    tracked_branch: str
    last_seen_main: GitHubRepositoryHead
    last_seen_release: GitHubRelease
    updated_at: str

    @classmethod
    def bootstrap(cls, snapshot: UpstreamSnapshot, *, updated_at: str) -> TrackingState:
        return cls(
            schema_version="1.0",
            upstream_repository=snapshot.repository,
            tracked_branch=snapshot.branch,
            last_seen_main=snapshot.main,
            last_seen_release=GitHubRelease(
                tag_name=snapshot.latest_release.tag_name,
                name=snapshot.latest_release.name,
                html_url=snapshot.latest_release.html_url,
                published_at=snapshot.latest_release.published_at,
                target_commitish=snapshot.latest_release.target_commitish,
                body="",
            ),
            updated_at=updated_at,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TrackingState:
        return cls(
            schema_version=_require_string(payload, "schema_version"),
            upstream_repository=_require_string(payload, "upstream_repository"),
            tracked_branch=_require_string(payload, "tracked_branch"),
            last_seen_main=GitHubRepositoryHead(**_require_dict(payload, "last_seen_main")),
            last_seen_release=GitHubRelease(**_require_dict(payload, "last_seen_release")),
            updated_at=_require_string(payload, "updated_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TrackerResponse:
    summary: str
    changed_paths: tuple[str, ...]
    verification_commands: tuple[str, ...]
    assumptions: tuple[str, ...]
    release_readiness_notes: str


@dataclass(frozen=True, slots=True)
class VersionTrackerResult:
    branch_drift: bool
    release_drift: bool
    changed: bool
    release_needed: bool
    commit_message: str
    snapshot: UpstreamSnapshot
    state: TrackingState
    prompt_path: Path
    response_path: Path | None
    release_notes_path: Path | None
    context_paths: tuple[Path, ...] = ()


@dataclass(slots=True)
class VersionTrackerConfig:
    repo_root: Path = Path.cwd()
    state_path: Path = field(default_factory=lambda: DEFAULT_STATE_PATH)
    context_dir: Path = field(default_factory=lambda: DEFAULT_CONTEXT_DIR)
    upstream_repository: str = DEFAULT_UPSTREAM_REPOSITORY
    upstream_branch: str = DEFAULT_UPSTREAM_BRANCH
    release_tag_prefix: str = DEFAULT_RELEASE_TAG_PREFIX
    codex_bin: str = "codex"
    model: str = "gpt-5.4"
    github_token: str | None = None
    github_output_path: Path | None = None
    apply_changes: bool = True
    run_verification: bool = True
    verification_commands: tuple[tuple[str, ...], ...] = DEFAULT_VERIFICATION_COMMANDS
    tracking_targets: tuple[UpstreamTrackingTarget, ...] = DEFAULT_TRACKING_TARGETS
    max_context_files: int = 12

    def resolved_state_path(self) -> Path:
        return self.repo_root / self.state_path

    def resolved_context_dir(self) -> Path:
        return self.repo_root / self.context_dir


class GitHubApiClient:
    def __init__(self, repository: str, *, token: str | None = None) -> None:
        self.repository = repository
        self._token = token

    def fetch_main_head(self, branch: str) -> GitHubRepositoryHead:
        payload = self._fetch_json(f"/repos/{self.repository}/commits/{branch}")
        commit = payload.get("commit")
        if not isinstance(commit, dict):
            raise RuntimeError("GitHub commit payload did not include a nested commit object.")
        committer = commit.get("committer")
        if not isinstance(committer, dict):
            raise RuntimeError("GitHub commit payload did not include committer metadata.")
        message = commit.get("message")
        if not isinstance(message, str):
            raise RuntimeError("GitHub commit payload did not include a commit message.")
        return GitHubRepositoryHead(
            sha=_require_string(payload, "sha"),
            html_url=_require_string(payload, "html_url"),
            committed_at=_require_string(committer, "date"),
            message=message.splitlines()[0],
        )

    def fetch_latest_release(self) -> GitHubRelease:
        payload = self._fetch_json(f"/repos/{self.repository}/releases/latest")
        tag_name = _require_string(payload, "tag_name")
        return GitHubRelease(
            tag_name=tag_name,
            name=_optional_string(payload, "name") or tag_name,
            html_url=_require_string(payload, "html_url"),
            published_at=_require_string(payload, "published_at"),
            target_commitish=_require_string(payload, "target_commitish"),
            body=_optional_string(payload, "body") or "",
        )

    def compare_commits(self, base: str, head: str) -> GitHubCompareResult:
        encoded_base = urllib.parse.quote(base, safe="")
        encoded_head = urllib.parse.quote(head, safe="")
        payload = self._fetch_json(
            f"/repos/{self.repository}/compare/{encoded_base}...{encoded_head}"
        )
        files_payload = payload.get("files")
        commits_payload = payload.get("commits")
        if not isinstance(files_payload, list) or not isinstance(commits_payload, list):
            raise RuntimeError("GitHub compare payload did not include files and commits lists.")
        files = tuple(
            GitHubCompareFile(
                filename=_require_string(file_payload, "filename"),
                status=_require_string(file_payload, "status"),
                additions=_require_int(file_payload, "additions"),
                deletions=_require_int(file_payload, "deletions"),
                changes=_require_int(file_payload, "changes"),
                raw_url=_optional_string(file_payload, "raw_url"),
            )
            for file_payload in files_payload
            if isinstance(file_payload, dict)
        )
        commits = []
        for commit_payload in commits_payload:
            if not isinstance(commit_payload, dict):
                continue
            commit_details = commit_payload.get("commit")
            if not isinstance(commit_details, dict):
                continue
            committer = commit_details.get("committer")
            if not isinstance(committer, dict):
                continue
            message = commit_details.get("message")
            if not isinstance(message, str):
                continue
            commits.append(
                GitHubCompareCommit(
                    sha=_require_string(commit_payload, "sha"),
                    html_url=_require_string(commit_payload, "html_url"),
                    committed_at=_require_string(committer, "date"),
                    message=message.splitlines()[0],
                )
            )
        return GitHubCompareResult(
            html_url=_require_string(payload, "html_url"),
            base_sha=_require_string(payload, "base_commit", nested_key="sha"),
            head_sha=head,
            ahead_by=_require_int(payload, "ahead_by"),
            total_commits=_require_int(payload, "total_commits"),
            commits=tuple(commits),
            files=files,
        )

    def download_text(self, ref: str, upstream_path: str) -> str:
        encoded_path = "/".join(
            urllib.parse.quote(part, safe="") for part in upstream_path.split("/")
        )
        url = (
            f"https://raw.githubusercontent.com/{self.repository}/"
            f"{urllib.parse.quote(ref, safe='')}/{encoded_path}"
        )
        return self._fetch_text(url)

    def _fetch_json(self, path: str) -> dict[str, Any]:
        url = f"https://api.github.com{path}"
        text = self._fetch_text(url)
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise RuntimeError(f"Expected a JSON object from {url}, received {type(payload)!r}.")
        return payload

    def _fetch_text(self, url: str) -> str:
        request = urllib.request.Request(
            url,
            headers=self._build_headers(),
        )
        try:
            with urllib.request.urlopen(request) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                payload = response.read()
                if not isinstance(payload, bytes):
                    raise RuntimeError(f"GitHub request returned non-bytes payload for {url}.")
                return payload.decode(charset)
        except urllib.error.HTTPError as exc:  # pragma: no cover - exercised by callers via tests
            raise RuntimeError(f"GitHub request failed for {url}: {exc.code} {exc.reason}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - exercised by callers via tests
            raise RuntimeError(f"GitHub request failed for {url}: {exc.reason}") from exc

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "codex-meta-agent-version-tracker",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers


class VersionTracker:
    """Orchestrate upstream detection, SDK-driven updates, and release planning."""

    def __init__(
        self,
        config: VersionTrackerConfig,
        *,
        github_client: GitHubApiClient | None = None,
        codex_runner: Callable[[str], TrackerResponse] | None = None,
        command_runner: Callable[[Sequence[str], Path], None] | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._github = github_client or GitHubApiClient(
            config.upstream_repository,
            token=config.github_token,
        )
        self._codex_runner = codex_runner
        self._command_runner = command_runner or _run_subprocess_command
        self._now_factory = now_factory or (lambda: datetime.now(UTC))

    def run(self) -> VersionTrackerResult:
        snapshot = UpstreamSnapshot(
            repository=self.config.upstream_repository,
            branch=self.config.upstream_branch,
            main=self._github.fetch_main_head(self.config.upstream_branch),
            latest_release=self._github.fetch_latest_release(),
        )
        state_path = self.config.resolved_state_path()
        state = _load_tracking_state(state_path)
        branch_drift = state is None or state.last_seen_main.sha != snapshot.main.sha
        release_drift = (
            state is None or state.last_seen_release.tag_name != snapshot.latest_release.tag_name
        )

        context_dir = self.config.resolved_context_dir()
        context_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = context_dir / "tracker-brief.md"
        response_path: Path | None = None
        release_notes_path: Path | None = None
        context_paths: tuple[Path, ...] = ()

        compare = None
        if state is not None and branch_drift:
            compare = self._github.compare_commits(state.last_seen_main.sha, snapshot.main.sha)

        if branch_drift or release_drift:
            context_paths = self._download_upstream_context(compare=compare, snapshot=snapshot)
            prompt = render_tracker_prompt(
                repo_root=self.config.repo_root,
                snapshot=snapshot,
                prior_state=state,
                compare=compare,
                tracking_targets=self.config.tracking_targets,
                context_paths=context_paths,
                state_path=state_path,
            )
            prompt_path.write_text(prompt, encoding="utf-8")
            response = self._invoke_codex(prompt)
            response_path = context_dir / "tracker-response.json"
            response_path.write_text(
                json.dumps(asdict(response), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if self.config.run_verification:
                self._run_verification()
        else:
            prompt_path.write_text(
                "No upstream drift detected. The tracker did not need to invoke Codex.\n",
                encoding="utf-8",
            )

        updated_state = TrackingState.bootstrap(snapshot, updated_at=_utc_now(self._now_factory))

        changed = branch_drift or release_drift
        commit_message = _commit_message(
            snapshot,
            branch_drift=branch_drift,
            release_drift=release_drift,
        )
        if self.config.apply_changes and (branch_drift or release_drift):
            _write_tracking_state(state_path, updated_state)
        else:
            changed = False if not (branch_drift or release_drift) else changed
        if release_drift and self.config.apply_changes:
            release_notes_path = context_dir / "release-notes.md"
            release_notes_path.write_text(
                _render_release_notes(snapshot=snapshot, compare=compare),
                encoding="utf-8",
            )

        self._write_github_outputs(
            changed=changed,
            release_needed=release_drift,
            commit_message=commit_message,
            snapshot=snapshot,
            prompt_path=prompt_path,
            response_path=response_path,
            release_notes_path=release_notes_path,
        )

        return VersionTrackerResult(
            branch_drift=branch_drift,
            release_drift=release_drift,
            changed=changed,
            release_needed=release_drift,
            commit_message=commit_message,
            snapshot=snapshot,
            state=updated_state,
            prompt_path=prompt_path,
            response_path=response_path,
            release_notes_path=release_notes_path,
            context_paths=context_paths,
        )

    def _download_upstream_context(
        self,
        *,
        compare: GitHubCompareResult | None,
        snapshot: UpstreamSnapshot,
    ) -> tuple[Path, ...]:
        if compare is None:
            return ()
        tracked_files = [
            file
            for file in compare.files
            if any(
                file.filename.startswith(target.upstream_prefix)
                for target in self.config.tracking_targets
            )
        ][: self.config.max_context_files]
        if not tracked_files:
            return ()
        context_root = self.config.resolved_context_dir() / "upstream" / snapshot.main.sha
        paths: list[Path] = []
        for file in tracked_files:
            try:
                text = self._github.download_text(snapshot.branch, file.filename)
            except RuntimeError:
                continue
            destination = context_root / file.filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8")
            paths.append(destination)
        return tuple(paths)

    def _invoke_codex(self, prompt: str) -> TrackerResponse:
        if not self.config.apply_changes:
            return TrackerResponse(
                summary="Dry run only; Codex was not invoked.",
                changed_paths=(),
                verification_commands=(),
                assumptions=("dry-run mode",),
                release_readiness_notes=(
                    "No release action was prepared because --dry-run was used."
                ),
            )
        if self._codex_runner is not None:
            return self._codex_runner(prompt)
        with SyncCodexSDKClient(
            options=CodexOptions(
                model=self.config.model,
                cwd=str(self.config.repo_root),
                approval_policy="never",
                sandbox_mode="workspace-write",
                developer_instructions=(
                    "Work directly in the checked out repository. Keep changes minimal, "
                    "production-ready, and reviewable. Update docs and tests with any "
                    "behavioral or workflow changes. Prefer existing scripts and "
                    "verification commands already present in the repo."
                ),
            ),
            app_server=AppServerConfig(codex_bin=self.config.codex_bin),
        ) as client:
            turn = client.query(prompt, output_schema=TRACKER_OUTPUT_SCHEMA)
            result = turn.wait()
        if result.status != "completed":
            raise RuntimeError(f"Codex turn did not complete successfully: {result.status}")
        if result.assistant_text is None:
            raise RuntimeError("Codex did not return structured tracker output.")
        return parse_tracker_response(result.assistant_text)

    def _run_verification(self) -> None:
        for command in self.config.verification_commands:
            self._command_runner(command, self.config.repo_root)

    def _write_github_outputs(
        self,
        *,
        changed: bool,
        release_needed: bool,
        commit_message: str,
        snapshot: UpstreamSnapshot,
        prompt_path: Path,
        response_path: Path | None,
        release_notes_path: Path | None,
    ) -> None:
        output_path = self.config.github_output_path
        if output_path is None:
            return
        values = {
            "changed": _bool_text(changed),
            "release_needed": _bool_text(release_needed),
            "commit_message": commit_message,
            "upstream_main_sha": snapshot.main.sha,
            "upstream_release_tag": snapshot.latest_release.tag_name,
            "prompt_path": str(prompt_path),
            "release_tag": build_release_tag(
                snapshot.latest_release.tag_name,
                prefix=self.config.release_tag_prefix,
            ),
            "release_name": _release_name(snapshot.latest_release),
            "response_path": str(response_path) if response_path is not None else "",
            "release_notes_path": str(release_notes_path) if release_notes_path is not None else "",
        }
        _write_github_outputs(output_path, values)


def render_tracker_prompt(
    *,
    repo_root: Path,
    snapshot: UpstreamSnapshot,
    prior_state: TrackingState | None,
    compare: GitHubCompareResult | None,
    tracking_targets: Sequence[UpstreamTrackingTarget],
    context_paths: Sequence[Path],
    state_path: Path,
) -> str:
    compare_block = _render_compare_block(compare)
    prior_block = _render_prior_state(prior_state)
    context_block = _render_context_paths(context_paths)
    target_block = "\n".join(
        f"- `{target.upstream_prefix}` -> {', '.join(f'`{path}`' for path in target.local_paths)}\n"
        f"  reason: {target.rationale}"
        for target in tracking_targets
    )
    return textwrap.dedent(
        f"""\
        # Codex Meta-Agent Branch Sync

        Update the repository at `{repo_root}` so it stays aligned with `{snapshot.repository}`.

        ## Upstream Snapshot

        - tracked branch: `{snapshot.branch}`
        - latest upstream main commit: `{snapshot.main.sha}` ({snapshot.main.committed_at})
        - main commit summary: {snapshot.main.message}
        - main commit URL: {snapshot.main.html_url}
        - latest stable release tag: `{snapshot.latest_release.tag_name}`
        - stable release name: `{snapshot.latest_release.name}`
        - stable release published at: {snapshot.latest_release.published_at}
        - stable release URL: {snapshot.latest_release.html_url}

        ## Previously Tracked State

        {prior_block}

        ## Relevant Upstream Drift

        {compare_block}

        ## Mapping Guidance

        {target_block}

        ## Downloaded Upstream Context

        {context_block}

        ## Required Repository Work

        - Update code, docs, tests, and release plumbing as needed so this repo
          tracks upstream drift cleanly.
        - Keep the automation implementation under `src/codex_meta_agent/`.
        - Use existing repository scripts when they fit instead of inventing
          parallel workflows.
        - Always update `{state_path}` to the latest upstream commit and
          release metadata by the end of the run.
        - If upstream stable release drift exists, leave the repo ready for a
          GitHub release tagged with the prefixed upstream tag.
        - Prefer deterministic edits and verification over speculative changes.
        - Do not edit files under `{DEFAULT_CONTEXT_DIR}` except for generated
          run artifacts.

        ## Response Format

        Reply as JSON matching this schema exactly:

        ```json
        {json.dumps(TRACKER_OUTPUT_SCHEMA, indent=2, sort_keys=True)}
        ```
        """
    )


def parse_tracker_response(text: str) -> TrackerResponse:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("Tracker response must be a JSON object.")
    summary = _require_string(payload, "summary")
    changed_paths = _require_string_list(payload, "changed_paths")
    verification_commands = _require_string_list(payload, "verification_commands")
    assumptions = _require_string_list(payload, "assumptions")
    release_readiness_notes = _require_string(payload, "release_readiness_notes")
    return TrackerResponse(
        summary=summary,
        changed_paths=tuple(changed_paths),
        verification_commands=tuple(verification_commands),
        assumptions=tuple(assumptions),
        release_readiness_notes=release_readiness_notes,
    )


def build_release_tag(upstream_tag: str, *, prefix: str = DEFAULT_RELEASE_TAG_PREFIX) -> str:
    return f"{prefix}{upstream_tag}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check openai/codex for upstream drift, use Codex via codex_agent_sdk to "
            "update this repository, and prepare release metadata."
        )
    )
    parser.add_argument("--repo-root", default=".", help="Repository root to update.")
    parser.add_argument(
        "--state-path",
        default=str(DEFAULT_STATE_PATH),
        help="Repository-relative path to the committed upstream tracking state file.",
    )
    parser.add_argument(
        "--context-dir",
        default=str(DEFAULT_CONTEXT_DIR),
        help="Repository-relative directory for temporary tracker artifacts.",
    )
    parser.add_argument(
        "--github-output",
        default=os.environ.get("GITHUB_OUTPUT"),
        help="Optional GitHub Actions output file path.",
    )
    parser.add_argument(
        "--upstream-repository",
        default=DEFAULT_UPSTREAM_REPOSITORY,
        help="GitHub repository slug to track.",
    )
    parser.add_argument(
        "--upstream-branch",
        default=DEFAULT_UPSTREAM_BRANCH,
        help="Upstream branch to compare against.",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.4",
        help="Codex model name to use for the maintenance run.",
    )
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Codex CLI binary name. Reserved for future process overrides.",
    )
    parser.add_argument(
        "--release-tag-prefix",
        default=DEFAULT_RELEASE_TAG_PREFIX,
        help="Prefix to add before upstream stable release tags in this repository.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect drift and write artifacts without invoking Codex or running verification.",
    )
    parser.add_argument(
        "--skip-verification",
        action="store_true",
        help="Skip the repository verification commands after Codex finishes.",
    )
    args = parser.parse_args(argv)

    github_token = os.environ.get("GITHUB_TOKEN")
    config = VersionTrackerConfig(
        repo_root=Path(args.repo_root).resolve(),
        state_path=Path(args.state_path),
        context_dir=Path(args.context_dir),
        upstream_repository=args.upstream_repository,
        upstream_branch=args.upstream_branch,
        release_tag_prefix=args.release_tag_prefix,
        codex_bin=args.codex_bin,
        model=args.model,
        github_token=github_token,
        github_output_path=Path(args.github_output) if args.github_output else None,
        apply_changes=not args.dry_run,
        run_verification=not args.skip_verification and not args.dry_run,
    )
    tracker = VersionTracker(config)
    tracker.run()
    return 0


def _render_compare_block(compare: GitHubCompareResult | None) -> str:
    if compare is None:
        return (
            "- no prior tracked commit is available, so this run must bootstrap "
            "from the latest upstream snapshot."
        )
    file_lines = [
        f"  - `{file.filename}` ({file.status}, +{file.additions}/-{file.deletions})"
        for file in compare.files[:20]
    ]
    commit_lines = [
        f"  - `{commit.sha[:12]}` {commit.committed_at}: {commit.message}"
        for commit in compare.commits[:10]
    ]
    rendered_files = "\n".join(file_lines) if file_lines else "  - no file list supplied by GitHub"
    rendered_commits = (
        "\n".join(commit_lines) if commit_lines else "  - no commit list supplied by GitHub"
    )
    return textwrap.dedent(
        f"""\
        - compare URL: {compare.html_url}
        - upstream commits ahead: {compare.ahead_by}
        - total commits in compare window: {compare.total_commits}
        - commits:
        {rendered_commits}
        - changed files:
        {rendered_files}
        """
    ).strip()


def _render_prior_state(prior_state: TrackingState | None) -> str:
    if prior_state is None:
        return "- no committed state file exists yet; this is a bootstrap run."
    return textwrap.dedent(
        f"""\
        - previous main commit: `{prior_state.last_seen_main.sha}`
          ({prior_state.last_seen_main.committed_at})
        - previous main summary: {prior_state.last_seen_main.message}
        - previous stable release: `{prior_state.last_seen_release.tag_name}`
          ({prior_state.last_seen_release.published_at})
        - state last updated at: {prior_state.updated_at}
        """
    ).strip()


def _render_context_paths(paths: Sequence[Path]) -> str:
    if not paths:
        return "- no upstream context files were materialized for this run."
    return "\n".join(f"- `{path}`" for path in paths)


def _render_release_notes(
    *,
    snapshot: UpstreamSnapshot,
    compare: GitHubCompareResult | None,
) -> str:
    compare_summary = (
        f"- upstream compare URL: {compare.html_url}\n"
        f"- upstream commits in compare window: {compare.total_commits}\n"
        if compare is not None
        else "- this release was prepared during a bootstrap run with no previous compare window.\n"
    )
    upstream_body = snapshot.latest_release.body.strip() or "_Upstream release body was empty._"
    return textwrap.dedent(
        f"""\
        # Track {snapshot.repository} {snapshot.latest_release.tag_name}

        This repository release tracks the upstream stable release
        `{snapshot.latest_release.tag_name}` (`{snapshot.latest_release.name}`).

        - upstream release URL: {snapshot.latest_release.html_url}
        - upstream release published at: {snapshot.latest_release.published_at}
        - upstream main commit evaluated by the tracker: `{snapshot.main.sha}`
        {compare_summary}

        ## Upstream Release Notes

        {upstream_body}
        """
    )


def _release_name(release: GitHubRelease) -> str:
    return f"Track openai/codex {release.tag_name}"


def _commit_message(
    snapshot: UpstreamSnapshot,
    *,
    branch_drift: bool,
    release_drift: bool,
) -> str:
    if branch_drift and release_drift:
        return (
            f"chore: track {snapshot.repository} {snapshot.branch} @ {snapshot.main.sha[:12]} "
            f"and {snapshot.latest_release.tag_name}"
        )
    if release_drift:
        return f"chore: track {snapshot.repository} release {snapshot.latest_release.tag_name}"
    return f"chore: track {snapshot.repository} {snapshot.branch} @ {snapshot.main.sha[:12]}"


def _utc_now(now_factory: Callable[[], datetime]) -> str:
    return now_factory().astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_tracking_state(path: Path) -> TrackingState | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Tracking state file must contain a JSON object: {path}")
    return TrackingState.from_dict(payload)


def _write_tracking_state(path: Path, state: TrackingState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_subprocess_command(command: Sequence[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _write_github_outputs(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            if "\n" in value:
                handle.write(f"{key}<<__CODEX_EOF__\n{value}\n__CODEX_EOF__\n")
            else:
                handle.write(f"{key}={value}\n")


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _require_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Expected {key!r} to be a JSON object.")
    return value


def _require_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise ValueError(f"Expected {key!r} to be an integer.")
    return value


def _require_string(
    payload: dict[str, Any],
    key: str,
    *,
    nested_key: str | None = None,
    default: str | None = None,
) -> str:
    value: Any = payload.get(key, default)
    if nested_key is not None:
        if not isinstance(value, dict):
            raise ValueError(f"Expected {key!r} to be a JSON object.")
        value = value.get(nested_key)
    if not isinstance(value, str):
        raise ValueError(f"Expected {key!r} to be a string.")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Expected {key!r} to be a string when present.")
    return value


def _require_string_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Expected {key!r} to be a list of strings.")
    return list(value)

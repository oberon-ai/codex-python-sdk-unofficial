# Upstream Tracking Automation

This repository includes an automated tracker for stable `openai/codex`
releases.

The release flow is:

1. the scheduled tracker checks whether `openai/codex` published a newer stable
   GitHub release than the one recorded in
   `.github/codex-upstream-state.json`
2. if a new upstream release exists, the tracker prepares a branch named
   `puck/frontier-realese--v<version>` from a fresh `main` checkout
3. the tracker opens a pull request from that branch back to `main`
4. after that branch is merged to `main`, GitHub Actions creates the
   repository GitHub release `v<version>` and publishes the matching PyPI
   version

For manual backports, `workflow_dispatch` can pass a specific Codex release
version through `.github/workflows/backport-release.yml`. The existing
`.github/workflows/version-tracker.yml` manual trigger also accepts
`tracking_branch_prefix` and `skip_verification`, which makes it suitable for
testing the same backport flow before the dedicated workflow lands on `main`.
That targeted mode uses the branch naming convention
`puck/backport-release--v<version>`, is intended for a fresh, clean `main`
checkout, keeps the Codex prompt focused on the release delta between the
currently tracked version and the requested older release, and treats the
resulting branch as a dead-end branch marked with `backport-v<version>`.

The local repository version follows the same semantic version number as the
tracked Codex release. For example, upstream `rust-v0.120.0` maps to:

- frontier branch: `puck/frontier-realese--v0.120.0`
- backport branch: `puck/backport-release--v0.120.0`
- backport dead-end tag: `backport-v0.120.0`
- repository GitHub release tag: `v0.120.0`
- PyPI package version: `0.120.0`

## Key Pieces

- `.github/workflows/version-tracker.yml`
  runs the frontier-release tracker daily and on manual dispatch, using a
  controller checkout for the automation code plus a separate fresh target
  checkout for the repository Codex edits, then creating a pull request when a
  new upstream stable release exists
- `.github/workflows/backport-release.yml`
  runs only on manual dispatch, keeps the same controller-versus-target
  isolation, and prepares a dead-end backport-release branch plus a matching
  `backport-v<version>` tag for an explicitly requested version
- `.github/workflows/publish-pypi.yml`
  runs on pushes to `main`, creates the GitHub release if needed, and publishes
  the matching PyPI release if that version is not already on PyPI
- `.github/codex-upstream-state.json`
  records the latest upstream stable release tag that this repository has
  already evaluated
- `src/codex_meta_agent/version_tracker.py`
  fetches live GitHub release metadata, prepares focused upstream context
  files, uses `SyncCodexSDKClient` to let Codex update the repository, and
  emits structured branch and release metadata for GitHub Actions
- `.codex-meta-agent/`
  is a gitignored working directory for prompt briefs, upstream context files,
  structured response JSON, and workflow-generated release notes

## Workflow Contract

The tracker workflow does the following:

1. checks out the automation controller and a separate clean target checkout,
   then installs Python, `uv`, Node.js, and the Codex CLI
2. bootstraps a clean headless Codex auth home by piping the
   `OPENAI_API_KEY` secret through `codex login --with-api-key`
3. reads the committed `.github/codex-upstream-state.json`
4. fetches `openai/codex` `releases/latest` metadata from GitHub
5. compares the latest upstream release tag against the previously tracked
   release tag
6. downloads relevant upstream files from the new release tag under tracked
   prefixes such as `sdk/python/` and `codex-rs/app-server-protocol/` into
   `.codex-meta-agent/`
7. runs `uv run python -m codex_meta_agent --repo-root <target checkout>`, which uses
   `SyncCodexSDKClient` plus an enforced JSON output schema to let Codex update
   the target checkout without depending on the repository it is rewriting
8. runs the repository verification commands
9. commits the resulting changes on `puck/frontier-realese--v<version>`
10. creates or reuses a pull request back to `main`

The manual backport-release workflow does the same preparation flow, but
requires an explicit target version, uses
`puck/backport-release--v<version>` for the branch name, and tags the
resulting branch head as `backport-v<version>` instead of opening a pull
request back to `main`.

The deployment workflow on `main` then:

1. reads `pyproject.toml` plus `.github/codex-upstream-state.json`
2. verifies that the package version matches the normalized upstream release
   version
3. creates the repository GitHub release `v<version>` if it does not already
   exist
4. publishes the package to PyPI if that version has not already been uploaded

## Git Author Configuration

The tracking workflow commits as `puck-by-oberon`.

Configure that by setting the repository variable
`TRACKER_GIT_AUTHOR_EMAIL` to the GitHub email you want attached to those
commits, ideally the account's noreply email.

The workflow uses:

- `git config user.name "puck-by-oberon"`
- `git config user.email "$TRACKER_GIT_AUTHOR_EMAIL"`

You do not need a personal access token just to author commits with that name
and email. The default `GITHUB_TOKEN` is enough to push the tracking branch in
most repositories.

You would only need a token from the `puck-by-oberon` account if you want the
GitHub API actions themselves to authenticate as that user instead of
`github-actions[bot]`, or if repository policy blocks the default token.

## Why The Tracker Uses The SDK

The repository is an SDK project, so the maintenance automation should show
that the SDK can drive real work.

The tracker therefore uses:

- `SyncCodexSDKClient`
  so the automation can run from a normal synchronous GitHub Actions step
- `CodexOptions`
  to set `cwd`, `approval_policy="never"`, `sandbox_mode="workspace-write"`,
  and the developer instructions for the maintenance run
- a JSON `output_schema`
  so the workflow receives structured summary data instead of trying to parse
  ad hoc prose

## Local Commands

Dry-run the tracker without invoking Codex or mutating the committed state:

```bash
uv run python -m codex_meta_agent --dry-run
```

Run the tracker locally but skip the project-wide verification loop:

```bash
uv run python -m codex_meta_agent --skip-verification
```

For local end-to-end testing, keep the controller checkout separate from the
repository Codex edits by pointing `--repo-root` at a clean clone or worktree:

```bash
uv run python -m codex_meta_agent --repo-root /tmp/codex-target --skip-verification
```

Prepare a specific prior Codex release from a clean `main` checkout:

```bash
uv run python -m codex_meta_agent \
  --repo-root /tmp/codex-target \
  --target-version 0.119.0 \
  --tracking-branch-prefix puck/backport-release-- \
  --skip-verification
```

Plan a walk backward through historical stable releases, always starting each
prepared backport from a fresh `main` worktree:

```bash
uv run python scripts/backport_release_history.py --limit 5
```

Add `--execute` to actually run Codex against each historical release in a
temporary worktree. The script writes a JSON report to
`.codex-meta-agent/backport-history-report.json` and flags releases whose
upstream compare or prepared diff cross the built-in "major backport"
thresholds.

By default the tracker writes temporary artifacts under `.codex-meta-agent/`
and updates `.github/codex-upstream-state.json`.

## GitHub Actions Requirements

The workflows expect:

- `OPENAI_API_KEY`
  so the workflow can bootstrap headless Codex auth with
  `codex login --with-api-key`
- `GITHUB_TOKEN`
  so the tracker can read GitHub metadata, push tracking branches, create pull
  requests, and create repository releases
- optionally `TRACKER_GIT_AUTHOR_NAME` and `TRACKER_GIT_AUTHOR_EMAIL`
  as repository or environment secrets when you want automated commits to use a
  specific identity instead of the triggering actor
- either PyPI Trusted Publishing configured for this repository and
  `.github/workflows/publish-pypi.yml`, or
- a `PYPI_API_TOKEN` secret on the repository or `pypi` environment

The publish workflow auto-detects `PYPI_API_TOKEN` and uses token-based
publishing when that secret is present. Otherwise it defaults to Trusted
Publishing without attaching a GitHub environment to the PyPI upload job. That
keeps the OIDC claim set aligned with PyPI publisher setups that do not specify
a GitHub environment.

For the current repository, the PyPI Trusted Publisher should be configured
with:

- owner: `oberon-ai`
- repository: `codex-python-sdk-unofficial`
- workflow filename: `.github/workflows/publish-pypi.yml`
- environment: leave blank unless you intentionally want PyPI to require the
  GitHub `pypi` environment claim

If branch protections or action restrictions block branch pushes or release
creation, the workflow contract will need to change accordingly.

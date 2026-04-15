# Upstream Tracking Automation

This repository now includes an automated tracker for `openai/codex`.

The goal is twofold:

- the `main` branch of this repository should keep pace with the upstream
  `main` branch
- GitHub releases in this repository should track upstream stable releases

## Key Pieces

- `.github/workflows/version-tracker.yml`
  runs the tracker daily and also supports manual `workflow_dispatch` runs.
- `.github/codex-upstream-state.json`
  records the last upstream `main` commit and stable release tag that this
  repository has already evaluated.
- `src/codex_meta_agent/version_tracker.py`
  fetches live GitHub metadata, prepares focused upstream context files, uses
  `SyncCodexSDKClient` to let Codex update the repository, and emits release
  metadata for the workflow.
- `.codex-meta-agent/`
  is a gitignored working directory for prompt briefs, upstream context files,
  structured response JSON, and generated release notes.

## Workflow Contract

The tracker workflow does the following:

1. checks out the repository and installs Python, `uv`, Node.js, and the
   Codex CLI
2. reads the committed `.github/codex-upstream-state.json`
3. fetches the live upstream `main` head and `releases/latest` metadata from
   GitHub
4. compares the upstream branch head against the previously tracked commit
5. downloads relevant upstream files under tracked prefixes such as
   `sdk/python/` and `codex-rs/app-server-protocol/` into `.codex-meta-agent/`
6. runs `uv run python -m codex_meta_agent`, which uses
   `SyncCodexSDKClient` plus an enforced JSON output schema to let Codex update
   the checked-out repository
7. runs the repository verification commands
8. commits branch-sync changes back to `main`
9. creates a GitHub release tagged as `upstream-<upstream-tag>` when the
   upstream stable release tag changed

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

## Release Tagging Policy

This repository prefixes upstream release tags before publishing them here:

- upstream tag: `rust-v0.120.0`
- repository release tag: `upstream-rust-v0.120.0`

The prefix keeps the repository free to adopt its own project versioning later
without colliding with the upstream tag namespace.

## Local Commands

Dry-run the tracker without invoking Codex or mutating the committed state:

```bash
uv run python -m codex_meta_agent --dry-run
```

Run the tracker locally but skip the project-wide verification loop:

```bash
uv run python -m codex_meta_agent --skip-verification
```

By default the tracker writes temporary artifacts under `.codex-meta-agent/`
and updates `.github/codex-upstream-state.json`.

## GitHub Actions Requirements

The workflow expects:

- `OPENAI_API_KEY`
  so the Codex CLI can authenticate in GitHub Actions
- `GITHUB_TOKEN`
  so the tracker can read GitHub metadata at higher rate limits and the
  workflow can push commits and publish releases
- permission for GitHub Actions to push back to `main`

If branch protections block direct pushes, the workflow contract will need to
change to create pull requests instead.

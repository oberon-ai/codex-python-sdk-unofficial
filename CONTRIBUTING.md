# Contributing

Thanks for contributing to `codex-agent-sdk-unofficial`.

This repository is an unofficial, preview-stage Python SDK for the Codex
app-server protocol. Contributions that improve correctness, documentation,
tests, examples, and protocol compatibility are all in scope.

## Ways To Contribute

- Fix bugs in the public SDK surface or lower-level transport and protocol
  layers.
- Improve type coverage, tests, fixtures, and example programs.
- Tighten the documentation when public behavior, contributor workflow, or
  repository structure changes.
- Refresh vendored schema snapshots or generated protocol artifacts when the
  upstream Codex protocol changes.

Small fixes can go straight to a pull request. For larger API, dependency, or
protocol-shape changes, explain the motivation and compatibility impact
clearly in the PR description.

## Development Environment

Contributors should have:

- Python 3.11+
- `uv`
- a working `codex` CLI on `PATH` for example scripts and schema snapshot
  refreshes

The authoritative project metadata lives in `pyproject.toml`. `uv.lock`
records the exact dependency set used for local verification, builds, and
code-generation work.

## Local Setup

Install the default contributor environment from the repository root:

```bash
uv sync
```

That creates `.venv/`, installs the package in editable mode, and includes the
default `dev` dependency group.

If you only need the runtime dependency set:

```bash
uv sync --no-dev
```

If you are working on generated protocol artifacts, install the extra tooling:

```bash
uv sync --group codegen
```

## Daily Workflow

Use targeted commands while iterating, then run the full verification loop
before opening a pull request.

Examples:

```bash
uv run pytest tests/test_query.py -q
uv run pytest tests/test_stdio_transport.py -q
uv run pytest tests/test_examples.py -q
```

When you are ready to validate the whole change, run:

```bash
uv run pytest -q
uv run mypy
uv run ruff check .
uv run ruff format --check .
uv build
```

If you are changing the upstream-tracking automation itself, also run the
tracker-focused tests:

```bash
uv run pytest tests/test_version_tracker.py -q
```

## Examples And Documentation

If you change the public API, contributor workflow, or user-visible behavior,
update the relevant docs in the same pull request.

- Keep the root `README.md` focused on the public package story.
- Keep detailed user and maintainer docs under `docs/`.
- Keep runnable CLI examples under `examples/`.
- Prefer root-package imports in examples and user-facing docs:
  `from codex_agent_sdk import ...`.

The examples are designed to run directly from the checkout:

```bash
uv run python examples/workspace_brief.py
uv run python examples/file_brief.py path/to/file.txt
uv run python examples/interactive_thread.py
```

## Schema Snapshot Workflow

The repository vendors canonical Codex schema snapshots under
`tests/fixtures/schema_snapshots/` so protocol changes stay reviewable and do
not depend on whichever local CLI binary a contributor happens to have.

Verify the current snapshots first:

```bash
uv run python scripts/vendor_protocol_schema.py --check
```

Refresh them intentionally:

```bash
uv run python scripts/vendor_protocol_schema.py
```

If you are intentionally changing the pinned Codex CLI version, opt in
explicitly:

```bash
uv run python scripts/vendor_protocol_schema.py --allow-version-change
```

Review the resulting JSON diff carefully. Snapshot changes are part of the
repository's compatibility boundary.

## Protocol Model Generation

Generated Python artifacts live under `src/codex_agent_sdk/generated/`.
Do not hand-edit files under `src/codex_agent_sdk/generated/`.

After changing the vendored schema inputs, regenerate the stable protocol
artifacts with:

```bash
uv run --group codegen python scripts/generate_protocol_models.py
```

Check for drift without rewriting files:

```bash
uv run --group codegen python scripts/generate_protocol_models.py --check
```

When upstream protocol inputs change, use this sequence:

1. Refresh the vendored schema snapshots.
2. Regenerate the checked-in Python artifacts.
3. Re-run the no-write checks and focused regression tests.

In command form:

```bash
uv run python scripts/vendor_protocol_schema.py --check
uv run --group codegen python scripts/generate_protocol_models.py --check
uv run pytest tests/test_codegen_regressions.py -q
```

## Automated Upstream Tracking

The repository has a scheduled GitHub Actions workflow at
`.github/workflows/version-tracker.yml` that keeps this checkout aligned with
`openai/codex`.

The automation is split across:

- `src/codex_meta_agent/`
  the Python orchestration package
- `.github/codex-upstream-state.json`
  the committed record of the last upstream main commit and stable release tag
- `.github/workflows/version-tracker.yml`
  the daily and manual trigger that runs the tracker, commits changes to
  `main`, and publishes releases

The tracker uses `SyncCodexSDKClient` so the maintenance job exercises the SDK
itself rather than bypassing it with a separate automation stack.

Useful local commands:

```bash
uv run python -m codex_meta_agent --dry-run
uv run python -m codex_meta_agent --skip-verification
```

The workflow assumes:

- GitHub Actions is allowed to push back to `main`
- `OPENAI_API_KEY` is available to the runner
- the tracked release tags in this repository are prefixed as
  `upstream-<upstream-tag>` so they do not collide with any future project
  versioning scheme

## Dependency Changes

Use `uv` as the only supported dependency manager for this repository.

- Add or update dependency declarations in `pyproject.toml`.
- Refresh `uv.lock` with `uv lock` or `uv sync` after dependency changes.
- Keep the runtime dependency surface intentionally small.
- Do not reintroduce `requirements/*.txt` files.

If a dependency is only needed for code generation or contributor tooling, keep
it out of the runtime dependency list and place it in the appropriate
dependency group instead.

## Design Boundaries

This repository relies on a layered layout. Preserve those boundaries when you
make code changes:

- keep subprocess and stdio transport concerns in `transport/`
- keep JSON-RPC envelopes, routing, and request correlation in `rpc/`
- keep generated wire artifacts isolated in `generated/`
- keep repository-tracking automation in `src/codex_meta_agent/`
- keep handwritten protocol adapters and registries in `protocol/`
- keep the public SDK surface in `client.py`, `query.py`, `events.py`,
  `approvals.py`, `results.py`, `options.py`, `errors.py`, and `retry.py`

When a new abstraction becomes public:

1. Define it in the appropriate public-layer module.
2. Re-export it from `codex_agent_sdk`.
3. Update the relevant docs and examples.
4. Add or update tests that lock down the public import surface.

## Pull Request Checklist

Before opening a pull request, make sure the change set is coherent and
reviewable:

- run targeted tests for the area you touched
- run the full verification loop when the change is ready
- include doc updates for public API or workflow changes
- review generated artifacts intentionally instead of treating them as opaque
- explain any compatibility impact, schema refresh, or dependency update in the
  PR description

If a change updates vendored snapshots, generated protocol models, or both,
call that out explicitly so reviewers know to inspect those diffs with the
right expectations.

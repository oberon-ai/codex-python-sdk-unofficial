# Contributing

This repository uses `uv` for dependency management, test execution, and
package builds. The authoritative project metadata lives in `pyproject.toml`,
and `uv.lock` records the exact toolchain used for local work and CI-style
verification.

## Local Setup

Install the default contributor environment:

```bash
uv sync
```

That creates `.venv/`, installs the package in editable mode, and includes the
default `dev` dependency group.

If you only want the runtime dependency set:

```bash
uv sync --no-dev
```

## Main Verification Loop

Run the core quality gates from the repository root:

```bash
uv run pytest
uv run mypy
uv run ruff check .
uv run ruff format --check .
uv build
```

For quick iteration on a smaller slice, pass explicit test paths to `pytest`.

## Running The Examples

The example scripts are designed to run directly from the checkout:

```bash
uv run python examples/workspace_brief.py
uv run python examples/file_brief.py path/to/file.txt
uv run python examples/interactive_thread.py
```

They expect a working `codex` CLI on `PATH` unless you pass `--codex-bin`.

## Code Generation Workflow

Protocol model generation uses a separate dependency group so day-to-day
contributor syncs stay lean.

Install the extra tooling when you need to regenerate checked-in artifacts:

```bash
uv sync --group codegen
```

Then use:

```bash
uv run --group codegen python scripts/generate_protocol_models.py
uv run --group codegen python scripts/generate_protocol_models.py --check
```

The generated outputs live under `src/codex_agent_sdk/generated/` and should
not be hand-edited.

## Schema Snapshot Workflow

Vendored schema snapshots are maintained separately from Python code generation.

Verify the current snapshots:

```bash
uv run python scripts/vendor_protocol_schema.py --check
```

Refresh them intentionally:

```bash
uv run python scripts/vendor_protocol_schema.py
```

If the pinned Codex CLI version changes, opt in explicitly:

```bash
uv run python scripts/vendor_protocol_schema.py --allow-version-change
```

## Review Boundaries

- Do not hand-edit modules under `src/codex_agent_sdk/generated/`.
- Keep handwritten transport code in `transport/`, JSON-RPC routing in `rpc/`,
  and higher-level adapters in `protocol/` or the public SDK modules.
- Prefer root-package imports in examples and user-facing docs:
  `from codex_agent_sdk import ...`.
- When schema inputs change, refresh vendored snapshots first, then regenerate
  the checked-in protocol artifacts.

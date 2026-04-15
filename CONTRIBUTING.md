# Contributing

This repository uses `uv` as the only supported project manager and packaging
workflow. The authoritative project metadata lives in `pyproject.toml`, and
exact reproducible tool versions live in `uv.lock`.

## Local Setup

Install the default contributor environment:

```bash
uv sync
```

That creates `.venv/`, installs the package in editable mode, and includes the
default `dev` dependency group.

Run the main verification loop with:

```bash
uv run pytest
uv run mypy
uv run ruff check .
uv run ruff format --check .
uv build
```

## Codegen Workflow

Protocol model regeneration uses a separate dependency group so ordinary
contributor syncs stay lean.

Sync the extra tooling when you need to regenerate generated artifacts:

```bash
uv sync --group codegen
```

Then run:

```bash
uv run --group codegen python scripts/generate_protocol_models.py
uv run --group codegen python scripts/generate_protocol_models.py --check
```

Schema snapshot vendoring stays stdlib-only apart from the pinned `codex` CLI:

```bash
uv run python scripts/vendor_protocol_schema.py --check
```

## Review Boundaries

- Do not hand-edit modules under `src/codex_agent_sdk/generated/`.
- Keep handwritten behavior in the public SDK, protocol, transport, and RPC
  packages.
- When schema inputs change, refresh vendored snapshots first, then regenerate
  the checked-in protocol artifacts.

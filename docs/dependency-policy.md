# Dependency Policy

This repository keeps the runtime dependency surface intentionally small and
uses `uv` as the only supported dependency manager.

- Runtime is `asyncio`-only.
- Transport, subprocess management, queues, cancellation, and streaming stay on
  the Python standard library.
- The repo does not introduce `anyio`, Trio, HTTP client wrappers, or generic
  async abstraction layers.
- Code generation tooling is isolated from day-to-day development so end users
  do not pay for maintainer-only tools.

## Compatibility Metadata Versus Repo Pins

`pyproject.toml` declares compatible version ranges for published package
metadata and local dependency groups.

`uv.lock` records the exact resolved package set for reproducible local work,
verification, builds, and code generation. When you need the locked toolchain,
prefer:

```bash
uv sync
```

or, for codegen work:

```bash
uv sync --group codegen
```

## Runtime Dependency

The runtime dependency set is one package:

| Package | Locked version | Metadata range | Why it exists |
| --- | --- | --- | --- |
| `pydantic` | `2.13.0` | `>=2.13,<3` | Generated protocol models need a typed validation and serialization layer. |

Why nothing else is runtime-critical:

- `typing_extensions` is not a direct dependency because the project baseline
  is Python 3.11.
- No generic async wrapper library is added because this SDK is intentionally
  `asyncio`-native.
- No codegen library is imported at runtime; generated artifacts live under
  `src/codex_agent_sdk/generated/`.

## Development Tooling

These tools live in the default `dev` dependency group:

| Package | Locked version | Group specifier | Why it exists |
| --- | --- | --- | --- |
| `mypy` | `1.20.1` | `>=1.20,<2` | Enforces strict typing across handwritten code. |
| `pytest` | `9.0.3` | `>=9,<10` | Runs the repository test suite. |
| `pytest-asyncio` | `1.3.0` | `>=1.3,<2` | Supports native async transport and client tests. |
| `ruff` | `0.15.10` | `>=0.15,<0.16` | Provides linting and formatting. |

Because `uv` installs the `dev` group by default, the normal contributor setup
is simply:

```bash
uv sync
```

Runtime-only setup is still available:

```bash
uv sync --no-dev
```

## Codegen-Only Tooling

The repository keeps schema-to-model generation tooling out of the default
sync.

| Package | Locked version | Group specifier | Why it exists |
| --- | --- | --- | --- |
| `datamodel-code-generator` | `0.56.0` | `>=0.56,<0.57` | Generates Pydantic model scaffolding from the vendored Codex JSON Schema bundle. |

Sync it explicitly with:

```bash
uv sync --group codegen
```

## Build Tooling

The package build backend is `uv_build`, and distribution builds should go
through uv directly:

```bash
uv build
```

There is no separate `build` package in the contributor dependency group.

## Docs Tooling

There is no docs-specific dependency group yet. The current docs are
handwritten Markdown, so adding MkDocs or Sphinx would broaden the dependency
surface without supporting an existing workflow.

## Command Summary

Typical local development:

```bash
uv sync
```

Targeted verification:

```bash
uv run pytest
uv run mypy
uv run ruff check .
uv run ruff format --check .
```

Protocol code generation work:

```bash
uv sync --group codegen
uv run --group codegen python scripts/generate_protocol_models.py --check
```

Refreshing the vendored schema snapshots does not require the codegen-only
Python dependencies. It uses the pinned `codex` runtime recorded in
`tests/fixtures/schema_snapshots/vendor_manifest.json` plus the stdlib-only
`scripts/vendor_protocol_schema.py` helper:

```bash
uv run python scripts/vendor_protocol_schema.py --check
```

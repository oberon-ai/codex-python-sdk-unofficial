# Dependency Policy

This repository keeps the runtime dependency surface intentionally small.

- Runtime is `asyncio`-only.
- Transport, subprocess management, queues, cancellation, and streaming stay on the Python standard library.
- The repo does not introduce `anyio`, Trio, HTTP client wrappers, or generic async abstraction layers in v1.
- Code generation tooling is isolated from runtime dependencies so end users do not pay for maintainer-only tools.

## Compatibility Metadata Versus Repo Pins

`pyproject.toml` declares compatible version ranges for published package metadata.
The `requirements/*.txt` files pin exact versions for reproducible local work,
CI, and future regeneration scripts.

## Runtime Dependency

The runtime dependency set is one package:

| Package | Repo pin | Metadata range | Why it exists |
| --- | --- | --- | --- |
| `pydantic` | `2.13.0` | `>=2.13,<3` | Planned generated protocol models need a typed validation and serialization layer, and the upstream Codex Python SDK already uses Pydantic v2 for this job. |

Why nothing else is runtime-critical yet:

- `typing_extensions` is not a direct dependency because the project baseline is Python 3.11.
- No generic async wrapper library is added because this SDK is intentionally `asyncio`-native.
- No codegen library is imported at runtime; generated artifacts will live under `src/codex_agent_sdk/generated/`.

## Development Tooling

These tools are part of the normal maintainer workflow and are pinned in
`requirements/dev.txt`:

| Package | Repo pin | Metadata range | Why it exists |
| --- | --- | --- | --- |
| `build` | `1.4.3` | `>=1.4,<2` | Verifies sdists and wheels build cleanly. |
| `mypy` | `1.20.1` | `>=1.20,<2` | Enforces strict typing across handwritten code. |
| `pytest` | `9.0.3` | `>=9,<10` | Runs the repository test suite. |
| `pytest-asyncio` | `1.3.0` | `>=1.3,<2` | Supports native async transport and client tests without adding a non-stdlib runtime abstraction. |
| `ruff` | `0.15.10` | `>=0.15,<0.16` | Provides formatting and linting. |

## Codegen-Only Tooling

The repo keeps schema-to-model generation tooling out of the default development
install. Use it only when regenerating protocol artifacts.

| Package | Repo pin | Metadata range | Why it exists |
| --- | --- | --- | --- |
| `datamodel-code-generator` | `0.56.0` | `>=0.56,<0.57` | Generates Pydantic model scaffolding from the Codex JSON Schema bundle. |

## Docs Tooling

There is no docs-specific dependency group yet. The current docs are handwritten
Markdown, so adding MkDocs or Sphinx now would broaden the dependency surface
without supporting an active workflow.

## Install Modes

Typical local development:

```bash
python -m pip install -e ".[dev]"
```

Reproducible local or CI setup using the repo pins:

```bash
python -m pip install -e . -r requirements/dev.txt
```

Protocol code generation work:

```bash
python -m pip install -e . -r requirements/dev.txt -r requirements/codegen.txt
```

Refreshing the vendored schema snapshots does not require the codegen-only
Python dependencies. It uses the pinned `codex` runtime recorded in
`tests/fixtures/schema_snapshots/vendor_manifest.json` plus the stdlib-only
`scripts/vendor_protocol_schema.py` helper.

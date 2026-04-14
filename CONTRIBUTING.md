# Contributing

## Local setup

The project uses a `src/` layout and expects Python 3.11 or newer.

Standard editable install:

```bash
python -m pip install -e ".[dev]"
```

Reproducible repo pins for local work or CI:

```bash
python -m pip install -e . -r requirements/dev.txt
```

Code generation work pulls in a separate maintainer-only toolchain:

```bash
python -m pip install -e . -r requirements/dev.txt -r requirements/codegen.txt
```

The dependency rationale and pinning policy live in
`docs/dependency-policy.md`.

## Vendored schema snapshots

The app-server schema source of truth for this repo is the checked-in pair of
canonical JSON snapshots under `tests/fixtures/schema_snapshots/`. Their current
generator pin and hashes live in
`tests/fixtures/schema_snapshots/vendor_manifest.json`.

Typical maintenance flow:

```bash
python scripts/vendor_protocol_schema.py --check
```

Intentional pin bumps require an explicit opt-in:

```bash
python scripts/vendor_protocol_schema.py --allow-version-change
```

If the manifest pin and the installed `codex` binary version do not match, the
script fails fast rather than silently refreshing the snapshots from the wrong
runtime version. The rationale and workflow details live in
`docs/schema-vendoring.md`.

## Generated Pydantic wire models

The first generated Python wire-model layer is checked in at:

- `src/codex_agent_sdk/generated/stable.py`

It is generated from the pinned stable schema snapshot, not directly from a
developer machine's live `codex` output.

Verify that the checked-in generated models are current:

```bash
python scripts/generate_protocol_models.py --check
```

Refresh them intentionally:

```bash
python scripts/generate_protocol_models.py
```

The script fails fast if the installed `datamodel-code-generator` version does
not match the repo pin in `requirements/codegen.txt`.

## Quality gates

Run these commands from the repository root:

```bash
python -m ruff format .
python -m ruff check .
python -m mypy src tests
python -m pytest
python -m build
```

For a CI-style no-write pass, use:

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy src tests
python -m pytest
python -m build
```

## Generated-code boundary

Handwritten code stays under strict linting and type checking. The
`src/codex_agent_sdk/generated/` tree is reserved for machine-generated protocol
artifacts, so Ruff and mypy exclude it by default to leave room for future code
generation without weakening checks across the rest of the repository.

## Transport debugging

The low-level app-server transport can emit opt-in debug logs for process
startup, JSON-RPC frame direction, request ids, and shutdown milestones.

```python
import logging

from codex_agent_sdk import AppServerClient, AppServerConfig

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("codex_agent_sdk.debug")

async with AppServerClient(
    AppServerConfig(
        debug_logging=True,
        debug_logger=logger,
    )
) as client:
    await client.initialize()
```

The debug mode stays redacted by default:

- frame metadata such as direction, method, and request id are visible
- prompt-like text, file paths, diffs, and env values are redacted or truncated
- environment override keys are summarized without logging their values

Tests can assert on the structured `logging` extras attached to each record,
including `codex_debug_event`, `codex_direction`, `codex_request_id`,
`codex_method`, and `codex_frame_preview`.

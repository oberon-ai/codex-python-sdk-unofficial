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

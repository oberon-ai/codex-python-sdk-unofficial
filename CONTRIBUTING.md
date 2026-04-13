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

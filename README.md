# Unofficial Codex Python SDK (`codex-agent-sdk-unofficial`)

Unofficial Python SDK for the Codex app-server protocol.

The package launches `codex app-server --listen stdio://`, speaks JSON-RPC v2
over stdio, and keeps Codex concepts such as threads, turns, items, and
approval requests visible in the Python API.

Status: preview. The low-level `AppServerClient`, the one-shot async `query()`
helper, the stateful async `CodexSDKClient`, and the synchronous
`SyncCodexSDKClient` wrapper are all usable today. The sync client runs the
async client on a private event loop thread, so it is convenient for
synchronous call sites but less natural than using the native async APIs
directly.

## Highlights

- Native `asyncio` transport for the local Codex app-server.
- Typed request and response models generated from vendored schema snapshots.
- Streamed turn events for assistant text, reasoning text, command output, item
  lifecycle, approvals, token usage, and raw passthrough envelopes.
- First-class approval helpers built around `ApprovalRequest` and
  `ApprovalDecision`.
- A small `query()` helper for single-turn scripts and automation jobs.
- A stateful `CodexSDKClient` for higher-level async thread workflows.
- A `SyncCodexSDKClient` wrapper for synchronous Python.
- A lower-level `AppServerClient` for full control over thread and turn
  lifecycle calls.
- A scheduled meta-agent workflow that uses this SDK to track
  `openai/codex` branch and release drift.

## Requirements

- Python 3.11+
- A working `codex` CLI on `PATH`
- Whatever authentication your local Codex CLI already requires

## Install

Install from PyPI:

```bash
pip install codex-agent-sdk-unofficial
```

Or with `uv`:

```bash
uv add codex-agent-sdk-unofficial
```

Install from a checkout for local development:

```bash
uv sync
```

That creates `.venv/`, installs the package in editable mode, and includes the
default contributor tooling.

If you only want the runtime dependency set:

```bash
uv sync --no-dev
```

## Quick Start

Use `query()` when you want one turn, an ephemeral thread, and streamed events
from async code:

```python
import asyncio

from codex_agent_sdk import AgentTextDeltaEvent, CodexOptions, TurnCompletedEvent, query


async def main() -> None:
    async for event in query(
        prompt="Summarize the purpose of this repository.",
        options=CodexOptions(
            cwd=".",
            approval_policy="on-request",
            model="gpt-5.4",
        ),
    ):
        if isinstance(event, AgentTextDeltaEvent):
            print(event.text_delta, end="", flush=True)
        elif isinstance(event, TurnCompletedEvent):
            print(f"\n\nturn status: {event.turn_status}")


asyncio.run(main())
```

Use `SyncCodexSDKClient` when you need synchronous Python. It wraps the async
client on a private background loop, so it is practical for non-async scripts
but still not as natural as the native async surface:

```python
from codex_agent_sdk import AgentTextDeltaEvent, CodexOptions, SyncCodexSDKClient


with SyncCodexSDKClient(
    options=CodexOptions(
        cwd=".",
        approval_policy="never",
        model="gpt-5.4",
    )
) as client:
    turn = client.query("Summarize the purpose of this repository.")
    for event in turn:
        if isinstance(event, AgentTextDeltaEvent):
            print(event.text_delta, end="", flush=True)

    result = turn.wait()
    print(f"\n\nturn status: {result.status}")
```

Use `CodexSDKClient` when you want a long-lived async client that manages an
active thread across multiple turns. Drop down to `AppServerClient` when you
need explicit control over app-server requests:

```python
import asyncio

from codex_agent_sdk import AppServerClient, AppServerConfig


async def main() -> None:
    async with AppServerClient(AppServerConfig()) as client:
        await client.initialize()
        thread = await client.thread_start(ephemeral=True)
        turn = await client.turn_start(
            thread_id=thread.thread.id,
            input="List the highest-risk files in this repository.",
        )
        completion = await client.wait_for_turn_completed(
            thread_id=thread.thread.id,
            turn_id=turn.turn.id,
        )
        print(completion.status)


asyncio.run(main())
```

## Examples

The repository ships a few runnable examples under [examples](examples):

- `uv run python examples/workspace_brief.py`
  streams a short brief for the current workspace with the one-shot helper.
- `uv run python examples/file_brief.py path/to/file.txt`
  summarizes a single UTF-8 text file.
- `uv run python examples/interactive_thread.py`
  runs a small interactive loop on top of `AppServerClient`, including manual
  approval handling.

See [examples/README.md](https://github.com/oberon-ai/codex-python-sdk-unofficial/blob/main/examples/README.md)
for details and command-line options.

## Documentation

- [Documentation index](https://github.com/oberon-ai/codex-python-sdk-unofficial/blob/main/docs/README.md)
- [API overview](https://github.com/oberon-ai/codex-python-sdk-unofficial/blob/main/docs/api.md)
- [Configuration and option layering](https://github.com/oberon-ai/codex-python-sdk-unofficial/blob/main/docs/codex-options.md)
- [Package layout](https://github.com/oberon-ai/codex-python-sdk-unofficial/blob/main/docs/package-layout.md)
- [Public import policy](https://github.com/oberon-ai/codex-python-sdk-unofficial/blob/main/docs/public-import-policy.md)
- [Schema vendoring](https://github.com/oberon-ai/codex-python-sdk-unofficial/blob/main/docs/schema-vendoring.md)
- [Protocol model code generation](https://github.com/oberon-ai/codex-python-sdk-unofficial/blob/main/docs/protocol-model-codegen.md)
- [Upstream tracking automation](https://github.com/oberon-ai/codex-python-sdk-unofficial/blob/main/docs/upstream-tracking.md)
- [Dependency policy](https://github.com/oberon-ai/codex-python-sdk-unofficial/blob/main/docs/dependency-policy.md)
- [Contributing](https://github.com/oberon-ai/codex-python-sdk-unofficial/blob/main/CONTRIBUTING.md)

## Development

Common verification commands:

```bash
uv run pytest
uv run mypy
uv run ruff check .
uv run ruff format --check .
uv build
```

Contributor setup, code generation, and schema refresh workflows are documented
in [CONTRIBUTING.md](https://github.com/oberon-ai/codex-python-sdk-unofficial/blob/main/CONTRIBUTING.md).

## Automated Upstream Tracking

This repository now ships a daily GitHub Actions workflow at
`.github/workflows/version-tracker.yml`.

The workflow:

- checks the latest stable `openai/codex` GitHub release every day and on
  manual dispatch
- compares that release tag against the committed
  `.github/codex-upstream-state.json`
- uses `python -m codex_meta_agent` plus `SyncCodexSDKClient` to let Codex
  reconcile local code, docs, tests, and release metadata for that release
- keeps the automation runtime on a controller checkout while Codex edits a
  separate clean target checkout
- runs the repository verification commands
- commits release-tracking changes to
  `puck/frontier-realese--v<version>` from a fresh `main` checkout
- opens a pull request for that prepared frontier release branch
- ships a separate manual workflow for backfilling older releases on
  `puck/flegacy-release--v<version>`
- publishes a GitHub release tagged `v<version>` and publishes the matching
  PyPI release when that branch is merged to `main`

See [docs/upstream-tracking.md](https://github.com/oberon-ai/codex-python-sdk-unofficial/blob/main/docs/upstream-tracking.md)
for the detailed workflow contract, local dry-run command, and required GitHub
Actions secrets.

## License

This project is licensed under the Apache License 2.0. See
[LICENSE](https://github.com/oberon-ai/codex-python-sdk-unofficial/blob/main/LICENSE).

## Project Notes

- This project is unofficial and is not affiliated with OpenAI
- This was implemented and released by the project maintainers
- All PRs merged to main must be approved by the maintainers
- Stable protocol artifacts are generated from vendored schema snapshots checked
  into this repository.

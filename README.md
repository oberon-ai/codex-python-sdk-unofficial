# Unofficial Codex Python SDK (`codex-agent-sdk-unofficial`)

Unofficial, native-async Python SDK for the Codex app-server protocol.

The package launches `codex app-server --listen stdio://`, speaks JSON-RPC v2
over stdio, and keeps Codex concepts such as threads, turns, items, and
approval requests visible in the Python API.

Status: preview. The low-level `AppServerClient` and the one-shot `query()`
helper are usable today. `CodexSDKClient` is exported as the intended
stateful-thread entry point, but its high-level workflow helpers are not fully
implemented yet.

## Highlights

- Native `asyncio` transport for the local Codex app-server.
- Typed request and response models generated from vendored schema snapshots.
- Streamed turn events for assistant text, reasoning text, command output, item
  lifecycle, approvals, token usage, and raw passthrough envelopes.
- First-class approval helpers built around `ApprovalRequest` and
  `ApprovalDecision`.
- A small `query()` helper for single-turn scripts and automation jobs.
- A lower-level `AppServerClient` for full control over thread and turn
  lifecycle calls.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) for local environment management
- A working `codex` CLI on `PATH`
- Whatever authentication your local Codex CLI already requires

## Install From Source

The project is currently meant to be used from a checkout:

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

Use `query()` when you want one turn, an ephemeral thread, and streamed events:

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

Drop down to `AppServerClient` when you need explicit control over app-server
requests:

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

See [examples/README.md](examples/README.md) for details and command-line
options.

## Documentation

- [Documentation index](docs/README.md)
- [API overview](docs/api.md)
- [Configuration and option layering](docs/codex-options.md)
- [Package layout](docs/package-layout.md)
- [Public import policy](docs/public-import-policy.md)
- [Schema vendoring](docs/schema-vendoring.md)
- [Protocol model code generation](docs/protocol-model-codegen.md)
- [Dependency policy](docs/dependency-policy.md)
- [Contributing](CONTRIBUTING.md)

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
in [CONTRIBUTING.md](CONTRIBUTING.md).

## Project Notes

- This project is unofficial and is not affiliated with OpenAI.
- Stable protocol artifacts are generated from vendored schema snapshots checked
  into this repository.
- A standalone license file has not been added yet.

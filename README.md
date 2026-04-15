# Unofficial Codex Python SDK (`codex-python-sdk-unofficial`)

An unofficial, natively async Python SDK for OpenAI's [Codex](https://github.com/openai/codex) app-server protocol. It speaks `codex app-server --listen stdio://` over JSON-RPC v2 and aims to feel ergonomic in Python without losing Codex-native concepts like threads, turns, items, and approval requests.

Status: preview. The implemented happy paths today are the low-level `AppServerClient`, the one-shot `query()` helper, typed turn events, approval adapters, and the supporting option and retry utilities. The higher-level `CodexSDKClient` API is part of the public contract, but its thread lifecycle helpers are still being wired up.

## Quick Start

Requirements:

- Python 3.11+
- A working `codex` CLI installation on your `PATH`
- Whatever authentication setup your local Codex CLI already requires

Install from source:

```bash
python -m pip install -e .
```

Stream a single turn with the one-shot helper:

```python
import asyncio

from codex_agent_sdk import AgentTextDeltaEvent, CodexOptions, query


async def main() -> None:
    async for event in query(
        prompt="Summarize the purpose of this repository.",
        options=CodexOptions(
            model="gpt-5.4",
            cwd=".",
            approval_policy="on-request",
        ),
    ):
        if isinstance(event, AgentTextDeltaEvent):
            print(event.text_delta, end="", flush=True)


asyncio.run(main())
```

Drop down to the low-level typed client when you need direct control over threads and turns:

```python
import asyncio

from codex_agent_sdk import AppServerClient, AppServerConfig


async def main() -> None:
    async with AppServerClient(AppServerConfig()) as client:
        await client.initialize()
        thread = await client.thread_start(ephemeral=True)
        turn = await client.turn_start(
            thread_id=thread.thread.id,
            input="List the highest-risk files in this repo.",
        )
        completion = await client.wait_for_turn_completed(
            thread_id=thread.thread.id,
            turn_id=turn.turn.id,
        )
        print(completion.status)


asyncio.run(main())
```

See [examples](examples) for runnable scripts and the [docs](docs/public-api-contract.md) directory for the detailed contract and design notes.

## Context

### Motivation

This project exists because the upstream [Codex Python SDK](https://github.com/openai/codex/tree/main/sdk/python) is still experimental and lives inside the main Codex monorepo. As Codex has become materially more capable at repository-scale coding tasks, the need for a Python SDK that can be embedded directly into async services, background workers, and higher-order agentic harnesses has become much more urgent.

The biggest practical gap is async behavior. A sync-first or thread-offloaded wrapper is harder to compose inside existing `asyncio` applications, makes cancellation and backpressure less predictable, and turns streaming or approval workflows into coordination problems between worker threads and the event loop. This repository instead keeps the transport, JSON-RPC routing, event streaming, and approval handling natively async end to end.

The official Python artifacts are still valuable reference material, especially for protocol shape and code generation, but they do not currently offer the same curated ergonomic surface as the [Codex TypeScript SDK](https://github.com/openai/codex/tree/main/sdk/typescript) or the same Python-first async feel that many developers liked in the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python). This project aims to fill that gap while staying strictly Codex-native in terminology and protocol behavior.

This repository is being shared publicly so teams building agentic coding harnesses can inspect the protocol mapping, reuse the async transport patterns, and collaborate on compatibility fixes as Codex evolves. A standalone release license should be finalized before the first packaged public release.

This project is not affiliated with OpenAI.

### Features

- Native `asyncio` transport for `codex app-server --listen stdio://`, rather than wrapping blocking IO behind worker threads.
- A small one-shot `query()` helper that creates an ephemeral thread, streams typed events for one turn, and cleans up automatically.
- A low-level `AppServerClient` with typed methods for initialization, thread lifecycle calls, turn lifecycle calls, raw requests, notifications, and server requests.
- Typed streaming events for assistant text, reasoning text, command output, item lifecycle, token usage, and raw passthrough envelopes.
- First-class approval handling via `ApprovalRequest`, `ApprovalDecision`, `ApprovalRequestedEvent.respond()`, and an optional async approval callback.
- Clean separation between user-facing behavior defaults (`CodexOptions`) and app-server bootstrap concerns (`AppServerConfig`).
- Utility helpers such as `TurnOutputAggregator` for assembling streamed output and `retry_on_overload()` for opt-in overload backoff on safe operations.

Compared with the official experimental Codex Python SDK, this project puts more emphasis on a native-async contract, a smaller curated public surface, and application-facing ergonomics. Compared with the Claude Agent SDK, it intentionally borrows only the pleasant parts of the API shape while keeping Codex terminology and protocol semantics front and center.

### Implementation

The repository was planned and implemented almost entirely by Puck Code (formerly YoloPilot), Oberon's agentic coding harness. That made it a useful dogfooding exercise: the SDK was built while actively evaluating Codex as a coding agent, not as a paper design disconnected from real harness needs.

The implementation is intentionally layered. Generated protocol artifacts live under `src/codex_agent_sdk/generated/`, handwritten protocol adapters and registries live under `src/codex_agent_sdk/protocol/`, the async transport and JSON-RPC routing live under `transport/` and `rpc/`, and the public ergonomic surface lives in `client.py`, `query.py`, `events.py`, `approvals.py`, and `results.py`. That separation keeps schema-derived code easy to regenerate and handwritten behavior easy to review.

At the current stage, the low-level app-server client, one-shot query flow, typed events, approval models, retry helpers, and fake app-server testing harness are implemented. The high-level `CodexSDKClient` thread workflow API is designed and exposed, but its convenience methods are still being completed.

## User Guide

### Prerequisites and installation

You need Python 3.11 or newer and a working `codex` CLI installation. For local development, the standard editable install is:

```bash
python -m pip install -e .
```

Contributor and maintainer setup, including dev and codegen dependencies, is documented in [CONTRIBUTING.md](CONTRIBUTING.md).

### Choosing an entry point

- Use `query()` for one-off scripts, CI jobs, and single-turn structured-output tasks.
- Use `AppServerClient` when you need direct typed access to `thread/start`, `thread/resume`, `thread/fork`, `turn/start`, `turn/steer`, raw notifications, or manual approval handling.
- Plan to use `CodexSDKClient` for long-lived thread workflows once its helpers land; today that API is present but not fully wired up.

### Configuration

`CodexOptions` controls model behavior and sticky thread or turn defaults such as `cwd`, approval policy, reasoning effort, summary mode, personality, service tier, instructions, and sandbox settings.

`AppServerConfig` controls how the SDK launches `codex app-server`, including the `codex` binary path, subprocess environment overrides, startup and shutdown timeouts, debug logging, and explicit experimental API opt-in.

Further detail is in [docs/codex-options.md](docs/codex-options.md).

### Authentication and environment

The SDK launches a local `codex app-server` subprocess and inherits the current process environment by default. In practice, if `codex app-server --listen stdio://` works in your shell, the SDK should be able to launch it too.

If you need to override runtime environment or credentials, pass `AppServerConfig(env={...})`. If the `codex` binary is not on your `PATH`, set `AppServerConfig(codex_bin="...")`.

### Further reading

- [docs/public-api-contract.md](docs/public-api-contract.md)
- [docs/codex-options.md](docs/codex-options.md)
- [docs/ergonomics-mapping.md](docs/ergonomics-mapping.md)
- [docs/package-layout.md](docs/package-layout.md)
- [docs/upstream-reference-map.md](docs/upstream-reference-map.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)

## Change Management

The OpenAI team is rapidly developing Codex, so significant changes to the app-server surface should be expected. This repository therefore treats schema vendoring, generated model refreshes, and compatibility review as part of the product rather than as afterthoughts.

### Version Tracking Strategy

The repository already checks in pinned stable and experimental schema snapshots under `tests/fixtures/schema_snapshots/`, along with a manifest of the expected Codex version and schema hashes. The intended maintenance loop is:

1. On a scheduled GitHub Actions run, install the pinned maintainer toolchain and run `python scripts/vendor_protocol_schema.py --check`.
2. If upstream schema or version drift is detected, refresh the vendored snapshots in a dedicated branch, regenerate the stable protocol models and registries with `python scripts/generate_protocol_models.py`, and run the full test suite.
3. Open a reviewable PR containing the snapshot diff, generated artifact diff, and any handwritten compatibility fixes. Oberon maintainers should approve that PR before merge because protocol changes can affect approvals, event routing, and backwards compatibility.
4. Tag and publish a new package version only after the refreshed SDK passes its compatibility checks.

For users, the safest upgrade path is to pin SDK versions, read the release notes or upgrade PR summary, and upgrade deliberately instead of floating to the newest commit. Source users can pull the updated revision and reinstall with `python -m pip install -e .`; maintainers should also rerun the vendoring and codegen checks when intentionally tracking a new upstream Codex release.

### Deprecation Plan

As Codex adoption increases, and as engineering teams deploy increasingly sophisticated agentic coding harnesses, it is likely that the OpenAI team will invest further in their SDKs so that the power of their coding agent can be deployed by AI-native engineering organizations in increasingly automated ways.

If and when the official Codex Python SDK reaches parity with this project's native-async ergonomics and public surface, this project should be deprecated in favor of the official SDK.

## Architecture

At a high level, the SDK follows the same boundary lines as the Codex app-server protocol:

- `transport/` owns subprocess lifecycle, stdio wiring, stderr capture, and JSONL framing.
- `rpc/` owns JSON-RPC envelopes, request correlation, notification routing, and server-request routing.
- `generated/` contains machine-generated stable protocol models and registries derived from vendored schema snapshots.
- `protocol/` contains handwritten adapters that map raw wire payloads into typed public events and approval objects.
- `client.py`, `query.py`, `events.py`, `approvals.py`, and `results.py` make up the public SDK surface.
- `testing/` and `tests/fixtures/` provide deterministic fake app-server scripts, schema snapshots, and golden fixtures for regression testing.

The one-shot `query()` helper is the easiest way to see the architecture in action: it boots an `AppServerClient`, performs the required `initialize` handshake, starts an ephemeral thread, subscribes to notifications and server requests before `turn/start`, streams typed `TurnEvent` values, aggregates the terminal result, and then closes the subprocess connection. Higher-level thread-centric ergonomics are planned on top of the same transport and routing layers.

For the deeper design rationale, see [docs/public-api-contract.md](docs/public-api-contract.md), [docs/package-layout.md](docs/package-layout.md), and the ADRs in [docs/adr](docs/adr).

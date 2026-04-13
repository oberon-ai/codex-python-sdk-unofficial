# ADR 0001: Native-Async App-Server Scope And Non-Goals

- Status: Accepted
- Date: 2026-04-13

## Context

This repository is for an unofficial Python SDK for Codex. The project needs to feel like a real async library, not a thin shell wrapper around the CLI.

The main risk at this stage is architectural drift toward shortcuts that would weaken the public contract:

- building around `codex exec --json` instead of the app-server protocol
- hiding blocking behavior behind `asyncio.to_thread(...)` or sync wrappers
- depending on the current experimental Codex Python SDK as the transport layer
- copying Claude Agent SDK internals even though it solves a different transport problem

The upstream Codex app-server references define the protocol surface and lifecycle. Claude Agent SDK references are useful for developer ergonomics and async UX patterns, but not as an implementation template for transport or protocol handling.

## Decision

The SDK will speak to `codex app-server --listen stdio://` over newline-delimited JSON-RPC v2 on stdio.

That decision carries the following implementation rules:

1. The transport layer is native `asyncio` end to end.
2. Public IO paths must remain genuinely async and must not block the event loop.
3. The connection must perform `initialize`, then emit `initialized`, before any other protocol method.
4. The SDK will expose Codex terminology in the public API: `thread`, `turn`, `item`, and approval request / decision.
5. The implementation will prefer a thin handwritten layer on top of generated protocol models rather than re-encoding wire shapes by hand across the codebase.

## Baseline Runtime

The baseline Python version for the project is Python 3.11 or newer.

That baseline keeps the implementation free to use modern `asyncio` primitives and cancellation patterns without compatibility shims. If later tasks introduce packaging metadata, that metadata should enforce `>=3.11`.

## Supported V1 Scope

Version 1 is expected to cover the smallest public surface that still feels like a real SDK:

- one-shot querying through a convenience helper such as `query(...)`
- stateful thread workflows in a long-lived client
- starting, resuming, and forking threads
- starting turns and handling steering or interruption of in-flight work
- streaming text deltas, item lifecycle events, token usage, and raw notifications
- approval handling for command execution, file changes, and related permission flows
- structured output through an output schema
- low-level JSON-RPC escape hatches for advanced callers

## Explicit Non-Goals For V1

The following are intentionally out of scope for the first serious version:

- building the SDK around `codex exec --json`
- wrapping a blocking transport in `asyncio.to_thread(...)`
- adding sync wrappers over the async API
- WebSocket transport
- full parity with every Codex CLI feature
- hidden dependence on the current experimental Codex Python SDK or a TypeScript helper server
- direct calls to undocumented upstream backend APIs
- default auto-approval of commands or file changes

If a feature is not available through the public app-server protocol, the SDK should expose that gap clearly instead of faking parity through side channels.

## Architectural Guidance

The expected layering is:

- transport: async subprocess management plus JSONL stdin/stdout IO
- rpc: JSON-RPC envelopes, request correlation, routing, cancellation, and server-initiated requests
- generated protocol models: stable schema artifacts first
- protocol adapters: typed event and notification helpers
- high-level client layer: ergonomic thread and turn workflows

Claude references may influence naming pressure for helpers like `query(...)`, streaming iteration style, and long-lived client ergonomics. They are not a license to copy Claude's transport architecture or session model wholesale.

## Stable Versus Experimental Surface

Stable protocol support should be the default. Experimental methods or fields must require explicit opt-in so callers can reason about compatibility and upgrade risk.

## Consequences

This ADR closes off several tempting shortcuts early:

- no `codex exec` fallback masquerading as the main implementation
- no secret blocking code on public async paths
- no broad manual duplication of protocol wire shapes
- no architectural copy-paste from the Claude SDK

Those constraints keep later implementation tasks aligned with the actual user goal: a native-async, app-server-based Python SDK that is explicit about protocol boundaries and safe to embed in larger async systems.

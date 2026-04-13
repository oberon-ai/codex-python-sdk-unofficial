# Ergonomics Mapping: Claude To Codex Terms

- Status: Draft for v1 implementation
- Date: 2026-04-13

## Purpose

This document maps the Claude Agent SDK ideas worth preserving into Codex-native public API terms.

The goal is not feature parity. The goal is to preserve the good async feel of Claude's Python SDK while keeping the public language, lifecycle, and protocol boundaries aligned with `codex app-server`.

## Shape Versus Semantics

Some Claude patterns are worth keeping at the API-shape level:

- a small `query()` helper for one-shot work
- a longer-lived async client for interactive workflows
- streaming iteration
- helpers for interruption and continuing prior work

Those familiar shapes must not hide different semantics:

- Codex state is a `thread`, not a `session`.
- Work happens in `turn`s, not in an open-ended message stream.
- Streaming units are `item` lifecycle updates, text deltas, command output deltas, token usage updates, and raw JSON-RPC notifications.
- Approvals are protocol-native server requests that require explicit decisions, not just a broad client-side permission mode toggle.

## Translation Table

| Claude concept | Codex-native equivalent |
| --- | --- |
| `query()` one-shot helper | `query()` stays public. `Adapt`: keep the easy async-generator entrypoint, but make it start a temporary Codex `thread`, run exactly one `turn`, stream `TurnEvent` values, and then close. It should feel familiar while still speaking Codex terms and lifecycle. |
| `ClaudeSDKClient` stateful client | `CodexSDKClient`. `Mirror`: keep the split between one-shot helper and long-lived client. `Adapt`: the stateful client owns one app-server connection and one current `thread` in v1, with explicit thread lifecycle instead of an implicit session stream. |
| `session` / `session_id` | `thread` / `thread_id`. `Adapt`: preserve the concept of resumable conversation state, but rename it to the Codex primitive and make it flow through `thread/start`, `thread/resume`, and `thread/fork` rather than through free-form message fields. |
| `client.query(prompt, session_id=...)` to continue a conversation | `client.query(prompt)` on an already-started or resumed `thread`. `Adapt`: continuing work is bound to the active `thread_id`, not to passing a session string on each message. If no thread is active, the SDK should raise a client-state error instead of inventing one. |
| `connect()` followed by an open interactive stream | `async with CodexSDKClient(...)` opens the app-server connection, but thread lifecycle stays explicit. `Adapt`: keep the pleasant async-context pattern, but do not treat connection state as equivalent to conversation state. |
| `receive_response()` | `receive_turn_events()` is the canonical name and `receive_response()` is only an alias. `Alias`: keep the Claude-flavored name for discoverability, but steer docs and examples toward the Codex-native event-stream name. |
| Claude message stream ending at `ResultMessage` | Turn-scoped `TurnEvent` stream ending at `TurnCompletedEvent`. `Adapt`: Codex should stream typed turn and item events rather than a generic message union, and completion should be defined by turn completion, not by a final result-message type. |
| Partial streaming as assistant message chunks | Partial streaming as typed deltas and lifecycle events. `Adapt`: expose `AgentTextDeltaEvent`, `ReasoningTextDeltaEvent`, `CommandOutputDeltaEvent`, `ItemStartedEvent`, `ItemCompletedEvent`, `TokenUsageUpdatedEvent`, plus raw passthrough wrappers. This is stronger than Claude's generic message stream because the app-server already sends turn/item structure. |
| Sending more input while a response is in progress | `turn/steer` surfaced as `CodexSDKClient.steer()` or `TurnHandle.steer()`. `Adapt`: keep the interactive feel, but make the operation explicit as steering an in-flight `turn` instead of treating it as just more messages on a session stream. |
| `interrupt()` in streaming mode | `interrupt()` still exists, but maps to `turn/interrupt` for a specific `turn_id` and `thread_id`. `Mirror`: keep the helper because users expect it. `Adapt`: the behavior is protocol-addressed and should complete with `TurnCompletedEvent(status=\"interrupted\")`. |
| Permission modes such as `default`, `acceptEdits`, or `bypassPermissions` | Approval requests and decisions. `Adapt`: Codex should expose `ApprovalRequestedEvent`, `ApprovalRequest`, and `ApprovalDecision` rather than copying Claude's broad permission-mode vocabulary. Safe defaults matter more than matching Claude's modes. |
| `can_use_tool`-style permission callback | Async approval handler callback. `Adapt`: keep the callback convenience, but scope it to concrete server-initiated approval requests for command execution, file changes, or explicit permission requests. The callback returns an approval decision, not a generic yes-or-no tool policy. |
| Resume a prior session | `resume_thread(thread_id)`. `Mirror`: keep the resumability expectation. `Adapt`: Codex is stronger here because resume is already a first-class app-server method instead of an SDK convention layered over local transcript handling. |
| Fork a prior session | `fork_thread(thread_id | None)`. `Mirror`: keep the branching workflow. `Adapt`: Codex is stronger here because `thread/fork` is a protocol-native operation with server-defined behavior for mid-turn forks and `forkedFromId`. |
| Session files on disk as part of the public mental model | Stored threads managed by app-server. `Omit`: do not make local transcript files or Claude-style session-file workflows part of the Codex SDK's public story. Resume and inspection should follow thread APIs, not filesystem conventions. |
| One-shot helper accepting open-ended `AsyncIterable` prompts for continued control | One-shot `query()` remains single-turn and unidirectional. `Omit`: do not copy Claude's streaming-input interpretation into the convenience helper. Interactive back-and-forth belongs on `CodexSDKClient` through explicit new turns or `steer()`. |
| Session-centric documentation language | Thread/turn/item language everywhere. `Omit`: even when helper names stay familiar, public docs should never explain the SDK in `session` terms because that would hide the real protocol model. |

## Where Codex Is Stronger Than The Claude Analogy

The Codex SDK should lean into protocol-native strengths instead of pretending the protocol is just another session stream:

- Threads are first-class server objects with explicit start, resume, fork, list, read, archive, and status-change APIs.
- Turns are first-class server objects with explicit start, steer, interrupt, and completion semantics.
- Item lifecycle notifications already describe agent messages, reasoning, commands, file edits, and tool calls without the SDK inventing its own synthetic event model.
- Approval flows are first-class server-initiated JSON-RPC requests tied to `threadId`, `turnId`, and `itemId`, so the SDK can expose approvals as typed protocol events instead of a vague permission abstraction.
- Stable-versus-experimental negotiation already exists in `initialize`, so the SDK can keep a clean boundary instead of exposing mixed-stability helpers by default.

## Deliberate Incompatibilities

These Claude behaviors should not be copied directly:

1. Do not expose `session` or `session_id` in the public API. Codex already has `thread_id`, and the SDK should teach that primitive instead of aliasing it everywhere.
2. Do not copy Claude's permission-mode vocabulary as the main approval model. Codex approvals are specific request-and-decision exchanges, and v1 must default to visible blocking rather than hidden auto-approval.
3. Do not let the long-lived client silently create a thread inside `client.query()`. One-shot `query()` may hide thread creation, but the stateful client should require explicit `start_thread()`, `resume_thread()`, or `fork_thread()`.
4. Do not turn the one-shot helper into an open-ended streaming-input control channel. Codex already has better primitives: a new turn for the next request, or `turn/steer` for in-flight guidance.
5. Do not treat local transcript files as the portability or resume mechanism. The public contract should depend on app-server thread APIs, not filesystem layout conventions.

## Working Rule For Later Implementation Tasks

When a Claude pattern is attractive, copy the smallest amount of shape needed for usability, then translate the semantics into Codex `thread`, `turn`, `item`, and approval-request language before it reaches the public API.

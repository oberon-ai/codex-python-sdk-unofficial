# API Overview

The SDK exposes four primary entry points:

- `query()`
  for one-shot, single-turn async workflows on an ephemeral thread.
- `AppServerClient`
  for direct access to the app-server JSON-RPC surface.
- `CodexSDKClient`
  for long-lived async thread-oriented workflows.
- `SyncCodexSDKClient`
  for synchronous Python that needs a wrapper over `CodexSDKClient`.

The sync surface is intentionally a wrapper, not a second transport stack. It
uses a private background `asyncio` loop and yields the same typed turn-event
objects as the async APIs.

## Recommended Imports

Import from the root package for the normal user path:

```python
from codex_agent_sdk import (
    AppServerClient,
    AppServerConfig,
    CodexOptions,
    SyncCodexSDKClient,
    query,
)
```

Direct imports from `codex_agent_sdk.options`, `codex_agent_sdk.errors`, and
`codex_agent_sdk.retry` are also treated as stable support surfaces. Lower
layers such as `transport`, `rpc`, `protocol`, `generated`, and `testing`
remain importable but are not part of the curated happy path.

## `query()`

`query()` is the smallest public entry point.

It:

- creates a temporary app-server connection
- performs the required `initialize` handshake
- starts an ephemeral thread
- starts exactly one turn
- yields typed `TurnEvent` values until the turn completes
- closes the subprocess connection before returning

The helper accepts:

- a plain string prompt
- one structured input item
- a sequence of structured input items for multimodal or tool-oriented calls

Example:

```python
import asyncio

from codex_agent_sdk import AgentTextDeltaEvent, CodexOptions, TurnCompletedEvent, query


async def main() -> None:
    async for event in query(
        prompt="Summarize the important modules in this repository.",
        options=CodexOptions(cwd=".", approval_policy="never"),
    ):
        if isinstance(event, AgentTextDeltaEvent):
            print(event.text_delta, end="", flush=True)
        elif isinstance(event, TurnCompletedEvent):
            print(f"\nstatus: {event.turn_status}")


asyncio.run(main())
```

## `AppServerClient`

`AppServerClient` is the low-level native-async client for
`codex app-server --listen stdio://`.

Its stable responsibilities include:

- automatic `initialize` then `initialized` handshake
- raw JSON-RPC `request()` and `notify()`
- typed helpers for `thread/start`, `thread/resume`, `thread/fork`, `thread/list`,
  `thread/read`, `thread/archive`, `thread/unarchive`, and `thread/name/set`
- typed helpers for `turn/start`, `turn/steer`, and `turn/interrupt`
- notification and server-request subscriptions
- approval helpers and approval callback installation
- `wait_for_turn_completed()` for terminal turn status plus latest token usage

Use it when you need manual thread management, request subscriptions, or direct
access to the wire protocol surface.

## `CodexSDKClient`

`CodexSDKClient` is the stateful async client for long-lived thread workflows.
It tracks:

- `thread_id`
- `active_turn_id`
- `thread_status`

Its main responsibilities are:

- `start_thread()`, `resume_thread()`, and `fork_thread()`
- `query()` returning a streamed `TurnHandle`
- `steer()` and `interrupt()`
- `receive_turn_events()` and `receive_response()`
- `respond_approval_request()` for manual approval flows

`query()` starts a new thread automatically on first use when no active thread
exists yet. Explicit thread lifecycle methods are still the better fit when you
need to choose between start, resume, or fork yourself.

The returned `TurnHandle` preserves the streamed event surface while also
adding:

- `wait()`
- `steer()`
- `interrupt()`

Those helpers are all async because the client itself is native `asyncio`.

## `SyncCodexSDKClient`

`SyncCodexSDKClient` wraps `CodexSDKClient` for synchronous Python. It mirrors
the same high-level methods:

- `start_thread()`
- `resume_thread()`
- `fork_thread()`
- `query()`
- `steer()`
- `interrupt()`
- `receive_turn_events()`
- `respond_approval_request()`

The sync client is practical when your application cannot expose an async API,
but it is still a wrapper over the async implementation. That means:

- prefer `query()`, `CodexSDKClient`, or `AppServerClient` when your host
  application is already async
- inline approval responses are less natural on the sync path because the raw
  streamed approval events still carry async helpers; use a sync
  `approval_handler` callback when possible, or call
  `SyncCodexSDKClient.respond_approval_request(...)` yourself

Example:

```python
from codex_agent_sdk import AgentTextDeltaEvent, CodexOptions, SyncCodexSDKClient


with SyncCodexSDKClient(
    options=CodexOptions(
        cwd=".",
        approval_policy="never",
        model="gpt-5.4",
    )
) as client:
    turn = client.query("Summarize the important modules in this repository.")
    for event in turn:
        if isinstance(event, AgentTextDeltaEvent):
            print(event.text_delta, end="", flush=True)

    result = turn.wait()
    print(result.status)
```

## Streaming Events

The `query()` helper and the low-level turn-stream helpers surface a typed
`TurnEvent` union. The main event types are:

- `TurnStartedEvent`
- `TurnCompletedEvent`
- `AgentTextDeltaEvent`
- `ReasoningTextDeltaEvent`
- `CommandOutputDeltaEvent`
- `ItemStartedEvent`
- `ItemCompletedEvent`
- `ApprovalRequestedEvent`
- `ThreadStatusChangedEvent`
- `TokenUsageUpdatedEvent`
- `RawNotificationEvent`
- `RawServerRequestEvent`

Use the typed events when possible, and fall back to the raw passthrough events
when you want access to envelopes the higher-level adapters do not normalize.

## Approvals

Approval flows are modeled explicitly:

- `ApprovalRequest` is the common typed base class.
- `CommandApprovalRequest`, `FileChangeApprovalRequest`, and
  `PermissionsApprovalRequest` carry normalized request details.
- `ApprovalDecision` renders the response payload expected by the app-server.

You can handle approvals in two ways:

- consume `ApprovalRequestedEvent` values and respond manually
- install an async `approval_handler` callback on `query()` or
  `AppServerClient`

Example callback:

```python
from codex_agent_sdk import ApprovalDecision, ApprovalRequest


async def auto_decline_commands(request: ApprovalRequest) -> ApprovalDecision | None:
    if request.kind == "command_execution":
        return ApprovalDecision.decline()
    return None
```

Returning `None` leaves the request unhandled so your code can process it from
the normal event or server-request stream.

## Results And Helpers

The package also exposes a few convenience abstractions:

- `TurnCompletion`
  wraps the terminal `turn/completed` notification and latest token usage seen
  on the stream.
- `TurnOutputAggregator`
  assembles assistant text, reasoning text, command output, and item-level
  summaries from streamed events.
- `TurnResult` and `TurnItemAggregation`
  capture the compact end-of-turn summary built by the aggregator.
- `retry_on_overload()`
  offers opt-in retry logic for retryable overload responses.

## Errors

Public exception types live in `codex_agent_sdk.errors` and are re-exported
from the root package. A few important ones:

- `StartupError` and `StartupTimeoutError`
- `TransportClosedError` and `TransportWriteError`
- `JsonRpcError` and its standard subclasses
- `RetryableOverloadError` and `RetryBudgetExceededError`
- `ApprovalError` subclasses for approval callback and lifecycle failures

See the module docstrings and type hints for the complete hierarchy.

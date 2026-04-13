# ADR 0002: Concurrency And State Model

- Status: Accepted
- Date: 2026-04-13

## Context

The SDK needs a clear concurrency model before transport or client runtime code exists.

Without a written ownership model, async agent clients usually fail in the same places:

- request ids are allocated in one place and resolved in another without a clear owner
- `turn/start` races with the first streamed notifications
- approval requests arrive while the client is between internal states
- cancellation of one caller accidentally tears down the whole connection
- event queues become ambiguous about whether they belong to the connection, the thread, or the caller

The app-server protocol already gives strong lifecycle primitives:

- one transport connection with a required `initialize` then `initialized` handshake
- thread lifecycle through `thread/start`, `thread/resume`, and `thread/fork`
- turn lifecycle through `turn/start`, `turn/steer`, `turn/interrupt`, and `turn/completed`
- server-initiated approval requests scoped by `threadId`, `turnId`, and often `itemId`

This ADR fixes how the handwritten runtime should map those protocol primitives onto native `asyncio` tasks, queues, futures, and locks.

## Decision Summary

The runtime will use a layered ownership model:

- `StdioTransport` owns the subprocess and raw stdio streams.
- `AppServerClient` owns the connection state, request-id correlation, and background reader and dispatcher tasks.
- `CodexSDKClient` owns one current high-level thread and at most one high-level active turn in v1.
- `ActiveTurnContext` owns the per-turn event queue and turn-scoped completion futures.
- `TurnHandle` borrows the `ActiveTurnContext`; it does not own queue lifetime.

The v1 concurrency boundary is explicit:

- one `AppServerClient` may have many in-flight JSON-RPC request ids
- one `CodexSDKClient` may have only one active high-level turn at a time
- one active high-level turn has one single-consumer event queue

This keeps the public API deterministic while still leaving the low-level RPC layer capable of normal JSON-RPC concurrency.

## Ownership Model

| Layer | Owns | Does not own |
| --- | --- | --- |
| `StdioTransport` | `asyncio` subprocess handle, stdin writer, stdout stream, stderr drain buffer, transport close sequence | request ids, thread selection, typed events |
| `AppServerClient` | connection lifecycle state, inbound reader task, dispatcher task, pending request futures, pending server-request contexts, notification subscribers, raw protocol errors | high-level thread convenience state, per-turn public iterator semantics |
| `CodexSDKClient` | current `thread_id`, mirrored `thread_status`, one high-level `ActiveTurnContext`, approval callback policy, stateful client invariants | subprocess bytes, raw JSON parsing, generic request routing |
| `ActiveTurnContext` | bound `turn_id`, per-turn event queue, terminal result future, approval/server-request map for that turn, single-consumer claim | process lifetime, unrelated turns, connection lifecycle |
| `TurnHandle` | ergonomic view over one `ActiveTurnContext`, `wait()`, `steer()`, `interrupt()` helpers | queue creation, queue shutdown, routing tables |

This answers the primary ownership questions directly:

- The subprocess is owned by `StdioTransport`.
- The connection is owned by `AppServerClient`.
- The turn queue is owned by `ActiveTurnContext`, which is created and destroyed by `CodexSDKClient`.

## Low-Level Connection Lifecycle

### State table

| Connection state | Meaning | Allowed operations | Exit conditions |
| --- | --- | --- | --- |
| `created` | Client object exists but no subprocess has started and no tasks are running. | `connect()` or explicit low-level `initialize()` bootstrap; idempotent `close()` | `connecting`, `closed` |
| `connecting` | Subprocess has been spawned or is being spawned; stdout reader, dispatcher, and stderr drain are starting; `initialize` request is in flight. | Wait for handshake; `close()` | `initialized`, `failed`, `closing` |
| `initialized` | `initialize` response has been received and `initialized` notification has been sent; normal RPC traffic is allowed. | All stable RPC methods; experimental methods only when opted in; `close()` | `closing`, `failed` |
| `closing` | Shutdown has started; no new outgoing requests are accepted; background tasks are being cancelled or drained. | Await shutdown completion; repeated `close()` is idempotent | `closed` |
| `closed` | Transport is gone and background tasks are finished. Terminal state. | Idempotent `close()` only | none |
| `failed` | A fatal startup, transport, decode, or protocol error occurred. The root error is preserved and pending operations fail fast. | Read failure information; `close()` | `closing`, `closed` |

### Transition sketch

```text
created -> connecting -> initialized -> closing -> closed
                  \-> failed -> closing -> closed
initialized ------^
created -------------------------------> closed
```

### Connection rules

1. The connection performs the handshake exactly once per subprocess:
   - send `initialize`
   - wait for response
   - send `initialized`
   - only then allow general RPC methods
2. `initialize` failure is connection failure, not a recoverable partial state.
3. Once `closing` begins, new request attempts fail with a client-state error instead of racing the shutdown path.
4. `failed` is observable so callers can distinguish a transport or protocol fault from a normal close.
5. A runtime failure eventually transitions through `closing` to `closed`, but the captured failure cause remains attached to the raised exception and any failed futures.

## Task Model And Mutable-State Ownership

The runtime should use three long-lived low-level task roles and keep their responsibilities narrow.

### 1. Stdout reader task

The reader task is the sole consumer of subprocess stdout.

Responsibilities:

- read newline-delimited JSON messages from stdout
- decode each line into one JSON-RPC envelope
- push decoded envelopes into a connection-scoped inbound queue
- treat EOF, decode failure, or transport read failure as connection failure

Non-responsibilities:

- no request correlation
- no typed event adaptation
- no approval callback execution

### 2. Dispatcher task

The dispatcher task is the single writer for connection-owned mutable routing state.

Responsibilities:

- consume inbound envelopes in arrival order
- resolve or fail `pending_requests[id]`
- update mirrored thread snapshots and active-turn bindings
- adapt known notifications into typed `TurnEvent` values
- route turn-scoped events into the current `ActiveTurnContext`
- register and clear pending server requests
- emit raw passthrough events when the typed layer does not cover a method

The dispatcher must not block on user code. If an approval callback exists, the dispatcher creates a separate `asyncio.Task` to run it and returns to routing.

### 3. Stderr drain task

The stderr drain task continuously reads subprocess stderr into a bounded in-memory buffer.

Responsibilities:

- preserve recent stderr lines for startup and shutdown failures
- avoid deadlocking the subprocess on a full stderr pipe

The buffer is diagnostic state, not part of the public event stream.

### Outbound writes

Outbound requests and notifications do not need a dedicated writer task in v1. They should be serialized with an `asyncio.Lock` on the transport stdin writer.

The request path should:

1. allocate the JSON-RPC request id
2. create and register the pending future
3. write the message while holding the write lock
4. await the future resolved by the dispatcher

The future must exist before bytes are written so a fast response cannot outrun correlation setup.

## High-Level `CodexSDKClient` State Model

The public state model is intentionally smaller than the full protocol surface. It compresses connection state, current-thread selection, active-turn activity, and approval blocking into states users can reason about.

`CodexSDKClient` should expose these high-level states:

| Client state | Meaning | Invariants | Entered by | Exits on |
| --- | --- | --- | --- | --- |
| `no_thread` | Connection is usable but no current thread is selected. | `thread_id is None`; `active_turn_id is None` | client enters context; current thread cleared after fatal thread loss | `start_thread()`, `resume_thread()`, `fork_thread()`, `close()` |
| `thread_loaded` | A current thread is selected and no high-level turn is active. | `thread_id` is set; no active turn context | successful thread start/resume/fork; prior turn completed | `query()`, thread switch, `close()` |
| `turn_active` | A high-level turn has been started and its queue is open. | exactly one `ActiveTurnContext`; `active_turn_id` may be provisional until bound | successful `query()` | approval request arrives, `interrupt()`, `turn/completed`, `close()` |
| `awaiting_approval` | The active turn is blocked on one or more unresolved server requests. | `turn_active` plus pending server-request map is non-empty | approval request or other turn-scoped server request arrives | response sent or `serverRequest/resolved`; `turn/completed`; `close()` |
| `interrupted` | Interrupt has been requested for the active turn and final completion is pending. | active turn still exists until `turn/completed(status="interrupted")` arrives | successful `interrupt()` request | `turn/completed`, failure, `close()` |
| `finished` | Client shutdown is in progress or complete. | no new high-level operations allowed | `close()` or context-manager exit | terminal |

Two important clarifications:

1. `awaiting_approval` and `interrupted` are activity states layered on top of an active turn, not separate thread identities.
2. `client.thread_status` should still mirror the server `ThreadStatus` union exactly. The server's `thread_status` can be `notLoaded`, `idle`, `systemError`, or `active(activeFlags)`, while the high-level client state answers a different question: "what can this client do right now?"

### Transition sketch

```text
no_thread -> thread_loaded -> turn_active -> thread_loaded
                             -> awaiting_approval -> turn_active
                             -> interrupted -> thread_loaded

no_thread ----\
thread_loaded -+-> finished
turn_active ---/
awaiting_approval -/
interrupted ------/
```

### Public-method rules

- `start_thread()`, `resume_thread()`, and `fork_thread()` require the connection to be `initialized`.
- `query()` requires `thread_loaded` and fails fast in `no_thread`.
- `query()` fails fast if the client is already in `turn_active`, `awaiting_approval`, or `interrupted`.
- `steer()` requires an active regular turn and should pass `expected_turn_id` by default.
- `interrupt()` changes high-level state to `interrupted` only after the `turn/interrupt` request itself is accepted. The turn is not finished until `turn/completed(status="interrupted")` arrives.

## One Active High-Level Turn Per `CodexSDKClient` In V1

This boundary is deliberate.

In v1, `CodexSDKClient` supports exactly one active high-level turn at a time.

That means:

- one public `TurnHandle`
- one `ActiveTurnContext`
- one turn-scoped event queue
- one turn-scoped approval map
- one default `receive_turn_events()` target when `turn_id` is omitted

This is not a limitation of JSON-RPC itself. The low-level connection must still support:

- multiple concurrent request ids
- notifications for any subscribed thread
- approval responses and control requests while a regular request is awaiting completion

The v1 restriction exists at the ergonomic client layer so routing stays deterministic and public queue ownership remains obvious.

Notifications for turns that do not match the active high-level turn should stay available through raw low-level observers, but they must not silently create additional high-level turn queues.

## Turn Queue Lifecycle And Routing

### `ActiveTurnContext`

The high-level client should maintain one internal turn context with at least:

- `thread_id`
- provisional or bound `turn_id`
- `queue: asyncio.Queue[TurnEvent | _QueueSentinel]`
- `result_future: asyncio.Future[TurnResult]`
- `pending_server_requests: dict[request_id, ServerRequestContext]`
- `consumer_claimed: bool`

### Queue ownership and single-consumer rule

The queue belongs to `ActiveTurnContext`, not to whichever API surfaces it first.

The first of these APIs to attach to the queue claims the single-consumer stream:

- iterating the returned `TurnHandle`
- `receive_turn_events()`
- `receive_response()`

Subsequent attempts to attach another consumer to the same turn should fail with a specific client-state error instead of trying to fan out implicitly.

`TurnHandle.wait()` is not a second stream consumer. It awaits `result_future`.

### Avoiding the `turn/start` race

The first streamed notifications can arrive immediately after `turn/start` is accepted. To avoid dropping `turn/started` or early `item/*` notifications, queue registration happens before notification routing can observe the new turn.

The high-level algorithm is:

1. `CodexSDKClient.query()` creates a provisional `ActiveTurnContext` for the current `thread_id`.
2. That provisional context is registered with the dispatcher as the pending high-level turn for the thread before `turn/start` is written.
3. The dispatcher binds the provisional context to the real `turn_id` using whichever arrives first:
   - the `turn/start` response
   - the first turn-scoped notification carrying the same `threadId`
4. Once bound, all later turn notifications and approval requests route by `turn_id`.

Because v1 allows only one active high-level turn per client, provisional routing by current `thread_id` is deterministic.

### Notification routing rules

The dispatcher handles inbound protocol messages in this order:

1. **Responses**
   Resolve the matching request future by JSON-RPC id.

2. **Turn and thread notifications**
   Update mirrored `thread_status` or turn completion state first, then emit typed events into the active turn queue when the `(thread_id, turn_id)` matches the bound or provisional context.

3. **Server-initiated requests**
   Create a `ServerRequestContext`, attach it to the active turn when scoped by matching `threadId` and `turnId`, and then either:
   - enqueue a typed `ApprovalRequestedEvent` or `RawServerRequestEvent`, or
   - spawn an approval-callback task that will answer automatically

4. **Server-request resolution notifications**
   Remove the matching pending request from the turn context and clear the `awaiting_approval` activity state when the map becomes empty.

### Queue shutdown

The active turn queue closes when one of these happens:

- `turn/completed` is received for the active turn
- connection failure makes further events impossible
- client shutdown begins

Shutdown behavior:

- enqueue a terminal sentinel so the async iterator ends cleanly
- resolve or fail `result_future`
- remove the turn context from routing tables
- clear `active_turn_id`

## Approval And Server-Request Handling

Approval requests are first-class turn activity, not out-of-band callbacks glued onto command output.

Rules:

1. The dispatcher tracks pending server requests by request id.
2. A turn enters `awaiting_approval` when its pending server-request map becomes non-empty.
3. If an approval callback is configured, the dispatcher spawns a child task to run it and write the response.
4. If no callback is configured, the dispatcher enqueues a turn event exposing an idempotent `respond()` coroutine.
5. The turn leaves `awaiting_approval` only after the pending request is cleared by:
   - a client response that the server resolves
   - `serverRequest/resolved`
   - turn completion or interruption cleanup

The dispatcher must never await approval handler code inline. User code may be slow, cancelled, or buggy; the transport reader must continue running.

## Cancellation And Failure Boundaries

The runtime should treat cancellation boundaries explicitly.

### Cancellation of one request waiter

If a caller awaiting one request result is cancelled:

- the connection stays alive
- reader and dispatcher tasks stay alive
- the underlying pending request remains registered until a response or connection shutdown arrives

This avoids assuming the app-server can cancel arbitrary RPC requests just because a local task stopped waiting.

### Cancellation of turn event consumption

If a caller stops iterating a turn stream or the iterator task is cancelled:

- the turn itself is not auto-interrupted
- the queue and `result_future` remain owned by `ActiveTurnContext`
- the caller may still call `interrupt()` or `wait()`

The public API should make this explicit in docs. Stream cancellation is not the same as turn cancellation.

### Cancellation of `TurnHandle.wait()`

Cancelling `wait()` does not cancel the turn. It only cancels that waiter. Another task may await `wait()` again later.

### Client shutdown

When `close()` or async-context exit starts:

1. connection state moves to `closing`
2. no new high-level operations are accepted
3. active approval callback tasks are cancelled
4. reader, dispatcher, and stderr tasks are cancelled or drained
5. all pending request futures fail with a close-related error
6. the active turn queue receives a terminal sentinel and `result_future` fails unless a terminal turn result was already captured
7. transport pipes and subprocess are closed

Cleanup should be wrapped in `asyncio.shield(...)` where needed so caller cancellation does not strand a live subprocess.

### Connection failure

On fatal read, decode, or protocol failure:

- connection state becomes `failed`
- pending request futures fail with the same root exception
- the active turn queue receives a terminal error path
- subsequent public operations fail fast
- context-manager exit still runs normal close cleanup

## `asyncio`-Only Implementation Boundary

This design is intentionally implementable with standard `asyncio` primitives only:

- `asyncio.create_subprocess_exec`
- `asyncio.Task`
- `asyncio.Queue`
- `asyncio.Future`
- `asyncio.Event`
- `asyncio.Lock`
- `asyncio.shield`

No `asyncio.to_thread(...)`, worker threads, or alternate async runtimes are required for the core transport and routing model.

## Consequences For Later Tasks

This ADR gives later implementation work a few hard rules:

1. Keep `AppServerClient` capable of normal JSON-RPC concurrency even though `CodexSDKClient` exposes one active high-level turn.
2. Treat the dispatcher as the single writer for routing tables and mirrored thread or turn state.
3. Register the active turn context before `turn/start` can race with notifications.
4. Keep approval handling turn-scoped and visibly blocking when no callback is configured.
5. Preserve the single-consumer rule for high-level turn streams.
6. Surface cancellation semantics directly instead of pretending that dropping a coroutine also drops remote state.

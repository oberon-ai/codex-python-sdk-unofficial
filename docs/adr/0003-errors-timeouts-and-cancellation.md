# ADR 0003: Errors, Timeouts, And Cancellation

- Status: Accepted
- Date: 2026-04-13

## Context

The SDK is about to grow real transport and client code.

If error classes, timeout defaults, and cancellation semantics are left implicit, later
tasks will make incompatible choices in different layers:

- transport code will invent process and stderr failure shapes
- RPC code will collapse retryable overload into generic server failure
- high-level client helpers will guess at timeouts for turns and approvals
- cancellation will either leak live subprocesses or get hidden behind generic exceptions

This ADR fixes the handwritten policy before runtime code exists so later tasks can
implement transport, routing, and client helpers against one stable contract.

## Decision Summary

The SDK uses a narrow error hierarchy with these rules:

1. `asyncio.CancelledError` is never wrapped in SDK exceptions.
2. Retryable overload is a distinct `JsonRpcServerError` subclass.
3. Handshake failures stay visible as protocol-level errors, even when they happen during startup.
4. Startup and shutdown use finite local timeouts.
5. Request waits, turn waits, event streams, and approval waits default to no timeout and are controlled by the caller.
6. Transport and startup errors preserve recent subprocess stderr for debugging.
7. JSON-RPC errors preserve the raw code, message, and data payload.

## Exception Tree

The initial handwritten exception tree is:

```text
CodexError
├── ClientStateError
├── TransportError
│   ├── StartupError
│   │   ├── CodexNotFoundError
│   │   └── StartupTimeoutError
│   ├── ShutdownError
│   │   └── ShutdownTimeoutError
│   ├── ProcessExitError
│   ├── TransportClosedError
│   ├── TransportWriteError
│   └── MessageDecodeError
├── JsonRpcError
│   ├── JsonRpcParseError
│   ├── JsonRpcInvalidRequestError
│   ├── JsonRpcMethodNotFoundError
│   ├── JsonRpcInvalidParamsError
│   ├── JsonRpcInternalError
│   └── JsonRpcServerError
│       ├── RetryableOverloadError
│       │   └── RetryBudgetExceededError
│       ├── NotInitializedError
│       └── AlreadyInitializedError
├── ProtocolError
│   ├── HandshakeError
│   ├── ResponseValidationError
│   └── UnexpectedMessageError
├── CodexTimeoutError
│   └── RequestTimeoutError
└── ApprovalError
    ├── ApprovalCallbackError
    ├── InvalidApprovalDecisionError
    └── ApprovalRequestExpiredError
```

### Classification notes

- `StartupTimeoutError` subclasses both `StartupError` and `CodexTimeoutError`.
- `ShutdownTimeoutError` subclasses both `ShutdownError` and `CodexTimeoutError`.
- `NotInitializedError` and `AlreadyInitializedError` subclass both `HandshakeError`
  and `JsonRpcServerError`.
- `RetryBudgetExceededError` remains a retryable overload subclass because it still
  represents overload semantics, whether the budget was exhausted by the server or by
  the SDK's own opt-in retry helper.
- `asyncio.CancelledError` stays outside this tree on purpose.

## Error Mapping Rules

| Failure shape | Exception class | Notes |
| --- | --- | --- |
| Codex binary missing or configured path invalid | `CodexNotFoundError` | Startup failure before subprocess spawn |
| Process exits during spawn or handshake | `StartupError` or `ProcessExitError` | Preserve exit code and stderr tail |
| Spawn or initialize does not finish before the local startup deadline | `StartupTimeoutError` | Preserve stderr tail and timeout seconds |
| Stdout closes unexpectedly after initialization | `TransportClosedError` | Treat as fatal connection failure |
| JSONL line cannot be decoded as JSON | `MessageDecodeError` | Fatal connection failure; preserve offending line sample |
| Stdin write fails or is cancelled mid-frame | `TransportWriteError` | Fatal connection failure because JSONL framing may be broken |
| JSON-RPC standard error codes | Matching `JsonRpc*Error` subclass | Preserve `code`, `message`, `data`, method, and request id |
| JSON-RPC server overload (`-32001`, overload marker, or equivalent) | `RetryableOverloadError` | Distinct from fatal server errors |
| JSON-RPC retry-budget exhaustion text during overload, or local helper exhaustion after repeated overload | `RetryBudgetExceededError` | Chained from the last overload when the local helper runs out of attempts |
| Request before handshake | `NotInitializedError` | Protocol and handshake failure |
| Repeated `initialize` on same connection | `AlreadyInitializedError` | Protocol and handshake failure |
| Unexpected envelope ordering or impossible state transitions | `UnexpectedMessageError` | Local protocol bug or incompatible server behavior |
| Response body does not validate against generated models | `ResponseValidationError` | Local protocol bug or schema drift |
| Caller-provided per-request timeout elapses | `RequestTimeoutError` | Local timeout only; remote request may still finish |
| Approval callback crashes | `ApprovalCallbackError` | Do not auto-approve or auto-decline |
| Approval callback returns unsupported shape | `InvalidApprovalDecisionError` | Do not auto-approve or auto-decline |
| Caller responds after request was already cleared | `ApprovalRequestExpiredError` | Usually after `serverRequest/resolved` or turn cleanup |

## Timeout Policy

### Defaults

The SDK defines these default local timeouts:

| Operation class | Default | Control surface | Reasoning |
| --- | --- | --- | --- |
| Subprocess startup plus required `initialize` -> `initialized` handshake | `20.0` seconds | `AppServerConfig.startup_timeout` | Startup is a local control-plane action and should fail if the process hangs |
| Graceful shutdown and subprocess cleanup | `5.0` seconds | `AppServerConfig.shutdown_timeout` | Close should not leave background processes lingering indefinitely |
| Low-level JSON-RPC request wait | no default timeout | caller-provided `timeout=` later, or external `asyncio.timeout(...)` | RPC methods vary widely in duration; the SDK should not guess |
| High-level turn completion (`TurnHandle.wait()`) | no default timeout | caller-controlled | Turns can be long-running or intentionally blocked on approvals |
| Turn event streaming | no default timeout | caller-controlled | Lack of new events is not itself an error |
| Approval-request wait | no default timeout | caller-controlled | Safe default is visible blocking until a user or policy decides |
| Overload retry | off by default | explicit helper or retry wrapper | Hidden retries would change semantics and mask latency; callers must choose where replay is safe |

### Timeout principles

1. Timeouts are local SDK deadlines, not remote cancellation.
2. A timeout exception does not imply the server abandoned the operation.
3. The SDK only sets defaults where hanging almost always means local process trouble:
   startup and shutdown.
4. Everything tied to model execution, human approval, or long-running work waits
   indefinitely unless the caller opts into a deadline.
5. Explicit connection close releases pending request waiters immediately instead of
   leaving them blocked behind transport shutdown.

## Cancellation Semantics

`asyncio.CancelledError` propagates directly. The SDK must not convert it into
`CodexError`.

### Active writes

Cancellation is split into two cases:

1. If the task is cancelled before any bytes are handed to the transport, the SDK:
   - unregisters the pending request state
   - propagates `CancelledError`
   - leaves the connection usable
2. If cancellation happens after a JSONL frame may have been partially written or while
   `drain()` is in progress, the SDK:
   - propagates `CancelledError` to that caller
   - fails the connection with `TransportWriteError`
   - closes the subprocess because newline-delimited framing can no longer be trusted

This keeps local cancellation honest about the transport boundary instead of pretending
that a partial write is harmless.

### Active request waits

If a caller cancels while awaiting a request result after the request has been written:

- the local waiter gets `CancelledError`
- the connection remains alive
- the pending request is released locally immediately so cancelled callers do not linger
  in correlation state
- a later server response is treated as a late response, logged, and discarded instead
  of failing the connection

If the connection closes or fails before the response arrives:

- every pending request waiter is released immediately
- explicit caller-driven close maps to `TransportClosedError`
- unexpected EOF after startup also maps to `TransportClosedError`
- raw notification and server-request iterators stop on explicit close and raise the
  terminal connection error on failure

### Turn event consumption

If a turn event iterator is cancelled or abandoned:

- the consuming task gets `CancelledError`
- the turn is not auto-interrupted
- the active turn context keeps its queue and completion future
- `interrupt()` and `wait()` remain valid
- SDK-internal helper tasks created to wait on queue data versus close signals are
  cancelled immediately so abandoned stream consumers do not leak background tasks

Because v1 uses a single-consumer event queue, callers should not assume they can
abandon one stream consumer and attach a second one later.

### `TurnHandle.wait()`

Cancelling `wait()` only cancels that waiter. It does not interrupt the turn and does
not fail the connection.

### Shutdown

Shutdown implementation should shield its cleanup path where necessary so caller
cancellation does not strand a live subprocess or leave pending requests unresolved.

## Approval Error Policy

Approval handling follows these rules:

1. No approval callback means no exception. The request stays visible in the turn event
   stream and the turn remains blocked.
2. If an approval callback raises, surface `ApprovalCallbackError` and keep the request
   unresolved rather than auto-declining.
3. If a callback returns a decision shape the SDK cannot encode, raise
   `InvalidApprovalDecisionError` and keep the request unresolved.
4. If the caller responds after `serverRequest/resolved` or other lifecycle cleanup has
   already cleared the request, raise `ApprovalRequestExpiredError`.

The core rule is that approval bugs in client code must be visible. They must not turn
into hidden approvals or hidden denials.

## Debugging Preservation

The SDK should preserve diagnostic context instead of flattening everything into a
string:

- transport and startup errors keep a bounded stderr tail
- process-exit errors keep the exit code
- JSON-RPC errors keep `code`, `message`, and `data`
- request-scoped failures should also keep the method name and request id when known
- local wrappers should chain the original cause with `raise ... from exc`

This matters especially for:

- startup failures caused by bad environment or binary resolution
- protocol drift where raw JSON-RPC payloads explain the mismatch
- overload handling where retry helpers need to distinguish retryable vs fatal errors

## Consequences For Later Tasks

Later transport and client work should follow these implementation constraints:

1. Keep `CancelledError` visible to callers and do not wrap it.
2. Release cancelled request waiters immediately and treat later responses as benign
   late responses rather than fatal correlation errors.
3. Fail the whole connection on mid-frame write cancellation or write corruption risk.
4. Keep startup and shutdown deadlines in config, but leave turn and approval waits
   timeout-free by default.
5. Map overload into a dedicated retryable exception class and keep a small helper like
   `retry_on_overload(...)` opt-in rather than automatic.
6. Preserve stderr and raw JSON-RPC payloads on exceptions so debugging remains possible
   without rerunning under a debugger.
7. Restrict overload replay to startup or read-only flows unless the caller has an
   explicit idempotency guarantee for side-effecting operations.

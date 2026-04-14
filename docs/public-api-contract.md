# Public API Contract

- Status: Draft for v1 implementation
- Date: 2026-04-13

## Purpose

This document fixes the intended handwritten public surface before transport and client code exist.

The goal is to keep the SDK small, predictable, and explicitly Codex-native while still feeling familiar to users who liked the Claude Agent SDK ergonomics.

## Design Rules

The public API contract follows these rules:

1. Native async only. Public IO is `async` end to end.
2. Codex terminology first. The API says `thread`, `turn`, `item`, and approval request / decision.
3. Small surface. There is one easy one-shot path and one explicit stateful path.
4. Typed where it helps, raw where the protocol is broader. Common turn events get typed adapters, while raw notifications and raw server requests stay available.
5. Stable by default. Experimental protocol support must be explicitly enabled during initialization.
6. No hidden approval bypass. If the caller does not provide an approval callback, approval requests are surfaced and the turn visibly waits for a decision.
7. Thread lifecycle is explicit in the stateful client. One-shot helpers may hide thread creation, but long-lived clients should not.
8. `output_schema` is turn-scoped, not a sticky default, because the app-server protocol applies it only to the current `turn/start`.

## Top-Level Public Names

These names should be importable from `codex_agent_sdk`:

| Name | Kind | Purpose |
| --- | --- | --- |
| `CodexOptions` | dataclass | High-level defaults for thread and turn behavior such as `model`, `cwd`, `approval_policy`, `sandbox_policy`, `effort`, and related stable options. |
| `AppServerConfig` | dataclass | Process and protocol bootstrap options such as `codex_bin`, environment, startup timeouts, client identity, `experimental_api=False`, and opt-in debug logging hooks. |
| `AppServerClient` | class | Low-level typed JSON-RPC client for `codex app-server --listen stdio://`. |
| `CodexSDKClient` | class | High-level stateful client for thread workflows and streamed turn events. |
| `TurnHandle` | class | Handle for one in-flight or completed turn, including event iteration and lifecycle helpers. |
| `TurnResult` | dataclass | Final summarized result for a turn. |
| `OverloadRetryPolicy` | dataclass | Opt-in backoff policy for replaying overload-safe operations. |
| `TurnEvent` | type alias | Union of typed high-level events plus raw passthrough wrappers. |
| `ApprovalRequest` | dataclass | Typed approval request surfaced from server-initiated JSON-RPC requests. |
| `ApprovalDecision` | type alias / dataclass union | Structured approval response values sent back to the app-server. |
| `retry_on_overload()` | async helper | Retry a caller-supplied async operation after transient overload using exponential backoff and jitter. |
| `query()` | async generator function | One-shot convenience helper that creates a temporary client, runs exactly one turn, streams events, and closes. |

The event classes below should also be public because they are part of the typed streaming contract:

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

## Public Shape

### `CodexOptions`

`CodexOptions` is the high-level configuration object used by `query()` and `CodexSDKClient`.

It should contain stable, user-meaningful defaults that map cleanly to thread-start and turn-start settings, for example:

- `model`
- `cwd`
- `approval_policy`
- `sandbox_policy`
- `approvals_reviewer`
- `effort`
- `summary`
- `personality`

Contract notes:

- Field names should be Pythonic snake_case even if the wire protocol uses camelCase.
- Generated wire models should accept upstream camelCase keys on validation and
  emit upstream wire keys on default serialization so SDK internals do not need
  per-field alias glue.
- Fields that are experimental upstream should not appear unless `experimental_api=True` is enabled through `AppServerConfig`.
- `output_schema` should not be stored on `CodexOptions`. It belongs on a single turn request.

### `AppServerConfig`

`AppServerConfig` configures how the SDK launches and initializes `codex app-server`.

It should cover:

- `codex_bin`
- `extra_args`
- `cwd`
- `env`
- `startup_timeout`
- `shutdown_timeout`
- `client_name`
- `client_title`
- `client_version`
- `experimental_api`
- `opt_out_notification_methods`
- `debug_logging`
- `debug_logger`

Contract notes:

- `experimental_api` defaults to `False`.
- `opt_out_notification_methods` defaults to an empty tuple and maps to `initialize.params.capabilities.optOutNotificationMethods`.
- `debug_logging` defaults to `False`. When enabled, transport-layer diagnostics should stay redacted and truncated by default rather than dumping raw prompts, diffs, or environment values.
- `AppServerConfig.cwd` controls the app-server process working directory and is distinct from `CodexOptions.cwd`, which is a per-thread or per-turn workspace override.
- `startup_timeout` covers both subprocess launch and the initial `initialize` response wait. It is one startup budget, not two unrelated helper timeouts.
- The low-level client performs the required handshake automatically: send `initialize`, then send `initialized`, then allow other methods.
- Stderr from the subprocess is captured and surfaced on startup or shutdown failures instead of being swallowed.

### `OverloadRetryPolicy` and `retry_on_overload()`

The SDK should expose a small opt-in helper for transient app-server overload.

Contract notes:

- Retry remains off by default. Callers must opt in explicitly.
- The helper retries only `RetryableOverloadError`, not arbitrary failures.
- The helper uses exponential backoff plus jitter and exposes the policy values as a dataclass.
- Exhausting the local retry budget raises `RetryBudgetExceededError` chained from the last overload response.
- Callers should use the helper for startup or read-only flows where replay is safe.
- Callers should not blindly use the helper for mutating methods, approval decisions, or other side-effecting requests unless they have an explicit idempotency story.
- Startup retry should create a fresh `AppServerClient` per attempt because a failed `initialize()` closes that connection.

## `query()`: One-Shot Helper

### Signature sketch

```python
async def query(
    *,
    prompt: str | list[InputItem],
    options: CodexOptions | None = None,
    app_server: AppServerConfig | None = None,
    output_schema: dict[str, object] | None = None,
    approval_handler: ApprovalHandler | None = None,
) -> AsyncIterator[TurnEvent]:
    ...
```

### Contract

`query()` is the smallest public entry point:

- it creates a temporary app-server connection
- it opens a fresh thread for the helper's own use
- it starts exactly one turn
- it yields `TurnEvent` values until the turn reaches a terminal state
- it closes the subprocess connection before returning

`query()` is for:

- one-off scripts
- CI and batch jobs
- simple integrations that only need one turn
- structured output extraction for a single request

`query()` is not for:

- thread resume
- thread fork
- same-turn steering
- interruption from unrelated tasks after control has been handed off elsewhere
- multiple turns on the same thread

### Input rules

- If `prompt` is a string, the helper converts it to a single text input item.
- If `prompt` is a list of `InputItem`, the helper sends them as-is so callers can pass images, skills, or app mentions explicitly.
- `output_schema` constrains only the current turn and is not persisted into future turns because `query()` has no future turns.

### Approval behavior

- If `approval_handler` is provided, the SDK uses it to answer approval requests automatically.
- If `approval_handler` is omitted, the event stream includes `ApprovalRequestedEvent`.
- `ApprovalRequestedEvent` must expose an async `respond(decision)` helper so callers can keep approval logic inline while iterating the stream.
- If the caller never responds, the turn remains blocked in a visible way.

## `CodexSDKClient`: Stateful Thread Client

### Signature sketch

```python
class CodexSDKClient:
    def __init__(
        self,
        options: CodexOptions | None = None,
        app_server: AppServerConfig | None = None,
        approval_handler: ApprovalHandler | None = None,
    ) -> None: ...
```

### Contract

`CodexSDKClient` is the explicit stateful API. It owns one connection to one app-server process and manages one current thread at a time in v1.

The client should support:

- start a new thread
- resume an existing thread by `thread_id`
- fork the current thread or a specified thread
- start one high-level active turn at a time
- steer a currently active regular turn
- interrupt a currently active turn
- stream typed events for the active turn
- expose raw notifications and raw server requests when the typed adapter layer does not cover a case yet

### Thread lifecycle methods

```python
async def start_thread(
    self,
    *,
    options: CodexOptions | None = None,
    ephemeral: bool = False,
) -> str: ...

async def resume_thread(
    self,
    thread_id: str,
    *,
    options: CodexOptions | None = None,
) -> str: ...

async def fork_thread(
    self,
    thread_id: str | None = None,
    *,
    options: CodexOptions | None = None,
    ephemeral: bool = False,
) -> str: ...
```

Contract notes:

- These methods return the active `thread_id` and also update `client.thread_id`.
- `fork_thread()` defaults to the current thread when `thread_id` is omitted.
- The stateful client should not silently auto-start a thread inside `query()`. Callers should either use `query()` for the hidden-thread convenience path or call `start_thread()` / `resume_thread()` / `fork_thread()` explicitly.

### Turn lifecycle methods

```python
async def query(
    self,
    prompt: str | list[InputItem],
    *,
    options: CodexOptions | None = None,
    output_schema: dict[str, object] | None = None,
) -> TurnHandle: ...

async def steer(
    self,
    prompt: str | list[InputItem],
    *,
    expected_turn_id: str | None = None,
) -> str: ...

async def interrupt(
    self,
    *,
    turn_id: str | None = None,
) -> None: ...
```

Contract notes:

- `query()` starts a new turn on the current thread and returns a `TurnHandle` immediately after the app-server acknowledges `turn/start`.
- `query()` requires an active thread. If there is no current thread, it raises a specific client-state error instead of auto-starting one.
- Only one high-level active turn may exist per `CodexSDKClient` in v1.
- `steer()` appends input to the currently active regular turn and returns the accepted `turn_id`.
- `interrupt()` requests cancellation and callers should wait for `TurnCompletedEvent(status="interrupted")` before treating the turn as finished.

### Event consumption methods

```python
def receive_turn_events(
    self,
    *,
    turn_id: str | None = None,
) -> AsyncIterator[TurnEvent]: ...

def receive_response(
    self,
    *,
    turn_id: str | None = None,
) -> AsyncIterator[TurnEvent]: ...
```

Contract notes:

- `receive_turn_events()` is the canonical public name.
- `receive_response()` exists as a compatibility and discoverability alias for users who arrive with Claude SDK expectations.
- Documentation should prefer `receive_turn_events()` in prose and examples unless the example is specifically demonstrating the alias.
- These methods default to the active turn when `turn_id` is omitted.
- A single turn event stream is single-consumer. Callers should choose one consumption path: `TurnHandle`, `receive_turn_events()`, or `receive_response()`.

### Client state properties

These properties should be public and cheap to inspect:

- `thread_id: str | None`
- `active_turn_id: str | None`
- `thread_status: ThreadStatus | None`

The client may also expose richer thread snapshots later, but those are not required for the initial public contract.

## `TurnHandle`

### Shape

```python
class TurnHandle(AsyncIterator[TurnEvent]):
    thread_id: str
    turn_id: str

    async def wait(self) -> TurnResult: ...
    async def steer(self, prompt: str | list[InputItem]) -> str: ...
    async def interrupt(self) -> None: ...
```

### Contract

`TurnHandle` is the per-turn convenience object returned from `CodexSDKClient.query()`.

It should:

- be directly async-iterable so `async for event in turn:` works
- expose `thread_id` and `turn_id` immediately
- provide `wait()` to return a final `TurnResult`
- proxy `steer()` and `interrupt()` to the parent client for that specific turn

This keeps the happy path small:

```python
turn = await client.query("Find the failing tests.")
async for event in turn:
    ...
result = await turn.wait()
```

## `TurnResult`

`TurnResult` is the compact terminal summary returned by `TurnHandle.wait()`.

It should contain:

- `thread_id`
- `turn_id`
- `status`
- `items`
- `token_usage`
- `error`
- `assistant_text`
- `structured_output`

Contract notes:

- `status` is terminal only: `completed`, `interrupted`, or `failed`.
- `structured_output` is populated only when `output_schema` was supplied and Codex returned a schema-conforming final assistant payload.
- `items` is the final authoritative item list for the turn, even though streaming item lifecycle is observed incrementally.

## `AppServerClient`: Low-Level Escape Hatch

### Signature sketch

```python
class AppServerClient:
    async def initialize(self) -> InitializeResult: ...
    @property
    def initialize_result(self) -> InitializeResult | None: ...
    @property
    def is_initialized(self) -> bool: ...
    async def request(
        self,
        method: str,
        params: object | None = None,
        *,
        response_model: type[ResponseT] | None = None,
        timeout: float | None = None,
    ) -> object | ResponseT: ...
    async def notify(self, method: str, params: object | None = None) -> None: ...
    def iter_notifications(self) -> AsyncIterator[JsonRpcNotification]: ...
    def subscribe_notifications(
        self,
        *,
        method: str | None = None,
        thread_id: str | None = None,
        turn_id: str | None = None,
        max_queue_size: int | None = None,
    ) -> JsonRpcNotificationSubscription: ...
    def subscribe_thread_notifications(
        self,
        thread_id: str,
        *,
        method: str | None = None,
        max_queue_size: int | None = None,
    ) -> JsonRpcNotificationSubscription: ...
    def subscribe_turn_notifications(
        self,
        turn_id: str,
        *,
        thread_id: str | None = None,
        method: str | None = None,
        max_queue_size: int | None = None,
    ) -> JsonRpcNotificationSubscription: ...
    def iter_server_requests(self) -> AsyncIterator[JsonRpcRequest]: ...
    async def respond_server_request(
        self,
        request_id: JsonRpcId,
        result: object | None = None,
    ) -> None: ...
    async def reject_server_request(
        self,
        request_id: JsonRpcId,
        code: int,
        message: str,
        *,
        data: object | None = None,
    ) -> None: ...
    def register_server_request_handler(
        self,
        method: str,
        handler: JsonRpcServerRequestHandler,
    ) -> None: ...
    def remove_server_request_handler(self, method: str) -> None: ...

    async def thread_start(
        self,
        *,
        cwd: str | None = None,
        model: str | None = None,
        ...
    ) -> ThreadStartResult: ...
    async def thread_resume(
        self,
        *,
        thread_id: str,
        cwd: str | None = None,
        model: str | None = None,
        ...
    ) -> ThreadResumeResult: ...
    async def thread_fork(
        self,
        *,
        thread_id: str,
        cwd: str | None = None,
        model: str | None = None,
        ...
    ) -> ThreadForkResult: ...
    async def thread_list(
        self,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        search_term: str | None = None,
        ...
    ) -> ThreadListResult: ...
    async def thread_read(
        self,
        *,
        thread_id: str,
        include_turns: bool | None = None,
    ) -> ThreadReadResult: ...
    async def turn_start(self, ...) -> TurnStartResult: ...
    async def turn_steer(self, ...) -> TurnSteerResult: ...
    async def turn_interrupt(self, ...) -> TurnInterruptResult: ...
```

### Contract

The low-level client exists so advanced callers can work directly against JSON-RPC while still benefiting from:

- native async subprocess transport
- request id correlation
- notification routing
- server-request handling
- initialization ordering
- subprocess lifecycle management

Low-level envelope iterators should yield typed envelope models rather than unstructured dicts. The envelope models normalize the internal `jsonrpc="2.0"` version while still respecting the Codex wire convention of omitting that field on the wire by default.

Design notes:

- `initialize()` performs the full required handshake and returns the initialize result after the `initialized` notification has already been sent.
- The initialize request is built from typed protocol models: `clientInfo` is always present, while `capabilities.experimentalApi` and `capabilities.optOutNotificationMethods` are included only when explicitly configured.
- `request("initialize", ...)` and `notify("initialized", ...)` are reserved handshake operations rather than general raw-method escape hatches.
- `initialize_result` exposes the cached typed handshake result without requiring a second initialize attempt.
- `request()` is the generic typed low-level helper: it accepts raw params or
  generated wire models, serializes Pydantic models with wire aliases, and
  optionally validates the result into the requested response model.
- If `response_model` is omitted, `request()` returns the raw decoded JSON-RPC
  result for escape-hatch use.
- Callers can also ask for `response_model=dict` when they explicitly want a raw
  dictionary while still going through the same helper.
- JSON-RPC error responses still flow through the normal exception hierarchy.
- `notify()` remains the raw low-level notification escape hatch.
- `iter_notifications()` is the catch-all convenience view over the same notification bus used by filtered subscriptions.
- `subscribe_notifications(...)` creates one bounded queue-backed subscription for all notifications or a filtered subset by `method`, `thread_id`, and `turn_id`.
- `subscribe_thread_notifications(...)` and `subscribe_turn_notifications(...)` are convenience wrappers intended for higher-level routing layers.
- `iter_server_requests()` remains the raw escape hatch for unhandled server-initiated requests.
- `respond_server_request(...)` and `reject_server_request(...)` send low-level JSON-RPC replies tied to a pending server request id.
- `register_server_request_handler(...)` lets callers auto-handle specific server-request methods without consuming the raw stream for those handled requests.
- `thread_fork(...)` is a thin typed branching helper over `thread/fork`; it does not invent a higher-level branch object or local lineage cache.
- `thread_list(...)` exposes server-native pagination and filtering directly. The low-level layer passes `cursor`, `limit`, and other filters through unchanged and returns the server's `next_cursor` rather than auto-paging.
- `thread_read(..., include_turns=True)` passes the history depth decision directly to the server instead of always hydrating turns.
- Notification subscriptions are independent. One slow or abandoned subscriber must not block other subscribers or the dispatcher task.
- Notification subscription queues are bounded by default. If a subscriber falls behind and its queue fills, that subscription closes with `NotificationSubscriptionOverflowError` after any already-queued notifications are drained.
- Unhandled server-request methods are surfaced to higher layers by default rather than rejected implicitly.
- If a registered server-request handler crashes, the client sends a JSON-RPC internal-error reply for that request instead of silently dropping it.
- `serverRequest/resolved` is the lifecycle signal that clears a pending server request locally, even when the request was already answered.
- `request(timeout=...)` is a local wait deadline only. A request timeout does not imply that the server abandoned the work, and it does not close the connection.
- Raw notification and server-request iterators wait indefinitely by default. Callers who want an idle deadline should wrap `anext(...)` or the async iterator in `asyncio.timeout(...)`.
- Explicitly closing a notification subscription unregisters it immediately and ends its iterator cleanly.
- Cancelling a blocked notification or server-request consumer must also clean up any SDK-internal helper tasks created to wait on queue data versus close signals.
- Explicit `close()` releases any pending request waiters with a connection-closed error and ends the raw inbound iterators cleanly.
- Unexpected EOF after startup is treated as connection failure. Pending request waiters receive `TransportClosedError`, and raw inbound iterators raise the same failure once queued items are drained.
- The typed helpers above should cover the stable app-server methods needed by the v1 high-level client.
- Experimental methods and fields remain unavailable unless `AppServerConfig(experimental_api=True)` was used.

## Event Model

`TurnEvent` is a tagged union of typed convenience adapters and raw passthrough wrappers.

### Typed events that should exist in v1

| Event | Carries |
| --- | --- |
| `TurnStartedEvent` | `thread_id`, `turn_id`, `turn_status` |
| `TurnCompletedEvent` | `thread_id`, `turn_id`, `turn_status`, `error`, `result` |
| `AgentTextDeltaEvent` | `thread_id`, `turn_id`, `item_id`, `text_delta` |
| `ReasoningTextDeltaEvent` | `thread_id`, `turn_id`, `item_id`, `text_delta` |
| `CommandOutputDeltaEvent` | `thread_id`, `turn_id`, `item_id`, `output_delta` |
| `ItemStartedEvent` | `thread_id`, `turn_id`, `item` |
| `ItemCompletedEvent` | `thread_id`, `turn_id`, `item` |
| `ApprovalRequestedEvent` | `thread_id`, `turn_id`, `item_id`, `request`, `respond()` |
| `ThreadStatusChangedEvent` | `thread_id`, `thread_status` |
| `TokenUsageUpdatedEvent` | `thread_id`, `turn_id`, `token_usage` |
| `RawNotificationEvent` | raw JSON-RPC notification envelope |
| `RawServerRequestEvent` | raw server-initiated JSON-RPC request envelope |

### Event rules

- Every typed event should carry `thread_id` when the protocol provides it.
- Turn-scoped events should carry both `thread_id` and `turn_id`.
- Item-scoped events should also carry `item_id`.
- Raw wrappers should expose the untouched wire payload so experimental or not-yet-adapted notifications are still usable.

## Codex Terminology And Status Vocabulary

The public API should keep Codex concepts and normalize them into Python naming.

### Identifiers

| Public name | Meaning |
| --- | --- |
| `thread_id` | Identifier for a Codex thread. |
| `turn_id` | Identifier for one turn inside a thread. |
| `item_id` | Identifier for a streamed thread item. |

### Status names

Expose snake_case public status values even if the wire payload uses camelCase or tagged unions.

| Public status family | Public values | Upstream shape |
| --- | --- | --- |
| `thread_status` | `not_loaded`, `idle`, `active`, `system_error` | thread status tagged union |
| `turn_status` | `in_progress`, `completed`, `interrupted`, `failed` | `turn.status` |
| `item_status` | `in_progress`, `completed`, `failed`, `declined` | item-specific status fields |

Rules:

- Use `thread_status`, `turn_status`, and `item_status` in public docs and type names.
- Preserve raw upstream values inside raw protocol wrappers for callers that need exact wire fidelity.
- Do not reintroduce Claude `session` terminology anywhere in the public surface.

## Reusable Example Section

The examples in this section are intentionally small and should be kept stable so later docs and tests can reuse them verbatim.

### One-shot query

```python
from codex_agent_sdk import CodexOptions, query

async for event in query(
    prompt="Audit this repo and summarize the highest-risk issues.",
    options=CodexOptions(model="gpt-5.4", cwd="."),
):
    print(event)
```

### Stateful thread workflow

```python
from codex_agent_sdk import CodexOptions, CodexSDKClient

async with CodexSDKClient(CodexOptions(cwd=".")) as client:
    await client.start_thread()

    turn = await client.query("Find the failing tests.")
    await client.steer("Focus on the smallest reproducible unit test.")
    await client.interrupt()

    async for event in turn:
        print(event)

    thread_id = client.thread_id
    forked_thread_id = await client.fork_thread()
```

### Low-level app-server access

```python
from codex_agent_sdk import AppServerClient, AppServerConfig

async with AppServerClient(AppServerConfig(codex_bin="codex")) as rpc:
    await rpc.initialize()
    thread = await rpc.thread_start(ephemeral=True)
    resumed = await rpc.thread_resume(thread_id=thread.thread.id, cwd="/repo")
    raw_turn = await rpc.request("turn/start", {"threadId": thread.thread.id}, response_model=dict)
```

## Explicit Non-Goals For The First Public Surface

The initial contract intentionally does not promise:

- sync wrappers
- websocket transport
- multiple high-level active turns per `CodexSDKClient`
- full coverage for every current or future app-server notification in typed form
- automatic thread creation inside the stateful client's `query()`
- hidden auto-approval behavior

## Mapping To Later Implementation Tasks

This contract maps cleanly onto later work:

- transport work implements `AppServerConfig` and `AppServerClient`
- routing work implements notification and server-request dispatch into typed `TurnEvent` adapters
- high-level client work implements `CodexSDKClient`, `TurnHandle`, and `TurnResult`
- approval work implements `ApprovalRequest`, `ApprovalDecision`, and `ApprovalRequestedEvent.respond()`
- examples and test fixtures can reuse the exact snippets in `Reusable Example Section`

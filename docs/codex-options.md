# CodexOptions

`CodexOptions` is the high-level defaults object for the public SDK entry
points:

- `query(..., options=...)`
- `CodexSDKClient(options=...)`
- `CodexSDKClient.start_thread(..., options=...)`
- `CodexSDKClient.resume_thread(..., options=...)`
- `CodexSDKClient.fork_thread(..., options=...)`
- `CodexSDKClient.query(..., options=...)`

It is deliberately separate from `AppServerConfig`.

## What belongs where

Use `CodexOptions` for sticky Codex behavior defaults:

- model selection
- working directory inside the thread/turn
- approval policy and reviewer
- reasoning effort and summary
- personality
- service tier
- thread instructions
- sandbox defaults

Use `AppServerConfig` for app-server process and connection bootstrap:

- `codex_bin`
- `extra_args`
- subprocess `cwd`
- subprocess `env`
- startup and shutdown timeouts
- `experimental_api`
- `opt_out_notification_methods`
- debug logging

Keep `output_schema` off `CodexOptions`.

- `output_schema` applies only to the current `turn/start`
- it should stay a direct argument to `query()` / `CodexSDKClient.query()`
- it is not part of sticky default merging

## Field shape

`CodexOptions` intentionally stays smaller than the raw wire schema.

Promoted high-level fields:

- shared sticky defaults:
  - `model`
  - `cwd`
  - `approval_policy`
  - `approvals_reviewer`
  - `personality`
  - `service_tier`
- thread-focused defaults:
  - `base_instructions`
  - `developer_instructions`
  - `sandbox_mode`
- turn-focused defaults:
  - `effort`
  - `summary`
  - `sandbox_policy`

Deliberately excluded from `CodexOptions`:

- `output_schema`
- `env`
- `experimental_api`
- `opt_out_notification_methods`
- low-level transport knobs
- every raw `thread/start` and `turn/start` protocol field

## Normalization

The constructor accepts Python-friendly strings and mappings where that improves
ergonomics, but it stores normalized generated stable types internally.

Examples:

- `approval_policy="on-request"` becomes generated `AskForApproval`
- `summary="concise"` becomes generated `ReasoningSummary`
- `sandbox_mode="workspace-write"` becomes generated `SandboxMode`
- `sandbox_policy={"type": "workspaceWrite", ...}` becomes generated
  `SandboxPolicy`

That keeps later client code typed without forcing callers to build generated
models by hand.

## Precedence

The intended merge order is:

1. client defaults
2. thread defaults
3. per-turn overrides

The rule is simple:

- later non-`None` values win
- `None` means "do not override"
- `output_schema` is not merged because it is current-turn-only

The implementation exposes that rule directly as:

```python
effective = CodexOptions.merge(client_defaults, thread_defaults, turn_overrides)
```

There is also a convenience instance method:

```python
effective = client_defaults.merged_with(thread_defaults)
```

## Mapping onto protocol calls

The protocol does not use exactly the same field set on every method, so
`CodexOptions` exposes small projection helpers instead of encouraging callers
to manually assemble dicts.

Available helpers:

- `to_thread_start_kwargs(...)`
- `to_thread_resume_kwargs()`
- `to_thread_fork_kwargs(...)`
- `to_turn_start_kwargs()`

These helpers intentionally preserve protocol differences.

Examples:

- `thread/fork` currently does not accept `personality`, so
  `to_thread_fork_kwargs(...)` omits it
- `turn/start` accepts `sandbox_policy`, while thread lifecycle calls use the
  coarser `sandbox` enum

## Sandbox layering

The upstream stable protocol splits sandbox defaults across two shapes:

- thread lifecycle calls use coarse `sandbox`
- `turn/start` uses richer `sandboxPolicy`

`CodexOptions` keeps both:

- `sandbox_mode`
- `sandbox_policy`

The projection helpers bridge the gap where possible:

- if only `sandbox_policy` is set and it has a coarse built-in mode such as
  `readOnly` or `workspaceWrite`, thread helpers derive `sandbox`
- if only `sandbox_mode` is set, turn helpers derive a simple matching
  `sandbox_policy`

This bridge is intentionally conservative. It avoids inventing richer thread
semantics that the protocol does not support.

## Examples

High-level defaults for a stateful client:

```python
from codex_agent_sdk import CodexOptions, CodexSDKClient

defaults = CodexOptions(
    model="gpt-5.4",
    cwd="/repo",
    approval_policy="on-request",
    sandbox_mode="workspace-write",
    sandbox_policy={"type": "workspaceWrite", "writableRoots": ["/repo"]},
    summary="concise",
    personality="pragmatic",
)

client = CodexSDKClient(options=defaults)
```

Thread-specific overlay:

```python
thread_defaults = CodexOptions(
    cwd="/repo/subdir",
    base_instructions="Stay focused on test failures.",
)

effective = CodexOptions.merge(defaults, thread_defaults)
thread_kwargs = effective.to_thread_start_kwargs(ephemeral=True)
```

Per-turn overrides with a current-turn-only schema:

```python
turn_overrides = CodexOptions(
    effort="high",
    summary="detailed",
)

effective = CodexOptions.merge(defaults, thread_defaults, turn_overrides)
turn_kwargs = effective.to_turn_start_kwargs()
output_schema = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}
```

Bootstrap concerns stay on `AppServerConfig`:

```python
from codex_agent_sdk import AppServerConfig

app_server = AppServerConfig(
    codex_bin="codex",
    env={"OPENAI_API_KEY": "..."},
    experimental_api=True,
    opt_out_notification_methods=("thread/started",),
)
```

## Notes for later tasks

- High-level `CodexSDKClient` code should use `CodexOptions.merge(...)` instead
  of re-implementing precedence rules ad hoc.
- High-level turn start code should keep `output_schema` separate from
  `CodexOptions`.
- New protocol fields should only be promoted into `CodexOptions` when they are
  stable, user-meaningful defaults rather than low-level wire details.

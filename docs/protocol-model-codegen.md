# Protocol Model Codegen

This repository now generates a first stable set of Pydantic v2 wire models
from the pinned vendored Codex app-server schema snapshot.

## Current Scope

The current codegen step is intentionally narrow:

- input:
  `tests/fixtures/schema_snapshots/stable/codex_app_server_protocol.v2.stable.schemas.json`
- output:
  `src/codex_agent_sdk/generated/stable.py`
  `src/codex_agent_sdk/generated/stable_notification_registry.py`
- generator:
  `datamodel-code-generator==0.56.0`

The generated module covers the major stable schema families needed by later
tasks:

- request params such as `InitializeParams`, `ThreadStartParams`, and
  `TurnStartParams`
- response payloads such as `ThreadStartResponse` and `TurnStartResponse`
- server notifications such as `ServerNotification`,
  `ThreadStartedNotification`, and `TurnStartedNotification`
- a generated stable notification registry that maps exact method names such as
  `thread/started` and `item/agentMessage/delta` to their generated payload
  models
- shared wire payloads such as `Thread`, `Turn`, `ThreadItem`, `UserInput`,
  and `AskForApproval`

## Field Naming And Aliases

Generated protocol models follow one fixed rule:

- Python attributes are snake_case.
- Validation accepts upstream wire keys such as `threadId`.
- Default serialization emits compact upstream wire keys and omits unset
  optionals.

The implementation uses generator-native snake_case conversion plus the shared
`codex_agent_sdk.protocol.pydantic.WireModel` / `WireRootModel` bases. A small
repo-side postprocess swaps generated `RootModel[...]` wrappers onto
`WireRootModel[...]`, because the upstream generator does not expose a direct
root-model base hook. That keeps the rule uniform across regenerated models
instead of hand-fixing aliases one field at a time, and it makes
`model_dump()` / `model_dump_json()` default to wire-ready payloads.

Example:

```python
from codex_agent_sdk.generated.stable import TurnStartParams

turn_start = TurnStartParams(
    thread_id="thread_123",
    input=[{"type": "text", "text": "Find the failing tests."}],
)

assert turn_start.thread_id == "thread_123"
assert turn_start.model_dump() == {
    "threadId": "thread_123",
    "input": [{"type": "text", "text": "Find the failing tests."}],
}
assert turn_start.model_dump(by_alias=False) == {
    "thread_id": "thread_123",
    "input": [{"type": "text", "text": "Find the failing tests."}],
}
```

## Rebuild And Drift Check

Install the maintainer codegen toolchain first:

```bash
python -m pip install -e . -r requirements/dev.txt -r requirements/codegen.txt
```

Regenerate the checked-in stable models:

```bash
python scripts/generate_protocol_models.py
```

That one command refreshes both:

- `src/codex_agent_sdk/generated/stable.py`
- `src/codex_agent_sdk/generated/stable_notification_registry.py`

Verify that the checked-in generated file is in sync:

```bash
python scripts/generate_protocol_models.py --check
```

The script fails fast if the installed `datamodel-code-generator` version does
not match the repo pin in `requirements/codegen.txt`.

## Why The Scope Stops At Stable Models

The repo's stable-by-default direction still applies here:

- codegen consumes the vendored `stable/` snapshot by default
- experimental schema handling stays an explicit follow-on task
- handwritten adapters still belong in `src/codex_agent_sdk/protocol/`

The stable schema snapshot does **not** currently expose a typed JSON-RPC
server-request union for approval or user-input requests. Those server-initiated
requests therefore remain routed as raw `JsonRpcRequest` envelopes in the `rpc/`
layer for now. The generated stable module still covers the schema-defined
request params, responses, notifications, and shared payload types that later
adapter layers need, while the separate generated notification registry gives
the handwritten `protocol.registries` layer an easy-to-refresh method-to-model
index without hand-maintaining wrapper-class names.

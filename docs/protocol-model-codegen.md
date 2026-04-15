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
  `src/codex_agent_sdk/generated/stable_server_request_registry.py`
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
- a generated stable server-request registry that maps exact interactive method
  names such as `item/commandExecution/requestApproval`,
  `item/tool/requestUserInput`, and `mcpServer/elicitation/request` to thin
  handwritten params models in `codex_agent_sdk.protocol.server_requests`
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

Sync the maintainer codegen toolchain first:

```bash
uv sync --group codegen
```

Regenerate the checked-in stable models:

```bash
uv run --group codegen python scripts/generate_protocol_models.py
```

That one command refreshes all of:

- `src/codex_agent_sdk/generated/stable.py`
- `src/codex_agent_sdk/generated/stable_notification_registry.py`
- `src/codex_agent_sdk/generated/stable_server_request_registry.py`

Verify that the checked-in generated file is in sync:

```bash
uv run --group codegen python scripts/generate_protocol_models.py --check
```

The script fails fast if the installed `datamodel-code-generator` version does
not match the locked repo version recorded in `uv.lock`.

The test suite also carries a regression layer that checks:

- the generated stable module header still matches the pinned stable schema
  hash, generator pin, and codegen flag fingerprint
- the checked-in stable notification and server-request registries still match
  the current renderer output exactly
- the repo still tracks both stable and experimental schema snapshots while
  defaulting code generation to the stable snapshot

That keeps ordinary `uv run pytest` runs useful even when the maintainer-only
codegen toolchain is not installed. In a maintainer environment with
`datamodel-code-generator` and `ruff` available, the regression suite also runs
`uv run --group codegen python scripts/generate_protocol_models.py --check`
directly.

## Intentional Snapshot Updates

When Codex actually changed upstream, use this workflow:

1. Refresh the vendored schema snapshots first.
   - Normal pin-preserving refresh:

   ```bash
   uv run python scripts/vendor_protocol_schema.py
   ```

   - Explicit pin bump when the Codex CLI version changed:

   ```bash
   uv run python scripts/vendor_protocol_schema.py --allow-version-change
   ```

2. Regenerate the checked-in Python artifacts:

   ```bash
   uv run --group codegen python scripts/generate_protocol_models.py
   ```

3. Re-run the no-write checks and the relevant tests:

   ```bash
   uv run python scripts/vendor_protocol_schema.py --check
   uv run --group codegen python scripts/generate_protocol_models.py --check
   uv run pytest tests/test_codegen_regressions.py -q
   ```

4. Review the diff intentionally.
   - The stable generated files should show updated provenance header lines when
     the stable schema hash, codegen pin, or renderer inputs changed.
   - Experimental-only schema changes should stay confined to the vendored
     experimental snapshot unless the stable snapshot changed too.

## Why The Scope Stops At Stable Models

The repo's stable-by-default direction still applies here:

- codegen consumes the vendored `stable/` snapshot by default
- experimental schema handling stays an explicit follow-on task
- handwritten adapters still belong in `src/codex_agent_sdk/protocol/`

The stable schema snapshot still does **not** expose a typed JSON-RPC
server-request union for approval, elicitation, or user-input methods. This
repo therefore keeps the split explicit:

- `stable.py` provides the schema-defined shared payload types the server
  request models can reuse
- `codex_agent_sdk.protocol.server_requests` provides a small handwritten params
  layer for the interactive server-request methods the upstream README
  documents
- `stable_server_request_registry.py` is a generated derived artifact that maps
  exact method names onto those handwritten params models

That keeps the raw `JsonRpcRequest` transport/RPC boundary intact while giving
`protocol.registries` a deterministic method-to-model index for typed parsing.

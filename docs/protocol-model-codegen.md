# Protocol Model Code Generation

The repository generates its typed protocol layer from vendored Codex app-server
schema snapshots.

## Current Scope

Stable code generation currently uses:

- input:
  `tests/fixtures/schema_snapshots/stable/codex_app_server_protocol.v2.stable.schemas.json`
- outputs:
  - `src/codex_agent_sdk/generated/stable.py`
  - `src/codex_agent_sdk/generated/stable_notification_registry.py`
  - `src/codex_agent_sdk/generated/stable_server_request_registry.py`
- generator:
  `datamodel-code-generator==0.56.0`

These generated files cover the major stable schema families used by the SDK:

- request params such as `InitializeParams`, `ThreadStartParams`, and
  `TurnStartParams`
- response payloads such as `ThreadStartResponse` and `TurnStartResponse`
- server notifications such as `thread/started`, `turn/started`, and
  `item/agentMessage/delta`
- shared wire payloads such as `Thread`, `Turn`, `ThreadItem`, `UserInput`,
  and `AskForApproval`

## Field Naming And Aliases

Generated protocol models follow one fixed rule:

- Python attributes are snake_case
- validation accepts upstream wire keys such as `threadId`
- default serialization emits compact upstream wire keys and omits unset
  optionals

The repository gets this behavior through generator-native snake_case
conversion plus shared Pydantic base classes under
`codex_agent_sdk.protocol.pydantic`.

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

## Regeneration And Drift Checks

Install the maintainer codegen tooling:

```bash
uv sync --group codegen
```

Regenerate the checked-in artifacts:

```bash
uv run --group codegen python scripts/generate_protocol_models.py
```

That refreshes all of:

- `src/codex_agent_sdk/generated/stable.py`
- `src/codex_agent_sdk/generated/stable_notification_registry.py`
- `src/codex_agent_sdk/generated/stable_server_request_registry.py`

Check for drift without rewriting files:

```bash
uv run --group codegen python scripts/generate_protocol_models.py --check
```

The script fails fast if the installed `datamodel-code-generator` version does
not match the version pinned in `uv.lock`.

## Intentional Snapshot Updates

When the upstream Codex schema changes, use this sequence:

1. Refresh the vendored schema snapshots.

   ```bash
   uv run python scripts/vendor_protocol_schema.py
   ```

   If the pinned Codex CLI version changes:

   ```bash
   uv run python scripts/vendor_protocol_schema.py --allow-version-change
   ```

2. Regenerate the checked-in Python artifacts.

   ```bash
   uv run --group codegen python scripts/generate_protocol_models.py
   ```

3. Re-run the no-write checks and regression tests.

   ```bash
   uv run python scripts/vendor_protocol_schema.py --check
   uv run --group codegen python scripts/generate_protocol_models.py --check
   uv run pytest tests/test_codegen_regressions.py -q
   ```

4. Review the diff intentionally.

## Why Generation Defaults To Stable

The repository tracks both stable and experimental schema snapshots, but the
generated Python layer defaults to the stable snapshot.

That keeps the public package conservative while still recording experimental
drift under `tests/fixtures/schema_snapshots/experimental/`.

Interactive server-request types are still represented by a hybrid approach:

- stable shared payloads come from `stable.py`
- handwritten request-param models live in
  `codex_agent_sdk.protocol.server_requests`
- the generated server-request registry maps method names onto those handwritten
  models

This keeps generated code and handwritten protocol adapters cleanly separated.

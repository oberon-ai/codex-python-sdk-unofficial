# Fake App-Server Scripts

Store deterministic JSONL scripts that the fake app-server harness can replay in
tests.

Naming convention: `NNN_scenario_name.script.jsonl`

Example: `100_turn_start_and_complete.script.jsonl`

## Runner

Launch the harness as a subprocess with:

```bash
python -m codex_agent_sdk.testing.fake_app_server \
  --script tests/fixtures/fake_server_scripts/100_turn_start_and_complete.script.jsonl
```

The harness reads client traffic from stdin and emits server traffic on stdout
using newline-delimited JSON.

## Action format

Each line is one JSON object with an `action` field. Blank lines are ignored.

- `expect_request`
  - Wait for a client JSON-RPC request.
  - Required fields: `method`
  - Optional fields: `params`, `request_id`, `save_as`, `timeout_ms`
- `expect_notification`
  - Wait for a client JSON-RPC notification.
  - Required fields: `method`
  - Optional fields: `params`, `save_as`, `timeout_ms`
- `send_response`
  - Send a JSON-RPC response for an earlier client request.
  - Optional fields: `request_ref`, `result`, `error`, `delay_ms`
  - Exactly one of `result` or `error` is required.
- `send_notification`
  - Emit a server notification.
  - Required fields: `method`
  - Optional fields: `params`, `delay_ms`
- `send_server_request`
  - Emit a server-initiated JSON-RPC request such as an approval request.
  - Required fields: `method`, `request_id`
  - Optional fields: `params`, `save_as`, `delay_ms`
- `expect_response`
  - Wait for a client response to an earlier server request.
  - Optional fields: `request_ref`, `result`, `error`, `save_as`, `timeout_ms`
  - `result` and `error` are optional matchers; if both are omitted the harness
    only validates that a response envelope arrives for the expected id.
- `sleep`
  - Pause replay for `duration_ms`.
- `emit_raw`
  - Write a raw stdout line exactly once. Use this for invalid JSON scenarios.
  - Required fields: `line`
  - Optional fields: `delay_ms`
- `close`
  - Close stdout and exit the harness process.
  - Optional fields: `delay_ms`, `exit_code`

## Matching rules

- `expect_*` actions use exact method matching.
- `params`, `result`, and `error` use recursive subset matching for JSON
  objects, so scripts can assert only the stable fields they care about.
- `send_response` defaults to the most recent `expect_request` if `request_ref`
  is omitted.
- `expect_response` defaults to the most recent `send_server_request` if
  `request_ref` is omitted.
- `save_as` stores an earlier envelope under an alias for later `request_ref`
  lookups.
- `send_server_request` also stores string request ids as aliases automatically,
  so later steps can use `"request_ref": "approval-1"`.

## Integration test guidance

- Keep scripts small and deterministic. Favor multiple short scripts over one
  giant scenario file.
- Put timing behavior in the script, not in ad hoc `asyncio.sleep(...)` calls in
  tests, so the scenario remains reviewable.
- Prefer `save_as` aliases when later lines need to answer a specific earlier
  request.
- Use `emit_raw` plus `close` for invalid-message and abrupt-EOF coverage.
- Put happy-path scenarios in fixture files and build one-off edge-case scripts
  with `FakeAppServerScript.from_actions(...)` inside the test when that is more
  readable.

# Test Fixtures And Snapshots

This tree stores small, deterministic protocol examples and golden recordings
that future tests can load without needing a live `codex app-server` process.

## Categories

- `jsonrpc/requests/`
  - Single client-to-server JSON-RPC request envelopes.
- `jsonrpc/responses/`
  - Single server-to-client JSON-RPC response envelopes that pair with request
    fixtures by numeric prefix.
- `jsonrpc/notifications/`
  - Single JSON-RPC notifications such as thread or turn lifecycle updates.
- `jsonrpc/server_requests/`
  - Single server-initiated JSON-RPC requests such as approval requests.
- `schema_snapshots/stable/`
  - Stable upstream schema snapshots used to detect drift in generated models or
    handwritten adapters.
- `schema_snapshots/experimental/`
  - Experimental upstream schema snapshots kept separate from stable fixtures so
    stable-by-default tests do not accidentally broaden their coverage.
- `fake_server_scripts/`
  - Deterministic JSONL scripts that a fake app-server can replay during tests.
- `golden_transcripts/turns/`
  - Expected turn-event transcripts in JSONL form.
- `golden_transcripts/approvals/`
  - Approval-focused transcripts in JSONL form.

## Naming rules

Use a zero-padded three-digit scenario prefix so related files sort together.
Keep the same prefix across request, response, script, and transcript files for
the same scenario.

- Request envelope: `001_initialize.request.json`
- Response envelope: `001_initialize.response.json`
- Notification envelope: `010_turn_started.notification.json`
- Server request envelope: `020_approval_required.server_request.json`
- Fake-server script: `100_turn_start_and_complete.script.jsonl`
- Turn transcript: `100_turn_start_and_complete.turn.jsonl`
- Approval transcript: `110_command_denied.approval.jsonl`

Each `.json` file should contain exactly one JSON-RPC envelope. Each `.jsonl`
file should contain one deterministic event or action per line in wire order.

## Fixture boundaries

Fixtures in this tree are curated test inputs or golden outputs. Machine-written
Python modules generated from schema snapshots belong under
`src/codex_agent_sdk/generated/`, not under `tests/fixtures/`.

Schema snapshots belong here because they are reviewable test inputs and drift
detectors, even when later tasks also generate Python code from them.

Integration recordings belong under `golden_transcripts/` only after they have
been sanitized, made deterministic, and reduced to the lines a test actually
asserts against. Raw scratch captures or ad hoc debugging dumps do not belong in
the committed fixture tree.

# Package Layout

This repository uses a layered package layout so the native-async transport,
JSON-RPC protocol handling, generated schema models, and public API stay
separate as the SDK grows.

## Layer map

- `src/codex_agent_sdk/transport/`
  - Owns subprocess lifecycle, stdio wiring, stderr capture, and JSONL IO.
- `src/codex_agent_sdk/rpc/`
  - Owns JSON-RPC envelopes, request correlation, connection state, and
    notification or server-request routing.
- `src/codex_agent_sdk/generated/`
  - Reserved for machine-generated protocol artifacts only. Handwritten edits
    do not belong here.
- `src/codex_agent_sdk/protocol/`
  - Owns handwritten registries and adapters that sit on top of generated wire
    models.
- `src/codex_agent_sdk/client.py`, `query.py`, `events.py`, `approvals.py`,
  and `results.py`
  - Reserved for the public SDK surface and other ergonomic helpers.
- `src/codex_agent_sdk/testing/`
  - Reserved for fake app-server harnesses and other SDK-specific test helpers.

## Repository support directories

- `tests/`
  - Unit and integration tests for handwritten and generated layers.
- `tests/fixtures/`
  - Curated JSON-RPC envelopes, schema snapshots, fake-server scripts, and
    golden transcripts used by deterministic protocol and client tests.
  - The vendored schema source of truth lives under
    `tests/fixtures/schema_snapshots/`, with hashes and version pin metadata in
    `tests/fixtures/schema_snapshots/vendor_manifest.json`.
- `examples/`
  - User-facing example programs and sample workflows.
- `scripts/`
  - Code generation, release, or maintenance scripts.

## Contributor rules

- Re-export public SDK names from `codex_agent_sdk/__init__.py` so users do not
  have to learn internal module paths for the happy path.
- Keep generated protocol code isolated under `src/codex_agent_sdk/generated/`.
- Put transport concerns in `transport/`, not in public API helpers.
- Put JSON-RPC request and routing concerns in `rpc/`, not in `client.py`.
- Keep the top-level public API thin and Codex-native.
- When in doubt, add a small handwritten adapter on top of generated models
  rather than copying the wire format into multiple modules.

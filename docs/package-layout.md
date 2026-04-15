# Package Layout

The repository uses a layered layout so the transport, JSON-RPC machinery,
generated wire models, handwritten protocol adapters, and public SDK surface
can evolve independently.

## Source Tree

- `src/codex_agent_sdk/transport/`
  owns subprocess lifecycle, stdio wiring, stderr capture, and newline-delimited
  JSON framing.
- `src/codex_meta_agent/`
  owns the repository-maintenance automation that tracks `openai/codex`,
  prepares branch-sync prompts, and emits release metadata on top of the SDK.
- `src/codex_agent_sdk/rpc/`
  owns JSON-RPC envelopes, request correlation, subscriptions, and server-request
  routing.
- `src/codex_agent_sdk/generated/`
  contains generated protocol artifacts only.
- `src/codex_agent_sdk/protocol/`
  contains handwritten adapters, registries, and helpers layered on top of the
  generated models.
- `src/codex_agent_sdk/client.py`, `sync_client.py`, `query.py`, `events.py`,
  `approvals.py`, `results.py`, `options.py`, `errors.py`, and `retry.py`
  make up the public SDK surface and stable support modules.
- `src/codex_agent_sdk/testing/`
  contains fake app-server helpers and other SDK-specific test support code.

## Repository Support Directories

- `tests/`
  contains unit and integration coverage for the handwritten and generated
  layers.
- `tests/fixtures/`
  contains reusable protocol fixtures, schema snapshots, fake server scripts,
  and golden transcripts.
- `examples/`
  contains runnable example programs for the public API.
- `scripts/`
  contains repository maintenance entry points.
- `.github/`
  contains the committed upstream tracking state plus the scheduled release and
  branch-sync workflow.
- `docs/`
  contains user-facing and maintainer-facing Markdown documentation.

## Fixture Tree

The repository relies on a deliberately structured `tests/fixtures/` tree:

- JSON-RPC requests, responses, notifications, and server requests live under
  `tests/fixtures/jsonrpc/`
- schema snapshots live under `tests/fixtures/schema_snapshots/`
- fake app-server recordings live under `tests/fixtures/fake_server_scripts/`
- golden transcripts for turn and approval flows live under
  `tests/fixtures/golden_transcripts/`

The schema snapshots are the canonical input to generated Python artifacts.
`tests/fixtures/schema_snapshots/vendor_manifest.json` records the current
stable and experimental schema snapshots, their hashes, and the pinned Codex
CLI version used to refresh them.

## Public Surface Versus Lower Layers

The public SDK surface is intentionally smaller than the full package tree.

- End-user code should usually import from `codex_agent_sdk`.
- `codex_agent_sdk.options`, `codex_agent_sdk.errors`, and
  `codex_agent_sdk.retry` are stable support modules.
- `transport`, `rpc`, `protocol`, `generated`, and `testing` stay importable
  for advanced work, but they are not the main user path.

See [public-import-policy.md](public-import-policy.md) for the detailed import
rules.

## Contributor Rules

- Keep generated code isolated under `src/codex_agent_sdk/generated/`.
- Keep repository automation under `src/codex_meta_agent/` instead of mixing it
  into the SDK runtime modules.
- Keep transport concerns in `transport/`, not in public convenience helpers.
- Keep JSON-RPC request and routing concerns in `rpc/`, not in `client.py`.
- Prefer thin handwritten adapters on top of generated models instead of
  copying wire shapes across multiple modules.
- Preserve the layered boundary between public SDK surface, protocol adapters,
  generated code, and testing helpers.

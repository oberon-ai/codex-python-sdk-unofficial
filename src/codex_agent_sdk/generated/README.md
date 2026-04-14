# Generated Protocol Artifacts

This package is reserved for code generated from the upstream Codex
app-server schema bundle.

Contributor rules:

- Do not hand-edit Python modules in this package.
- Keep handwritten helpers in sibling packages such as
  `codex_agent_sdk.protocol` or `codex_agent_sdk.rpc`.
- Prefer small generated surfaces and thin handwritten adapters rather than
  duplicating wire shapes manually across the codebase.
- Generated `BaseModel` classes should expose Pythonic snake_case attributes
  while keeping upstream wire keys as aliases.
- The shared `codex_agent_sdk.protocol.pydantic.WireModel` and
  `WireRootModel` bases keep validation and default serialization aligned with
  the wire protocol:
  construct with `thread_id=...`, accept `threadId` from wire payloads, and
  emit compact wire-ready payloads from `model_dump()` unless a caller opts out
  with `by_alias=False` or changes the dump defaults explicitly.
- Use the vendored canonical schema snapshots under
  `tests/fixtures/schema_snapshots/` as generation input rather than whatever
  happens to be on a developer machine.
- Check `tests/fixtures/schema_snapshots/vendor_manifest.json` for the current
  schema pin, hashes, and stable-versus-experimental split.
- Regenerate the checked-in stable models with
  `python scripts/generate_protocol_models.py`.
- That script refreshes all of:
  `codex_agent_sdk.generated.stable` and
  `codex_agent_sdk.generated.stable_notification_registry` and
  `codex_agent_sdk.generated.stable_server_request_registry`.
- Verify drift with
  `python scripts/generate_protocol_models.py --check`.
- Generated modules and registries carry deterministic provenance header
  comments so ordinary test runs can detect stale schema pins or codegen input
  changes without relying only on manual diff review.

Later code generation tasks can add machine-written modules here without
restructuring the rest of the SDK package.

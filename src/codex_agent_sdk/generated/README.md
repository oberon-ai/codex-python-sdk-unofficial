# Generated Protocol Artifacts

This package is reserved for code generated from the upstream Codex
app-server schema bundle.

Contributor rules:

- Do not hand-edit Python modules in this package.
- Keep handwritten helpers in sibling packages such as
  `codex_agent_sdk.protocol` or `codex_agent_sdk.rpc`.
- Prefer small generated surfaces and thin handwritten adapters rather than
  duplicating wire shapes manually across the codebase.
- Use the vendored canonical schema snapshots under
  `tests/fixtures/schema_snapshots/` as generation input rather than whatever
  happens to be on a developer machine.
- Check `tests/fixtures/schema_snapshots/vendor_manifest.json` for the current
  schema pin, hashes, and stable-versus-experimental split.
- Regenerate the checked-in stable models with
  `python scripts/generate_protocol_models.py`.
- Verify drift with
  `python scripts/generate_protocol_models.py --check`.

Later code generation tasks can add machine-written modules here without
restructuring the rest of the SDK package.

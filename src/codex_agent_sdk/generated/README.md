# Generated Protocol Artifacts

This package contains machine-generated code derived from the vendored Codex
app-server schema snapshots.

## Current Generated Modules

- `codex_agent_sdk.generated.stable`
- `codex_agent_sdk.generated.stable_notification_registry`
- `codex_agent_sdk.generated.stable_server_request_registry`

## Rules

- Do not hand-edit Python modules in this package.
- Keep handwritten helpers in sibling packages such as
  `codex_agent_sdk.protocol` or `codex_agent_sdk.rpc`.
- Use the vendored snapshots under `tests/fixtures/schema_snapshots/` as the
  generation input boundary.
- Check `tests/fixtures/schema_snapshots/vendor_manifest.json` for the current
  schema pin and snapshot hashes.

## Regeneration

Install the maintainer toolchain:

```bash
uv sync --group codegen
```

Regenerate the checked-in artifacts:

```bash
uv run --group codegen python scripts/generate_protocol_models.py
```

Verify that the generated files are up to date:

```bash
uv run --group codegen python scripts/generate_protocol_models.py --check
```

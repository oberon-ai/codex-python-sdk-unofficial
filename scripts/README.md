# Scripts

This directory contains the repository maintenance entry points used to keep
the vendored schema snapshots and generated protocol artifacts in sync.

The scheduled upstream branch and release tracker is not implemented as a file
in this directory. It lives under `src/codex_meta_agent/` and is documented in
`docs/upstream-tracking.md` because it is a first-class package that uses the
SDK itself.

## `vendor_protocol_schema.py`

Refreshes or verifies the checked-in stable and experimental schema snapshots
under `tests/fixtures/schema_snapshots/` and updates
`tests/fixtures/schema_snapshots/vendor_manifest.json`.

Common commands:

```bash
uv run python scripts/vendor_protocol_schema.py --check
uv run python scripts/vendor_protocol_schema.py
uv run python scripts/vendor_protocol_schema.py --allow-version-change
```

## `generate_protocol_models.py`

Regenerates or verifies the checked-in stable Pydantic protocol models and the
derived notification and server-request registries.

Outputs:

- `src/codex_agent_sdk/generated/stable.py`
- `src/codex_agent_sdk/generated/stable_notification_registry.py`
- `src/codex_agent_sdk/generated/stable_server_request_registry.py`

Common commands:

```bash
uv sync --group codegen
uv run --group codegen python scripts/generate_protocol_models.py
uv run --group codegen python scripts/generate_protocol_models.py --check
```

## Working Rules

- Do not hand-edit files under `src/codex_agent_sdk/generated/`.
- Refresh schema snapshots before regenerating Python artifacts when upstream
  protocol inputs change.
- Review generated diffs intentionally; these scripts are part of the
  compatibility boundary for the SDK.

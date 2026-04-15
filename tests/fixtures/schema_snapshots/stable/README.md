# Stable Schema Snapshots

This directory stores the canonical stable schema input for future generated
Python models.

Current filename:

- `codex_app_server_protocol.v2.stable.schemas.json`

Refresh command template:

```bash
codex app-server generate-json-schema --out <OUT_DIR>
```

Use `uv run python scripts/vendor_protocol_schema.py` from the repo root rather
than checking in the raw CLI output directly. The script rewrites the bundle
into a deterministic, sorted JSON form that is easier to diff in pull requests.

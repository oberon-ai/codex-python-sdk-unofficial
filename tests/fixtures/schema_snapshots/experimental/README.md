# Experimental Schema Snapshots

This directory stores the canonical experimental schema input for future
generated Python models. Keeping it separate prevents stable-by-default work
from silently depending on experimental fields or methods.

Current filename:

- `codex_app_server_protocol.v2.experimental.schemas.json`

Refresh command template:

```bash
codex app-server generate-json-schema --experimental --out <OUT_DIR>
```

Use `python scripts/vendor_protocol_schema.py` from the repo root rather than
checking in the raw CLI output directly. The script rewrites the bundle into a
deterministic, sorted JSON form that is easier to diff in pull requests.

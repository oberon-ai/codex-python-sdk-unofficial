# Scripts

This directory is reserved for repository maintenance scripts.

Expected future uses include:

- updating vendored or generated protocol artifacts
- verification helpers for schema drift
- release automation and packaging support

Current maintenance entrypoint:

- `vendor_protocol_schema.py`
  - Regenerates the pinned stable and experimental schema snapshots under
    `tests/fixtures/schema_snapshots/` and updates
    `tests/fixtures/schema_snapshots/vendor_manifest.json`.

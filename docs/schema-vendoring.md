# Schema Vendoring

The repository vendors the Codex app-server schema as checked-in canonical JSON
snapshots instead of treating a developer's local `codex` binary as the source
of truth.

## Source Of Truth

The canonical schema inputs are:

- `tests/fixtures/schema_snapshots/stable/codex_app_server_protocol.v2.stable.schemas.json`
- `tests/fixtures/schema_snapshots/experimental/codex_app_server_protocol.v2.experimental.schemas.json`

The coordinating metadata lives in:

- `tests/fixtures/schema_snapshots/vendor_manifest.json`

That manifest records:

- the pinned `codex-cli` version
- the upstream source path
- the stable and experimental generation commands
- per-artifact SHA-256 hashes
- the canonical JSON formatting policy used in this repository

## Why The Repo Vendors Snapshots

Checked-in snapshots make schema updates reviewable and reproducible.

Raw `codex app-server generate-json-schema` output is not a good long-term
artifact by itself because it depends on whichever local `codex` binary happens
to be installed and can produce noisier diffs than the repository wants to
review.

The local CLI is therefore a refresh mechanism, not the primary source of
truth for this repo.

## Stable Versus Experimental

The repository keeps two snapshot files on purpose:

- `stable/`
  generated with `codex app-server generate-json-schema`
- `experimental/`
  generated with `codex app-server generate-json-schema --experimental`

Generated Python models default to the stable snapshot. Experimental schema
tracking is kept visible, but it does not automatically widen the stable public
surface.

## Refresh Workflow

Verify the current checkout first:

```bash
uv run python scripts/vendor_protocol_schema.py --check
```

Refresh the snapshots while preserving the pinned Codex version:

```bash
uv run python scripts/vendor_protocol_schema.py
```

If you are intentionally changing the pinned Codex CLI version, opt in
explicitly:

```bash
uv run python scripts/vendor_protocol_schema.py --allow-version-change
```

The script fails fast on version mismatch unless `--allow-version-change` is
present.

## Diffability Policy

The vendored snapshots are rewritten into deterministic JSON before they are
checked in:

- UTF-8
- `sort_keys=True`
- two-space indentation
- trailing newline

This keeps schema diffs reviewable in pull requests.

## Relationship To Generated Python Code

Generated Python artifacts should always treat the vendored snapshots as the
input boundary:

- vendored JSON lives under `tests/fixtures/schema_snapshots/`
- generated Python modules live under `src/codex_agent_sdk/generated/`
- handwritten protocol adapters live under `src/codex_agent_sdk/protocol/`

The stable Pydantic code generation step is documented in
[protocol-model-codegen.md](protocol-model-codegen.md).

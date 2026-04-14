# Schema Vendoring

This repository vendors the Codex app-server schema as checked-in, canonical
JSON snapshots instead of treating a developer's local `codex` binary as the
source of truth.

## Source Of Truth

The schema source of truth for this repo is the pair of vendored files:

- `tests/fixtures/schema_snapshots/stable/codex_app_server_protocol.v2.stable.schemas.json`
- `tests/fixtures/schema_snapshots/experimental/codex_app_server_protocol.v2.experimental.schemas.json`

The coordinating metadata for those files lives in:

- `tests/fixtures/schema_snapshots/vendor_manifest.json`

That manifest records:

- the current pinned `codex-cli` version
- the upstream source path the schema family comes from
- the stable and experimental generation commands
- per-artifact SHA-256 hashes
- the canonical JSON formatting policy used in this repo

## Why This Repo Uses Vendored Snapshots

The SDK needs a reviewable, stable input for later generated protocol models.
Raw `codex app-server generate-json-schema` output is not a good checked-in
artifact by itself because:

- it depends on whichever `codex` binary happens to be installed locally
- the raw JSON object ordering is not byte-stable across runs, which makes diffs
  noisy
- the repo wants stable and experimental schema inputs split cleanly

This repo therefore uses the local `codex` binary only as a refresh mechanism.
The checked-in canonical JSON snapshots are the actual inputs that later code
generation tasks should consume.

## Upstream Reference Path

The schema family being vendored is the Codex app-server v2 bundle from:

- repository: `openai/codex`
- source path:
  `codex-rs/app-server-protocol/schema/json/codex_app_server_protocol.v2.schemas.json`

That upstream path is recorded for provenance. The repo's direct generation
inputs are still the vendored stable and experimental snapshots listed above.

## Stable Versus Experimental

The repo keeps two separate snapshot files on purpose:

- `stable/`
  - generated with `codex app-server generate-json-schema`
- `experimental/`
  - generated with `codex app-server generate-json-schema --experimental`

The future generated Python layer should default to the stable snapshot. Any
experimental model surface should be generated or exposed only through an
explicit opt-in path, mirroring `AppServerConfig(experimental_api=True)`.

## Refresh Workflow

The refresh entrypoint is:

```bash
python scripts/vendor_protocol_schema.py
```

Recommended maintenance flow:

1. Check `tests/fixtures/schema_snapshots/vendor_manifest.json` for the current
   pinned `codex-cli` version.
2. Install or point `--codex-bin` at that exact `codex` version.
3. Verify the current checkout first:

   ```bash
   python scripts/vendor_protocol_schema.py --check
   ```

4. If you are intentionally bumping the schema pin, rerun with explicit opt-in:

   ```bash
   python scripts/vendor_protocol_schema.py --allow-version-change
   ```

The script fails fast on version mismatch unless `--allow-version-change` is
present. That prevents accidental refreshes from an unpinned local binary.

## Diffability Policy

The vendored schema snapshots are rewritten into deterministic JSON before they
are checked in:

- UTF-8
- `sort_keys=True`
- two-space indentation
- trailing newline

This canonicalization is deliberate. The raw CLI output can vary in key order
between runs, but the canonicalized snapshots remain stable and reviewable in
pull requests.

## Relationship To Generated Python Code

Later code generation tasks should treat the vendored snapshots as the only
schema input and write machine-generated Python artifacts under
`src/codex_agent_sdk/generated/`.

Keep the boundary explicit:

- vendored JSON snapshots live under `tests/fixtures/schema_snapshots/`
- generated Python modules live under `src/codex_agent_sdk/generated/`
- handwritten adapters stay in `src/codex_agent_sdk/protocol/`

The current stable Pydantic codegen step is implemented by
`scripts/generate_protocol_models.py` and documented in
`docs/protocol-model-codegen.md`.

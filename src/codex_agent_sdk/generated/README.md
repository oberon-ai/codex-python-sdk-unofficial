# Generated Protocol Artifacts

This package is reserved for code generated from the upstream Codex
app-server schema bundle.

Contributor rules:

- Do not hand-edit Python modules in this package.
- Keep handwritten helpers in sibling packages such as
  `codex_agent_sdk.protocol` or `codex_agent_sdk.rpc`.
- Prefer small generated surfaces and thin handwritten adapters rather than
  duplicating wire shapes manually across the codebase.

Later code generation tasks can add machine-written modules here without
restructuring the rest of the SDK package.

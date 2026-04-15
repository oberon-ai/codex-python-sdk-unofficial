# Public Import Policy

This repository keeps a clear split between the stable import surface and the
lower-level implementation layers that support it.

## Root Package Is Authoritative

The primary import path for end users is the root package:

```python
from codex_agent_sdk import CodexOptions, AppServerClient, query
```

If a name is re-exported from `codex_agent_sdk`, that root import path is part
of the intended public API.

## Stable Support Modules

Three focused modules are also treated as stable support surfaces:

- `codex_agent_sdk.options`
  for configuration dataclasses and timeout helpers
- `codex_agent_sdk.errors`
  for the public exception hierarchy and JSON-RPC error classification helpers
- `codex_agent_sdk.retry`
  for opt-in overload retry helpers and policy dataclasses

Use these modules when you want narrower imports without pulling in the full
root barrel.

## Public Module Homes

The public abstractions live in these modules, even though most docs and
examples should still prefer root imports:

- `codex_agent_sdk.client`
- `codex_agent_sdk.query`
- `codex_agent_sdk.events`
- `codex_agent_sdk.approvals`
- `codex_agent_sdk.results`

These modules organize the public SDK surface. They are not meant to replace
the root import story for the common path.

## Non-Promoted Lower Layers

The modules below are intentionally importable for advanced or internal work,
but they are not part of the curated user-facing surface:

- `codex_agent_sdk.transport`
- `codex_agent_sdk.rpc`
- `codex_agent_sdk.protocol`
- `codex_agent_sdk.generated`
- `codex_agent_sdk.testing`

Rely on them only when you are deliberately working below the public API layer.

## Rule For Future Additions

When a new abstraction becomes public:

1. define it in the appropriate public-layer module
2. re-export it from `codex_agent_sdk`
3. document it in the relevant user-facing docs
4. add or update tests that lock down the root import surface

That keeps the supported entry points obvious without collapsing the internal
layer boundaries.

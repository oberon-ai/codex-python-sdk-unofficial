# Public Import Policy

This repository keeps a deliberate split between the public SDK surface and the
lower-level implementation layers that will power it.

## Root package is authoritative

The supported import path for end users is the root package:

```python
from codex_agent_sdk import CodexOptions, CodexSDKClient, query
```

If a name is re-exported from `codex_agent_sdk`, that root import path is part
of the intended public API contract.

## Stable support modules

Two focused modules are also treated as stable support surfaces:

- `codex_agent_sdk.options`
  - configuration dataclasses and timeout policy helpers
- `codex_agent_sdk.errors`
  - public exception types and JSON-RPC error classification helpers

These modules may be imported directly when a caller wants a narrower import or
needs type names without importing the full barrel.

## Public layer implementation homes

The modules below are where the public abstractions live, but docs and examples
should still prefer root imports:

- `codex_agent_sdk.client`
- `codex_agent_sdk.query`
- `codex_agent_sdk.events`
- `codex_agent_sdk.approvals`
- `codex_agent_sdk.results`

These modules exist to keep the public SDK organized. They should not become the
primary import story for users.

## Non-promoted lower layers

The modules below are intentionally importable for advanced or internal work,
but they are not part of the curated root surface and should not be treated as
stable user API unless a later task explicitly promotes them:

- `codex_agent_sdk.transport`
- `codex_agent_sdk.rpc`
- `codex_agent_sdk.protocol`
- `codex_agent_sdk.generated`
- `codex_agent_sdk.testing`

## Rule for future additions

When a new abstraction becomes public:

1. define it in the appropriate public-layer module
2. document it in the public API contract if the contract changes
3. re-export it from `codex_agent_sdk`
4. add or update tests that lock down the root import surface

That keeps the happy path visible from the top level without collapsing the
layered architecture underneath it.

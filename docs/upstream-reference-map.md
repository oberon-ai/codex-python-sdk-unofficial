# Upstream Reference Map

This project depends on two upstreams for different reasons:

- Codex upstreams define protocol truth, lifecycle rules, and code generation inputs.
- Claude upstreams are ergonomic pressure only. They can influence async UX and helper shape, but not transport architecture or public terminology.

When sources disagree, use this priority order:

1. Codex schema bundle
2. Codex app-server README
3. Codex app-server product docs
4. Codex Python SDK artifacts for implementation strategy and codegen mechanics
5. Claude SDK and docs for UX ideas only

## Read First

If you are new to the repo, read these in order before implementing transport or client code:

1. [`codex-rs/app-server/README.md`](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)
   Revisit `Protocol`, `Lifecycle Overview`, `Initialization`, `API Overview`, `Approvals`, and `Experimental API Opt-in`.
2. [`codex_app_server_protocol.v2.schemas.json`](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/json/codex_app_server_protocol.v2.schemas.json)
   Revisit `InitializeRequest`, `ThreadStartParams`, `ThreadResumeParams`, `ThreadForkParams`, `TurnStartParams`, `ServerNotification`, `ThreadStartedNotification`, `TurnStartedNotification`, `TurnCompletedNotification`, `ItemStartedNotification`, `ItemCompletedNotification`, and token-usage or reasoning delta notifications.
3. [`sdk/python/scripts/update_sdk_artifacts.py`](https://github.com/openai/codex/blob/main/sdk/python/scripts/update_sdk_artifacts.py)
   Revisit the schema normalization and generated-type pipeline before introducing handwritten wire-shape duplication.
4. Claude ergonomics references:
   [`src/claude_agent_sdk/query.py`](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/query.py),
   [`src/claude_agent_sdk/client.py`](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/client.py),
   and the Agent SDK docs on overview, sessions, streaming output, streaming modes, and permissions.

## Protocol Truth

These references answer "what does `codex app-server` actually speak?" They win over convenience code.

| Reference | Why it matters |
| --- | --- |
| [`openai/codex/codex-rs/app-server/README.md`](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md) | Primary lifecycle guide for the stdio JSON-RPC protocol, including the required `initialize` then `initialized` handshake, thread and turn flows, notification streams, approvals, overload behavior, and experimental opt-in rules. |
| [`developers.openai.com/codex/app-server`](https://developers.openai.com/codex/app-server) | Higher-level product documentation for the same app-server surface; useful for onboarding language, examples, and checking whether our public docs align with official terminology. |
| [`openai/codex/codex-rs/app-server-protocol/schema/json/codex_app_server_protocol.v2.schemas.json`](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/json/codex_app_server_protocol.v2.schemas.json) | Canonical machine-readable protocol surface for generated models, request and response payloads, notification unions, approval payloads, and stable versus experimental schema boundaries. |

Most important sections to revisit later:

- `README.md`: `Lifecycle Overview`, `Initialization`, `API Overview`, `Approvals`, `Experimental API Opt-in`
- Schema bundle: `InitializeRequest`, `Thread*`, `Turn*`, `ServerNotification`, `Item*`, reasoning and token usage notifications, approval request payloads

## Codegen References

These references show how upstream turns the schema into Python artifacts and where handwritten code should stop.

| Reference | Why it matters |
| --- | --- |
| [`openai/codex/sdk/python/scripts/update_sdk_artifacts.py`](https://github.com/openai/codex/blob/main/sdk/python/scripts/update_sdk_artifacts.py) | Best reference for how upstream normalizes the schema, stabilizes class names, and regenerates checked-in Python protocol models from the v2 schema bundle. |
| [`openai/codex/sdk/python/README.md`](https://github.com/openai/codex/blob/main/sdk/python/README.md) | Explains the current upstream Python package scope, generated-model story, packaging workflow, and compatibility targets so we can intentionally diverge where this repo's goals are stricter. |

Most important sections to revisit later:

- `update_sdk_artifacts.py`: schema bundle path handling, title normalization, generator flags, and checked-in artifact boundaries
- `sdk/python/README.md`: generated model description, packaging steps, compatibility notes

## Ergonomic References

These references are for API feel, async UX, and docs structure. They do not override Codex protocol truth.

| Reference | Why it matters |
| --- | --- |
| [`anthropics/claude-agent-sdk-python/README.md`](https://github.com/anthropics/claude-agent-sdk-python/blob/main/README.md) | Shows how Claude presents `query()` versus long-lived client usage, examples, permissions explanations, and the narrative split between simple scripts and stateful interactions. |
| [`anthropics/claude-agent-sdk-python/src/claude_agent_sdk/query.py`](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/query.py) | Useful model for a small, discoverable one-shot async entrypoint that streams results without forcing callers into a stateful client immediately. |
| [`anthropics/claude-agent-sdk-python/src/claude_agent_sdk/client.py`](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/client.py) | Useful for long-lived client ergonomics such as `query()`, streaming receive loops, interrupts, and mode changes inside one process. |
| [`code.claude.com/docs/en/agent-sdk/overview`](https://code.claude.com/docs/en/agent-sdk/overview) | Useful for how to frame the SDK for new users and how to separate the one-shot path from the stateful path in documentation. |
| [`code.claude.com/docs/en/agent-sdk/sessions`](https://code.claude.com/docs/en/agent-sdk/sessions) | Useful for session lifecycle concepts such as resume and fork workflows, even though this repo must expose Codex `thread` terminology instead. |
| [`code.claude.com/docs/en/agent-sdk/streaming-output`](https://code.claude.com/docs/en/agent-sdk/streaming-output) | Useful for documenting streamed event types and helping decide which events deserve first-class adapters versus raw passthrough. |
| [`code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode`](https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode) | Useful for drawing the line between one-shot `query()` behavior and an interactive client that can be steered or interrupted. |
| [`code.claude.com/docs/en/agent-sdk/permissions`](https://code.claude.com/docs/en/agent-sdk/permissions) | Useful for user-facing explanations of approval and permission flows, especially how to document manual handling versus callback-based decisions. |

Most important sections to revisit later:

- `query.py`: the public contract and examples for a small async iterator helper
- `client.py`: connect/query/receive/interrupt flow and when the stateful client becomes justified
- Claude docs: overview, sessions, streaming output, streaming mode split, permissions

## Anti-Pattern References

These references are worth reading specifically so we do not copy the wrong parts.

| Reference | What to learn without copying |
| --- | --- |
| [`openai/codex/sdk/python/src/codex_app_server/async_client.py`](https://github.com/openai/codex/blob/main/sdk/python/src/codex_app_server/async_client.py) | Upstream's current async wrapper is explicitly built around thread offloading and `asyncio.to_thread(...)`; this repo's ADR rejects that architecture for public IO paths. |
| [`openai/codex/sdk/python/src/codex_app_server/client.py`](https://github.com/openai/codex/blob/main/sdk/python/src/codex_app_server/client.py) | Useful for method inventory and approval callback shape, but it is sync-first and currently defaults `experimental_api` to `True`, both of which conflict with this repo's intended defaults. |
| [`openai/codex/codex-rs/app-server/README.md`](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md) | Read the websocket sections as a warning, not a roadmap: websocket transport is explicitly experimental and unsupported, so v1 should stay stdio-first. |
| [`anthropics/claude-agent-sdk-python/src/claude_agent_sdk/client.py`](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/client.py) | Good async UX reference, but it is organized around Claude `session` language and control protocol assumptions that must not leak into Codex-native thread and turn APIs. |
| [`code.claude.com/docs/en/agent-sdk/sessions`](https://code.claude.com/docs/en/agent-sdk/sessions) | Useful for resume and fork expectations, but a trap if copied literally because our public surface must say `thread`, `turn`, and `item`, not `session`. |

## Notes For Future Tasks

- Keep generated protocol artifacts isolated from handwritten transport, routing, and ergonomic client code.
- Default to stable protocol surface. Experimental methods and fields need explicit opt-in in both runtime config and generated artifacts.
- Approval handling is first-class. Re-read the app-server approval flow before implementing command execution, file change, or permission request behavior.
- Route streaming notifications from the schema, not from ad hoc string matching sprinkled across high-level code.
- Use Claude references to improve discoverability and examples, not to justify transport or naming drift.

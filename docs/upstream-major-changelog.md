# Upstream Major Changelog

Coverage: stable `openai/codex` releases from `rust-v0.92.0` on January 27,
2026 through `rust-v0.122.0` on April 20, 2026.

This is intentionally brief. It focuses on the shifts that materially changed
how Codex works as a product or platform, not every fix or UI polish item.

## Major Arcs

- `0.92.0` to `0.100.0`
  Codex grew from a thread/app-server oriented tool into a fuller agent
  runtime with smarter approvals, default plan mode, skills/apps surfaces,
  thread compaction, early memory plumbing, websocket transport recovery, and
  the first experimental `js_repl`.
- `0.102.0` to `0.107.0`
  The platform tightened permissions and network control, expanded multi-agent
  coordination, improved app-server metadata and thread APIs, and made voice,
  memory, and sub-agent workflows much more real.
- `0.110.0` to `0.117.0`
  Plugins became a first-class system, fast mode became standard, permission
  profiles and hooks matured, experimental code mode landed, and the app-server
  v2 surface became broad enough to justify the new Python SDK.
- `0.118.0` to `0.122.0`
  The emphasis shifted toward hardened sandboxing, richer marketplace/plugin
  management, stronger realtime/media flows, better remote/app-server
  ergonomics, and more polished planning plus side-conversation workflows.

## Release Highlights

- `0.92.0` on January 27, 2026
  Dynamic tools could be injected into v2 threads, thread list filtering and
  unarchive landed, MCP scopes moved into config, and multi-agent collaboration
  got safer guardrails.
- `0.93.0` on January 31, 2026
  Plan mode gained a dedicated streamed view, `/apps` arrived, smart approvals
  became default, and logging moved onto a stronger SQLite-backed footing.
- `0.94.0` on February 2, 2026
  Plan mode became enabled by default, the stable `personality` config landed,
  and skill loading from `.agents/skills` became part of the expected workflow.
- `0.95.0` on February 4, 2026
  Desktop launching from the CLI appeared on macOS, personal and remote skills
  expanded, `/plan` became easier to drive, and shell tools gained parallelism.
- `0.96.0` on February 4, 2026
  `thread/compact` landed in app-server v2 and websocket rate-limit signaling
  improved, which made long-running session management feel more explicit.
- `0.97.0` on February 5, 2026
  Session-scoped "allow and remember" approvals arrived, live skill reloads
  landed, and the first memory persistence plumbing showed up.
- `0.98.0` on February 5, 2026
  GPT-5.3-Codex was introduced and steer mode became stable and on by default.
- `0.99.0` on February 11, 2026
  Direct shell commands stopped interrupting active turns, app-server controls
  for active work expanded, and enterprise network/search restrictions got more
  explicit.
- `0.100.0` on February 12, 2026
  The experimental `js_repl` runtime landed, websocket transport returned in a
  more structured form, memory management commands expanded, and sandbox policy
  shapes became more expressive.
- `0.101.0` on February 12, 2026
  Mostly stabilization: model resolution and memory-pipeline behavior were
  tightened rather than broadened.
- `0.102.0` on February 17, 2026
  Permissions became more unified, structured network approvals appeared, and
  configurable multi-agent roles pushed agent orchestration further.
- `0.103.0` on February 17, 2026
  App listings became richer for clients, and commit attribution moved onto a
  Codex-managed hook.
- `0.104.0` on February 18, 2026
  Websocket proxy support improved, archived-thread notifications landed, and
  approval IDs became clearer for multi-step shell flows.
- `0.105.0` on February 25, 2026
  The TUI took a large usability step forward with syntax highlighting, theme
  selection, voice dictation, clearer multi-agent progress, stronger approval
  controls, and better thread APIs.
- `0.106.0` on February 26, 2026
  Install scripts were published for macOS and Linux, app-server v2 expanded
  thread and realtime support, `request_user_input` became available in Default
  mode, and memory behavior improved.
- `0.107.0` on March 2, 2026
  Thread forking into sub-agents landed, voice device selection improved,
  custom tools became more multimodal, and memory became explicitly
  configurable/resettable.
- `0.110.0` on March 5, 2026
  The plugin system arrived in earnest, fast/flex service tiers appeared, and
  multi-agent plus memory workflows became much more configurable. There were
  no stable upstream releases tagged `0.108.0` or `0.109.0`.
- `0.111.0` on March 5, 2026
  Fast mode became the default, plugin awareness was pushed into session start,
  app-server v2 exposed structured MCP elicitation, and image support broadened.
- `0.112.0` on March 8, 2026
  `@plugin` mentions landed, model-selection UX improved, and executable
  permission profiles merged more tightly into per-turn sandbox policy.
- `0.113.0` on March 10, 2026
  Runtime `request_permissions` arrived as a built-in tool, plugin marketplace
  handling expanded, and command execution gained streaming stdin/stdout/stderr
  plus PTY support.
- `0.114.0` on March 11, 2026
  Experimental code mode landed, the hooks engine appeared, realtime handoffs
  got better transcript continuity, and skills/apps/plugins became clearer in
  mention picking.
- `0.115.0` on March 16, 2026
  This is one of the biggest shifts in the range: full-resolution image
  inspection improved, realtime v2 matured, app-server v2 gained broad
  filesystem RPCs, and the new Python SDK for the app-server API landed.
- `0.116.0` on March 19, 2026
  Device-code ChatGPT sign-in arrived for app-server onboarding, plugins became
  easier to install/sync, and the new `userpromptsubmit` hook enabled prompt
  interception before execution.
- `0.117.0` on March 26, 2026
  Plugins became a first-class user workflow, sub-agents switched to readable
  path-based addresses like `/root/agent_a`, and remote/app-server shell plus
  filesystem interactions broadened.
- `0.118.0` on March 31, 2026
  Windows sandboxing got real OS-level proxy-only egress enforcement, device
  code login improved auth resilience, and custom model providers gained
  dynamic bearer-token refresh.
- `0.119.0` on April 10, 2026
  Realtime voice sessions defaulted to the v2 WebRTC path, MCP/custom-server
  support deepened again, remote workflows gained more transport/control
  options, and session ergonomics improved in the TUI.
- `0.120.0` on April 11, 2026
  Realtime v2 could now stream background agent progress and queue follow-ups,
  hook visibility improved, and structured MCP tool output typing got better.
- `0.121.0` on April 15, 2026
  Marketplace management expanded sharply: plugin marketplaces could be added
  from GitHub, git URLs, local directories, or direct manifests; memory mode
  controls deepened; and MCP/plugin metadata and sandbox-state reporting grew.
- `0.122.0` on April 20, 2026
  Standalone installs became more self-contained, `/side` conversations landed,
  plan mode could start implementation in a fresh context, plugin workflows got
  much more polished, filesystem deny-read rules tightened, and tool discovery
  plus image generation became default features.

## Takeaways

- The biggest long-term theme was the move from "agent in a terminal" toward a
  broader platform: app-server v2, remote control, marketplace/plugins, and a
  fuller SDK surface all expanded quickly.
- The most important operational theme was tightening control: approvals,
  permission profiles, network rules, sandboxing, and deny-read constraints all
  became more explicit and more enforceable.
- The most important interaction theme was richer multi-threaded work:
  plan mode, steer mode, side conversations, sub-agents, voice/realtime, and
  memory all became more central over this span.

## Source Releases

- [Official `openai/codex` releases](https://github.com/openai/codex/releases)
- [Starting point: `rust-v0.92.0`](https://github.com/openai/codex/releases/tag/rust-v0.92.0)
- [Key midpoint: `rust-v0.100.0`](https://github.com/openai/codex/releases/tag/rust-v0.100.0)
- [Key platform shift: `rust-v0.115.0`](https://github.com/openai/codex/releases/tag/rust-v0.115.0)
- [Current stable release: `rust-v0.122.0`](https://github.com/openai/codex/releases/tag/rust-v0.122.0)

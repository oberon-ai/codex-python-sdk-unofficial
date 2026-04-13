# codex-python-sdk-unofficial

An unofficial, natively async Python SDK for Codex that is intended to talk directly to `codex app-server` over JSON-RPC v2 on stdio.

The project scope is intentionally narrow in early tasks so the implementation does not drift toward CLI wrapper shortcuts or hidden blocking behavior. The architecture note in [docs/adr/0001-native-async-app-server-scope.md](docs/adr/0001-native-async-app-server-scope.md) defines:

- the required transport and protocol
- the supported v1 use cases
- the explicit non-goals and rejected shortcuts
- the baseline Python version and async-only requirement

Claude Agent SDK references inform ergonomics only. They are not the architecture template for this repository.

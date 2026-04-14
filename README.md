# codex-python-sdk-unofficial

An unofficial, natively async Python SDK for Codex that is intended to talk directly to `codex app-server` over JSON-RPC v2 on stdio.

The project scope is intentionally narrow in early tasks so the implementation does not drift toward CLI wrapper shortcuts or hidden blocking behavior. The architecture note in [docs/adr/0001-native-async-app-server-scope.md](docs/adr/0001-native-async-app-server-scope.md) defines:

- the required transport and protocol
- the supported v1 use cases
- the explicit non-goals and rejected shortcuts
- the baseline Python version and async-only requirement

The upstream reading order for future implementation work lives in [docs/upstream-reference-map.md](docs/upstream-reference-map.md).

The draft public surface for the SDK lives in [docs/public-api-contract.md](docs/public-api-contract.md).

The Claude-to-Codex ergonomics translation note lives in [docs/ergonomics-mapping.md](docs/ergonomics-mapping.md).

The runtime concurrency and state model lives in [docs/adr/0002-concurrency-and-state-model.md](docs/adr/0002-concurrency-and-state-model.md).

The error hierarchy, timeout defaults, and cancellation policy live in [docs/adr/0003-errors-timeouts-and-cancellation.md](docs/adr/0003-errors-timeouts-and-cancellation.md).

The package boundary guide lives in [docs/package-layout.md](docs/package-layout.md).

The curated root import surface and import policy live in [docs/public-import-policy.md](docs/public-import-policy.md).

The high-level options layering and precedence guide lives in
[docs/codex-options.md](docs/codex-options.md).

The dependency rationale and pinned repo toolchain live in [docs/dependency-policy.md](docs/dependency-policy.md).

The pinned schema snapshots, refresh workflow, and stable-versus-experimental
policy live in [docs/schema-vendoring.md](docs/schema-vendoring.md).

The stable Pydantic wire-model generation workflow lives in
[docs/protocol-model-codegen.md](docs/protocol-model-codegen.md).

Protocol fixture, schema snapshot, fake-server script, and golden transcript
conventions live in [tests/fixtures/README.md](tests/fixtures/README.md).

The reusable fake app-server harness and JSONL script format live in
[`codex_agent_sdk.testing.fake_app_server`](src/codex_agent_sdk/testing/fake_app_server.py)
and [tests/fixtures/fake_server_scripts/README.md](tests/fixtures/fake_server_scripts/README.md).

## Development

Install the project in editable mode with dev tooling:

```bash
python -m pip install -e ".[dev]"
```

For exact repo pins used by local verification and CI:

```bash
python -m pip install -e . -r requirements/dev.txt
```

Expected local and CI-friendly commands live in [CONTRIBUTING.md](CONTRIBUTING.md).

Claude Agent SDK references inform ergonomics only. They are not the architecture template for this repository.

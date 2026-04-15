# Documentation

This directory collects the public and maintainer-facing documentation for the
SDK.

## User Guide

- [API overview](api.md)
  describes the supported entry points, streaming events, approvals, and result
  helpers.
- [Codex options](codex-options.md)
  explains how `CodexOptions` and `AppServerConfig` are separated and how
  option layering works.
- [Public import policy](public-import-policy.md)
  documents which modules are meant to be stable imports.

## Repository Structure

- [Package layout](package-layout.md)
  maps the main source packages, tests, fixtures, examples, and scripts.
- [Dependency policy](dependency-policy.md)
  explains the runtime dependency surface and the `uv` workflow.

## Maintainer Workflows

- [Schema vendoring](schema-vendoring.md)
  covers the checked-in schema snapshots and refresh process.
- [Protocol model code generation](protocol-model-codegen.md)
  covers the generated Python artifacts and regeneration checks.
- [Scripts guide](../scripts/README.md)
  summarizes the repository maintenance scripts.

## More

- [Examples](../examples/README.md)
- [Contributing](../CONTRIBUTING.md)

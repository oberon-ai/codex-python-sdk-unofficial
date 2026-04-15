# Examples

The example scripts are meant to run directly from the repository checkout with
`uv run python ...`.

Each script expects a working `codex` CLI on `PATH` unless you pass
`--codex-bin`.

## `workspace_brief.py`

Streams a short brief for the current workspace using the one-shot `query()`
helper.

```bash
uv run python examples/workspace_brief.py
```

Useful options:

- `--cwd PATH`
- `--model MODEL`
- `--sandbox read-only|workspace-write|danger-full-access`
- `--codex-bin /path/to/codex`

## `file_brief.py`

Summarizes one UTF-8 text file and reports the terminal turn status and token
usage.

```bash
uv run python examples/file_brief.py path/to/file.txt
```

Useful options:

- `--max-bytes N`
- `--cwd PATH`
- `--model MODEL`
- `--sandbox read-only|workspace-write|danger-full-access`
- `--codex-bin /path/to/codex`

## `interactive_thread.py`

Runs a small interactive loop on top of `AppServerClient`. It starts one
thread, sends prompts from standard input, streams turn events, and lets you
answer approval requests from the terminal.

```bash
uv run python examples/interactive_thread.py
```

Useful options:

- `--cwd PATH`
- `--model MODEL`
- `--sandbox read-only|workspace-write|danger-full-access`
- `--codex-bin /path/to/codex`

This is the best example to read when you want to understand the low-level
client surface and approval handling together.

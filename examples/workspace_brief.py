# ruff: noqa: E402
"""Stream a concise Codex-generated brief for the current workspace.

This example requires no positional arguments and no input files. It uses the
top-level ``query()`` helper so it stays small and easy to adapt.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_agent_sdk import (
    AgentTextDeltaEvent,
    AppServerConfig,
    CodexOptions,
    TurnCompletedEvent,
    query,
)

DEFAULT_PROMPT = """Create a concise workspace brief for the current project.

Cover:
1. what kind of project this is
2. the most important modules, entrypoints, or workflows you notice
3. one sensible next step for a new contributor

Keep it concrete and easy to scan.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex-bin",
        help="Optional path to the Codex binary. Defaults to resolving `codex` from PATH.",
    )
    parser.add_argument(
        "--cwd",
        default=str(Path.cwd()),
        help="Workspace directory Codex should inspect. Defaults to the current directory.",
    )
    parser.add_argument(
        "--model",
        help="Optional model override. When omitted, Codex uses its normal default selection.",
    )
    parser.add_argument(
        "--sandbox",
        choices=("read-only", "workspace-write", "danger-full-access"),
        default="read-only",
        help="Sandbox mode for the example. Defaults to read-only for safety.",
    )
    return parser


def _extract_total_tokens(token_usage: object | None) -> int | None:
    total = getattr(token_usage, "total", None)
    total_tokens = getattr(total, "total_tokens", None)
    return total_tokens if isinstance(total_tokens, int) else None


async def run(args: argparse.Namespace) -> int:
    app_server = AppServerConfig(codex_bin=args.codex_bin) if args.codex_bin else None
    options = CodexOptions(
        approval_policy="never",
        cwd=args.cwd,
        model=args.model,
        sandbox_mode=args.sandbox,
    )

    assistant_stream_open = False
    saw_assistant_output = False
    completion_event: TurnCompletedEvent | None = None

    async for event in query(
        prompt=DEFAULT_PROMPT,
        options=options,
        app_server=app_server,
    ):
        if isinstance(event, AgentTextDeltaEvent):
            if not assistant_stream_open:
                print("assistant> ", end="", flush=True)
                assistant_stream_open = True
            saw_assistant_output = True
            print(event.text_delta, end="", flush=True)
            continue

        if isinstance(event, TurnCompletedEvent):
            completion_event = event

    if assistant_stream_open:
        print()

    if (
        completion_event is not None
        and not saw_assistant_output
        and completion_event.result is not None
        and completion_event.result.assistant_text
    ):
        print(f"assistant> {completion_event.result.assistant_text}")

    if completion_event is None:
        print("status: incomplete")
        return 1

    print(f"status: {completion_event.turn_status}")
    total_tokens = _extract_total_tokens(
        None if completion_event.result is None else completion_event.result.token_usage
    )
    if total_tokens is not None:
        print(f"total tokens: {total_tokens}")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

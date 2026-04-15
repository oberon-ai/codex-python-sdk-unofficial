# ruff: noqa: E402
"""Run a small interactive REPL on top of the low-level AppServerClient."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_agent_sdk import (
    AgentTextDeltaEvent,
    ApprovalDecision,
    AppServerClient,
    AppServerConfig,
    CommandApprovalRequest,
    CommandOutputDeltaEvent,
    FileChangeApprovalRequest,
    PermissionsApprovalRequest,
    ThreadStatusChangedEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
    adapt_approval_request,
)
from codex_agent_sdk.protocol.adapters import TurnEventAdapterState, adapt_turn_notification


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex-bin",
        help="Optional path to the Codex binary. Defaults to resolving `codex` from PATH.",
    )
    parser.add_argument(
        "--cwd",
        default=str(Path.cwd()),
        help="Workspace directory Codex should use for the interactive thread.",
    )
    parser.add_argument(
        "--model",
        help="Optional model override. When omitted, Codex uses its normal default selection.",
    )
    parser.add_argument(
        "--sandbox",
        choices=("read-only", "workspace-write", "danger-full-access"),
        default="read-only",
        help="Sandbox mode for the thread. Defaults to read-only for safety.",
    )
    return parser


def _extract_total_tokens(token_usage: object | None) -> int | None:
    total = getattr(token_usage, "total", None)
    total_tokens = getattr(total, "total_tokens", None)
    return total_tokens if isinstance(total_tokens, int) else None


async def _read_console_line(prompt: str) -> str | None:
    try:
        return await asyncio.to_thread(input, prompt)
    except EOFError:
        return None


async def _read_next_notification(notifications: AsyncIterator[object]) -> object:
    return await anext(notifications)


async def _read_next_request(requests: AsyncIterator[object]) -> object:
    return await anext(requests)


def _finish_assistant_line(assistant_stream_open: bool) -> bool:
    if assistant_stream_open:
        print()
    return False


async def _prompt_standard_decision() -> ApprovalDecision:
    while True:
        choice = await _read_console_line(
            "approval> [a]ccept, accept for [s]ession, [d]ecline, [c]ancel: "
        )
        if choice is None:
            return ApprovalDecision.cancel()

        normalized = choice.strip().lower()
        if normalized in {"a", "accept"}:
            return ApprovalDecision.accept()
        if normalized in {"s", "session", "accept-for-session"}:
            return ApprovalDecision.accept_for_session()
        if normalized in {"d", "decline"}:
            return ApprovalDecision.decline()
        if normalized in {"c", "cancel"}:
            return ApprovalDecision.cancel()

        print("Please choose a, s, d, or c.")


async def _handle_permissions_request(
    client: AppServerClient,
    request: PermissionsApprovalRequest,
) -> None:
    print("Approval requested.")
    print("kind: permissions")
    if request.reason:
        print(f"reason: {request.reason}")
    if request.permissions.file_system is not None:
        read_paths = ", ".join(request.permissions.file_system.read_paths) or "(none)"
        write_paths = ", ".join(request.permissions.file_system.write_paths) or "(none)"
        print(f"read paths: {read_paths}")
        print(f"write paths: {write_paths}")

    while True:
        choice = await _read_console_line(
            "approval> [g]rant for turn, grant for [s]ession, [r]eject: "
        )
        if choice is None:
            choice = "r"

        normalized = choice.strip().lower()
        if normalized in {"g", "grant"}:
            await request.respond(
                ApprovalDecision.grant_permissions(request.permissions, scope="turn")
            )
            print("approval> granted for turn")
            return
        if normalized in {"s", "session", "grant-for-session"}:
            await request.respond(
                ApprovalDecision.grant_permissions(request.permissions, scope="session")
            )
            print("approval> granted for session")
            return
        if normalized in {"r", "reject"}:
            await client.reject_server_request(
                request.request_id,
                code=-32000,
                message="Declined by interactive example user",
            )
            print("approval> rejected")
            return

        print("Please choose g, s, or r.")


async def _handle_approval_request(
    client: AppServerClient,
    request: CommandApprovalRequest | FileChangeApprovalRequest | PermissionsApprovalRequest,
) -> None:
    if isinstance(request, PermissionsApprovalRequest):
        await _handle_permissions_request(client, request)
        return

    print("Approval requested.")
    if isinstance(request, CommandApprovalRequest):
        print("kind: command execution")
        if request.command:
            print(f"command: {' '.join(request.command)}")
        if request.cwd:
            print(f"cwd: {request.cwd}")
    else:
        print("kind: file change")
        if request.changes:
            print(f"changes: {', '.join(change.path for change in request.changes)}")

    if request.reason:
        print(f"reason: {request.reason}")

    decision = await _prompt_standard_decision()
    await request.respond(decision)
    print(f"approval> sent {decision.decision}")


def _approval_responder(
    client: AppServerClient,
    request_id: object,
):
    async def _respond(decision: ApprovalDecision) -> None:
        await client.respond_approval_request(request_id, decision)

    return _respond


def _render_turn_event(
    event: object,
    *,
    assistant_stream_open: bool,
    saw_assistant_output: bool,
) -> tuple[bool, bool]:
    if isinstance(event, TurnStartedEvent):
        assistant_stream_open = _finish_assistant_line(assistant_stream_open)
        print(f"turn> {event.turn_id} ({event.turn_status})")
        return assistant_stream_open, saw_assistant_output

    if isinstance(event, AgentTextDeltaEvent):
        if not assistant_stream_open:
            print("assistant> ", end="", flush=True)
            assistant_stream_open = True
        print(event.text_delta, end="", flush=True)
        return assistant_stream_open, True

    if isinstance(event, CommandOutputDeltaEvent):
        assistant_stream_open = _finish_assistant_line(assistant_stream_open)
        print("command>")
        print(event.output_delta, end="", flush=True)
        return assistant_stream_open, saw_assistant_output

    if isinstance(event, ThreadStatusChangedEvent):
        assistant_stream_open = _finish_assistant_line(assistant_stream_open)
        print(f"thread status: {event.thread_status}")
        return assistant_stream_open, saw_assistant_output

    if isinstance(event, TurnCompletedEvent):
        assistant_stream_open = _finish_assistant_line(assistant_stream_open)
        if not event.result or not event.result.assistant_text:
            pass
        elif event.result.assistant_text and not saw_assistant_output:
            print(f"assistant> {event.result.assistant_text}")
        print(f"status: {event.turn_status}")
        total_tokens = _extract_total_tokens(
            None if event.result is None else event.result.token_usage
        )
        if total_tokens is not None:
            print(f"total tokens: {total_tokens}")
        return assistant_stream_open, saw_assistant_output

    return assistant_stream_open, saw_assistant_output


async def _run_turn(
    client: AppServerClient,
    *,
    thread_id: str,
    prompt: str,
) -> TurnCompletedEvent:
    notification_subscription = client.subscribe_thread_notifications(thread_id)
    server_request_subscription = client.subscribe_thread_server_requests(thread_id)
    notifications = notification_subscription.iter_notifications()
    server_requests = server_request_subscription.iter_requests()

    try:
        turn_response = await client.turn_start(thread_id=thread_id, input=prompt)
        turn_id = turn_response.turn.id
        state = TurnEventAdapterState()
        assistant_stream_open = False
        saw_assistant_output = False

        notification_task = asyncio.create_task(
            _read_next_notification(notifications),
            name=f"interactive-thread.notification:{turn_id}",
        )
        request_task = asyncio.create_task(
            _read_next_request(server_requests),
            name=f"interactive-thread.request:{turn_id}",
        )

        try:
            while True:
                done, _pending = await asyncio.wait(
                    {notification_task, request_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if request_task in done:
                    try:
                        request_envelope = request_task.result()
                    except StopAsyncIteration as exc:
                        raise RuntimeError(
                            f"server request stream closed before turn {turn_id} completed"
                        ) from exc

                    request_task = asyncio.create_task(
                        _read_next_request(server_requests),
                        name=f"interactive-thread.request:{turn_id}",
                    )

                    request_id = getattr(request_envelope, "request_id", None)
                    approval_request = adapt_approval_request(
                        request_envelope,
                        responder=_approval_responder(client, request_id),
                    )
                    if approval_request is None or approval_request.turn_id != turn_id:
                        continue

                    assistant_stream_open = _finish_assistant_line(assistant_stream_open)
                    await _handle_approval_request(client, approval_request)

                if notification_task in done:
                    try:
                        notification = notification_task.result()
                    except StopAsyncIteration as exc:
                        raise RuntimeError(
                            f"notification stream closed before turn {turn_id} completed"
                        ) from exc

                    notification_task = asyncio.create_task(
                        _read_next_notification(notifications),
                        name=f"interactive-thread.notification:{turn_id}",
                    )

                    event = adapt_turn_notification(
                        notification,
                        target_turn_id=turn_id,
                        state=state,
                    )
                    if event is None:
                        continue

                    assistant_stream_open, saw_assistant_output = _render_turn_event(
                        event,
                        assistant_stream_open=assistant_stream_open,
                        saw_assistant_output=saw_assistant_output,
                    )
                    if isinstance(event, TurnCompletedEvent):
                        return event
        finally:
            notification_task.cancel()
            request_task.cancel()
            await asyncio.gather(notification_task, request_task, return_exceptions=True)
    finally:
        notification_subscription.close()
        server_request_subscription.close()


async def run(args: argparse.Namespace) -> int:
    app_server = AppServerConfig(codex_bin=args.codex_bin) if args.codex_bin else AppServerConfig()

    async with AppServerClient(app_server) as client:
        await client.initialize()
        thread_response = await client.thread_start(
            approval_policy="on-request",
            approvals_reviewer="user",
            cwd=args.cwd,
            ephemeral=True,
            model=args.model,
            sandbox=args.sandbox,
        )
        thread_id = thread_response.thread.id

        print(f"thread> {thread_id}")
        print("Type /quit to exit.")

        while True:
            prompt = await _read_console_line("you> ")
            if prompt is None:
                print()
                print("bye")
                return 0

            stripped = prompt.strip()
            if not stripped:
                continue
            if stripped == "/quit":
                print("bye")
                return 0

            await _run_turn(client, thread_id=thread_id, prompt=stripped)


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

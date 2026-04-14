from __future__ import annotations

import asyncio
import os
import sys
import textwrap
from pathlib import Path

import pytest

from codex_agent_sdk import (
    AgentTextDeltaEvent,
    ApprovalDecision,
    ApprovalRequestedEvent,
    AppServerConfig,
    CodexOptions,
    TokenUsageUpdatedEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
    query,
)
from codex_agent_sdk.testing import (
    FakeAppServerScript,
    expect_notification,
    expect_request,
    expect_response,
    send_notification,
    send_response,
    send_server_request,
    sleep_action,
)

FAKE_SERVER_MODULE = "codex_agent_sdk.testing.fake_app_server"


@pytest.mark.asyncio
async def test_query_streams_string_prompt_events_and_supports_inline_approval_response(
    tmp_path: Path,
) -> None:
    output_schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    }
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "thread/start",
            save_as="thread_start",
            params={
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "cwd": "/repo",
                "developerInstructions": "Use pytest.",
                "ephemeral": True,
                "model": "gpt-5.4",
            },
        ),
        send_response(
            request_ref="thread_start",
            result=_build_thread_start_result(thread_id="thread_query_manual"),
        ),
        expect_request(
            "turn/start",
            save_as="turn_start",
            params={
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "cwd": "/repo",
                "effort": "high",
                "input": [{"type": "text", "text": "Audit the repo."}],
                "model": "gpt-5.4",
                "outputSchema": output_schema,
                "summary": "detailed",
                "threadId": "thread_query_manual",
            },
        ),
        send_response(
            request_ref="turn_start",
            result=_build_turn_start_result(turn_id="turn_query_manual"),
        ),
        send_notification(
            "turn/started",
            params={
                "threadId": "thread_query_manual",
                "turn": _build_turn_payload(
                    turn_id="turn_query_manual",
                    status="inProgress",
                ),
            },
        ),
        send_notification(
            "item/agentMessage/delta",
            params={
                "delta": "Draft answer.",
                "itemId": "item_agent_query_manual",
                "threadId": "thread_query_manual",
                "turnId": "turn_query_manual",
            },
        ),
        send_notification(
            "thread/tokenUsage/updated",
            params=_build_turn_token_usage_payload(
                thread_id="thread_query_manual",
                turn_id="turn_query_manual",
            ),
        ),
        sleep_action(20),
        send_server_request(
            "item/commandExecution/requestApproval",
            request_id="approval-query-manual-1",
            params={
                "threadId": "thread_query_manual",
                "turnId": "turn_query_manual",
                "itemId": "item_command_query_manual",
                "command": ["pytest", "-q"],
                "reason": "Run the targeted test command.",
            },
        ),
        expect_response(
            request_ref="approval-query-manual-1",
            result={"decision": "accept"},
        ),
        send_notification(
            "serverRequest/resolved",
            params={
                "threadId": "thread_query_manual",
                "requestId": "approval-query-manual-1",
            },
        ),
        send_notification(
            "turn/completed",
            params={
                "threadId": "thread_query_manual",
                "turn": _build_turn_payload(
                    turn_id="turn_query_manual",
                    status="completed",
                ),
            },
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="query_manual_approval_launcher.py",
    )

    collected: list[object] = []
    async for event in query(
        prompt="Audit the repo.",
        options=CodexOptions(
            model="gpt-5.4",
            cwd="/repo",
            approval_policy="on-request",
            approvals_reviewer="user",
            developer_instructions="Use pytest.",
            effort="high",
            summary="detailed",
        ),
        app_server=AppServerConfig(codex_bin=str(launcher)),
        output_schema=output_schema,
    ):
        collected.append(event)
        if isinstance(event, ApprovalRequestedEvent):
            await event.respond(ApprovalDecision.accept())

    assert [type(event) for event in collected] == [
        TurnStartedEvent,
        AgentTextDeltaEvent,
        TokenUsageUpdatedEvent,
        ApprovalRequestedEvent,
        TurnCompletedEvent,
    ]

    started_event = collected[0]
    assert isinstance(started_event, TurnStartedEvent)
    assert started_event.thread_id == "thread_query_manual"
    assert started_event.turn_id == "turn_query_manual"

    text_event = collected[1]
    assert isinstance(text_event, AgentTextDeltaEvent)
    assert text_event.text_delta == "Draft answer."

    token_event = collected[2]
    assert isinstance(token_event, TokenUsageUpdatedEvent)
    assert token_event.turn_id == "turn_query_manual"

    approval_event = collected[3]
    assert isinstance(approval_event, ApprovalRequestedEvent)
    assert approval_event.request.thread_id == "thread_query_manual"
    assert approval_event.request.turn_id == "turn_query_manual"
    assert approval_event.request.item_id == "item_command_query_manual"

    completion_event = collected[4]
    assert isinstance(completion_event, TurnCompletedEvent)
    assert completion_event.turn_status == "completed"
    assert completion_event.result is not None
    assert completion_event.result.assistant_text == "Draft answer."
    assert completion_event.result.token_usage is not None


@pytest.mark.asyncio
async def test_query_accepts_structured_input_items_and_threads_approval_handler(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request("thread/start", save_as="thread_start", params={"ephemeral": True}),
        send_response(
            request_ref="thread_start",
            result=_build_thread_start_result(thread_id="thread_query_structured"),
        ),
        expect_request(
            "turn/start",
            save_as="turn_start",
            params={
                "input": [
                    {"type": "text", "text": "First instruction."},
                    {"type": "text", "text": "Second instruction."},
                ],
                "threadId": "thread_query_structured",
            },
        ),
        send_response(
            request_ref="turn_start",
            result=_build_turn_start_result(turn_id="turn_query_structured"),
        ),
        send_notification(
            "turn/started",
            params={
                "threadId": "thread_query_structured",
                "turn": _build_turn_payload(
                    turn_id="turn_query_structured",
                    status="inProgress",
                ),
            },
        ),
        sleep_action(20),
        send_server_request(
            "item/commandExecution/requestApproval",
            request_id="approval-query-auto-1",
            params={
                "threadId": "thread_query_structured",
                "turnId": "turn_query_structured",
                "itemId": "item_command_query_structured",
                "command": ["pytest", "-q"],
            },
        ),
        expect_response(
            request_ref="approval-query-auto-1",
            result={"decision": "accept"},
        ),
        send_notification(
            "serverRequest/resolved",
            params={
                "threadId": "thread_query_structured",
                "requestId": "approval-query-auto-1",
            },
        ),
        send_notification(
            "turn/completed",
            params={
                "threadId": "thread_query_structured",
                "turn": _build_turn_payload(
                    turn_id="turn_query_structured",
                    status="completed",
                ),
            },
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="query_structured_input_launcher.py",
    )

    async def _approval_handler(_request: object) -> ApprovalDecision:
        return ApprovalDecision.accept()

    events = [
        event
        async for event in query(
            prompt=[
                {"type": "text", "text": "First instruction."},
                {"type": "text", "text": "Second instruction."},
            ],
            app_server=AppServerConfig(codex_bin=str(launcher)),
            approval_handler=_approval_handler,
        )
    ]

    assert [type(event) for event in events] == [
        TurnStartedEvent,
        TurnCompletedEvent,
    ]


@pytest.mark.asyncio
async def test_query_consumer_cancellation_closes_subprocess(tmp_path: Path) -> None:
    pid_path = tmp_path / "query_cancel.pid"
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request("thread/start", save_as="thread_start", params={"ephemeral": True}),
        send_response(
            request_ref="thread_start",
            result=_build_thread_start_result(thread_id="thread_query_cancel"),
        ),
        expect_request(
            "turn/start",
            save_as="turn_start",
            params={
                "input": [{"type": "text", "text": "Cancel after the first event."}],
                "threadId": "thread_query_cancel",
            },
        ),
        send_response(
            request_ref="turn_start",
            result=_build_turn_start_result(turn_id="turn_query_cancel"),
        ),
        send_notification(
            "turn/started",
            params={
                "threadId": "thread_query_cancel",
                "turn": _build_turn_payload(
                    turn_id="turn_query_cancel",
                    status="inProgress",
                ),
            },
        ),
        sleep_action(5_000),
    )
    launcher = _write_pid_recording_fake_codex_launcher(
        tmp_path,
        script,
        pid_path=pid_path,
        stem="query_cancel_launcher.py",
    )
    first_event_seen = asyncio.Event()

    async def _consume() -> None:
        async for _event in query(
            prompt="Cancel after the first event.",
            app_server=AppServerConfig(
                codex_bin=str(launcher),
                shutdown_timeout=0.05,
            ),
        ):
            first_event_seen.set()
            await asyncio.sleep(60)

    task = asyncio.create_task(_consume())
    await asyncio.wait_for(first_event_seen.wait(), timeout=1.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    pid = int(pid_path.read_text(encoding="utf-8"))
    await _wait_for_process_exit(pid, timeout=1.0)


def _build_thread_payload(*, thread_id: str) -> dict[str, object]:
    return {
        "cliVersion": "codex-cli 0.118.0",
        "createdAt": 1_710_000_000,
        "cwd": "/repo",
        "ephemeral": True,
        "id": thread_id,
        "modelProvider": "openai",
        "preview": "Audit the repo.",
        "source": "appServer",
        "status": {"type": "idle"},
        "turns": [],
        "updatedAt": 1_710_000_001,
    }


def _build_thread_start_result(*, thread_id: str) -> dict[str, object]:
    return {
        "approvalPolicy": "on-request",
        "approvalsReviewer": "user",
        "cwd": "/repo",
        "model": "gpt-5.4",
        "modelProvider": "openai",
        "sandbox": {"type": "dangerFullAccess"},
        "thread": _build_thread_payload(thread_id=thread_id),
    }


def _build_turn_payload(
    *,
    turn_id: str,
    status: str,
) -> dict[str, object]:
    return {
        "id": turn_id,
        "items": [],
        "status": status,
    }


def _build_turn_start_result(*, turn_id: str) -> dict[str, object]:
    return {"turn": _build_turn_payload(turn_id=turn_id, status="inProgress")}


def _build_turn_token_usage_payload(
    *,
    thread_id: str,
    turn_id: str,
) -> dict[str, object]:
    return {
        "threadId": thread_id,
        "turnId": turn_id,
        "tokenUsage": {
            "last": {
                "cachedInputTokens": 0,
                "inputTokens": 12,
                "outputTokens": 7,
                "reasoningOutputTokens": 3,
                "totalTokens": 22,
            },
            "total": {
                "cachedInputTokens": 5,
                "inputTokens": 20,
                "outputTokens": 15,
                "reasoningOutputTokens": 6,
                "totalTokens": 46,
            },
        },
    }


def _write_fake_codex_launcher(
    tmp_path: Path,
    script: FakeAppServerScript,
    *,
    stem: str,
) -> Path:
    script_path = tmp_path / f"{Path(stem).stem}.script.jsonl"
    launcher_path = tmp_path / stem
    script.write_jsonl(script_path)
    return _write_executable_script(
        launcher_path,
        f"""
        import os
        import sys

        os.execv(
            sys.executable,
            [
                sys.executable,
                "-m",
                "{FAKE_SERVER_MODULE}",
                "--script",
                {str(script_path)!r},
            ],
        )
        """,
    )


def _write_pid_recording_fake_codex_launcher(
    tmp_path: Path,
    script: FakeAppServerScript,
    *,
    pid_path: Path,
    stem: str,
) -> Path:
    script_path = tmp_path / f"{Path(stem).stem}.script.jsonl"
    launcher_path = tmp_path / stem
    script.write_jsonl(script_path)
    return _write_executable_script(
        launcher_path,
        f"""
        import os
        import pathlib
        import sys

        pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()), encoding="utf-8")
        os.execv(
            sys.executable,
            [
                sys.executable,
                "-m",
                "{FAKE_SERVER_MODULE}",
                "--script",
                {str(script_path)!r},
            ],
        )
        """,
    )


def _write_executable_script(path: Path, body: str) -> Path:
    script = "\n".join(
        [
            f"#!{sys.executable}",
            textwrap.dedent(body).strip(),
            "",
        ]
    )
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)
    return path


async def _wait_for_process_exit(pid: int, *, timeout: float) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        if _process_is_running(pid):
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail(f"process {pid} was still running after {timeout} seconds")
            await asyncio.sleep(0.01)
            continue
        return


def _process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True

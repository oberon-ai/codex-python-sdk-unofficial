from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from codex_agent_sdk import (
    AgentTextDeltaEvent,
    ApprovalDecision,
    AppServerConfig,
    CodexOptions,
    CodexSDKClient,
    SyncCodexSDKClient,
    TurnCompletedEvent,
    TurnStartedEvent,
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
async def test_codex_sdk_client_start_thread_and_query_stream_result(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "thread/start",
            save_as="thread_start",
            params={
                "approvalPolicy": "on-request",
                "baseInstructions": "Stay terse.",
                "cwd": "/repo",
                "ephemeral": True,
                "model": "gpt-5.4",
            },
        ),
        send_response(
            request_ref="thread_start",
            result=_build_thread_start_result(thread_id="thread_sdk_async"),
        ),
        expect_request(
            "turn/start",
            save_as="turn_start",
            params={
                "approvalPolicy": "on-request",
                "cwd": "/repo",
                "effort": "high",
                "input": [{"type": "text", "text": "Audit the repo."}],
                "model": "gpt-5.4",
                "summary": "detailed",
                "threadId": "thread_sdk_async",
            },
        ),
        send_response(
            request_ref="turn_start",
            result=_build_turn_start_result(turn_id="turn_sdk_async"),
        ),
        send_notification(
            "turn/started",
            params={
                "threadId": "thread_sdk_async",
                "turn": _build_turn_payload(turn_id="turn_sdk_async", status="inProgress"),
            },
        ),
        send_notification(
            "item/agentMessage/delta",
            params={
                "delta": "Draft answer.",
                "itemId": "item_sdk_async",
                "threadId": "thread_sdk_async",
                "turnId": "turn_sdk_async",
            },
        ),
        send_notification(
            "turn/completed",
            params={
                "threadId": "thread_sdk_async",
                "turn": _build_turn_payload(turn_id="turn_sdk_async", status="completed"),
            },
        ),
    )
    launcher = _write_fake_codex_launcher(tmp_path, script, stem="sdk_client_async_launcher.py")

    async with CodexSDKClient(
        options=CodexOptions(model="gpt-5.4", cwd="/repo"),
        app_server=AppServerConfig(codex_bin=str(launcher)),
    ) as client:
        thread_id = await client.start_thread(
            options=CodexOptions(
                approval_policy="on-request",
                base_instructions="Stay terse.",
            ),
            ephemeral=True,
        )

        assert thread_id == "thread_sdk_async"
        assert client.thread_id == "thread_sdk_async"
        assert client.thread_status == "idle"

        handle = await client.query(
            "Audit the repo.",
            options=CodexOptions(
                effort="high",
                summary="detailed",
            ),
        )
        events = [event async for event in handle]
        result = await handle.wait()

    assert [type(event) for event in events] == [
        TurnStartedEvent,
        AgentTextDeltaEvent,
        TurnCompletedEvent,
    ]
    completion_event = events[-1]
    assert isinstance(completion_event, TurnCompletedEvent)
    assert completion_event.result == result
    assert result.thread_id == "thread_sdk_async"
    assert result.turn_id == "turn_sdk_async"
    assert result.status == "completed"
    assert result.assistant_text == "Draft answer."


@pytest.mark.asyncio
async def test_codex_sdk_client_query_auto_starts_thread_and_turn_handle_controls(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "thread/start",
            save_as="thread_start",
            params={"ephemeral": False},
        ),
        send_response(
            request_ref="thread_start",
            result=_build_thread_start_result(thread_id="thread_sdk_auto"),
        ),
        expect_request(
            "turn/start",
            save_as="turn_start",
            params={
                "input": [{"type": "text", "text": "Kick off a long turn."}],
                "threadId": "thread_sdk_auto",
            },
        ),
        send_response(
            request_ref="turn_start",
            result=_build_turn_start_result(turn_id="turn_sdk_auto"),
        ),
        expect_request(
            "turn/steer",
            save_as="turn_steer",
            params={
                "expectedTurnId": "turn_sdk_auto",
                "input": [{"type": "text", "text": "Focus on tests."}],
                "threadId": "thread_sdk_auto",
            },
        ),
        send_response(
            request_ref="turn_steer",
            result={"turnId": "turn_sdk_auto"},
        ),
        expect_request(
            "turn/interrupt",
            save_as="turn_interrupt",
            params={
                "threadId": "thread_sdk_auto",
                "turnId": "turn_sdk_auto",
            },
        ),
        send_response(request_ref="turn_interrupt", result={}),
        sleep_action(20),
    )
    launcher = _write_fake_codex_launcher(tmp_path, script, stem="sdk_client_auto_launcher.py")

    async with CodexSDKClient(
        app_server=AppServerConfig(codex_bin=str(launcher)),
    ) as client:
        handle = await client.query("Kick off a long turn.")

        assert client.thread_id == "thread_sdk_auto"
        assert client.active_turn_id == "turn_sdk_auto"
        assert handle.thread_id == "thread_sdk_auto"
        assert handle.turn_id == "turn_sdk_auto"

        steered_turn_id = await handle.steer("Focus on tests.")
        await handle.interrupt()

    assert steered_turn_id == "turn_sdk_auto"


def test_sync_codex_sdk_client_wraps_async_client_and_sync_approval_handler(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "thread/start",
            save_as="thread_start",
            params={
                "approvalPolicy": "on-request",
                "ephemeral": False,
                "model": "gpt-5.4",
            },
        ),
        send_response(
            request_ref="thread_start",
            result=_build_thread_start_result(thread_id="thread_sdk_sync"),
        ),
        expect_request(
            "turn/start",
            save_as="turn_start",
            params={
                "approvalPolicy": "on-request",
                "input": [{"type": "text", "text": "Audit the sync path."}],
                "model": "gpt-5.4",
                "threadId": "thread_sdk_sync",
            },
        ),
        send_response(
            request_ref="turn_start",
            result=_build_turn_start_result(turn_id="turn_sdk_sync"),
        ),
        send_notification(
            "turn/started",
            params={
                "threadId": "thread_sdk_sync",
                "turn": _build_turn_payload(turn_id="turn_sdk_sync", status="inProgress"),
            },
        ),
        sleep_action(20),
        send_server_request(
            "item/commandExecution/requestApproval",
            request_id="approval-sdk-sync-1",
            params={
                "threadId": "thread_sdk_sync",
                "turnId": "turn_sdk_sync",
                "itemId": "item_sdk_sync",
                "command": ["pytest", "-q"],
            },
        ),
        expect_response(
            request_ref="approval-sdk-sync-1",
            result={"decision": "accept"},
        ),
        send_notification(
            "serverRequest/resolved",
            params={
                "threadId": "thread_sdk_sync",
                "requestId": "approval-sdk-sync-1",
            },
        ),
        send_notification(
            "turn/completed",
            params={
                "threadId": "thread_sdk_sync",
                "turn": _build_turn_payload(turn_id="turn_sdk_sync", status="completed"),
            },
        ),
    )
    launcher = _write_fake_codex_launcher(tmp_path, script, stem="sdk_client_sync_launcher.py")

    def _approval_handler(_request: object) -> ApprovalDecision:
        return ApprovalDecision.accept()

    with SyncCodexSDKClient(
        options=CodexOptions(model="gpt-5.4", approval_policy="on-request"),
        app_server=AppServerConfig(codex_bin=str(launcher)),
        approval_handler=_approval_handler,
    ) as client:
        handle = client.query("Audit the sync path.")
        events = list(handle)
        result = handle.wait()

        assert client.thread_id == "thread_sdk_sync"
        assert client.active_turn_id is None
        assert client.thread_status == "idle"

    assert [type(event) for event in events] == [
        TurnStartedEvent,
        TurnCompletedEvent,
    ]
    assert result.thread_id == "thread_sdk_sync"
    assert result.turn_id == "turn_sdk_sync"
    assert result.status == "completed"


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

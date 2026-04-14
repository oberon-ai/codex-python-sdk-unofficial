from __future__ import annotations

import asyncio
import logging
import sys
import textwrap
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from codex_agent_sdk import (
    AppServerClient,
    AppServerConfig,
    DuplicateResponseError,
    RequestTimeoutError,
    StartupTimeoutError,
    TransportClosedError,
    UnknownResponseIdError,
)
from codex_agent_sdk.rpc import JsonRpcNotification
from codex_agent_sdk.testing import (
    FakeAppServerScript,
    close_connection,
    emit_raw,
    expect_notification,
    expect_request,
    send_response,
    send_server_request,
    sleep_action,
)

IO_TIMEOUT_SECONDS = 1.0
FAKE_SERVER_MODULE = "codex_agent_sdk.testing.fake_app_server"


@pytest.mark.asyncio
async def test_initialize_uses_shared_startup_budget_for_initialize_response(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        sleep_action(120),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
    )
    launcher = _write_fake_codex_launcher(tmp_path, script)
    client = AppServerClient(
        AppServerConfig(
            codex_bin=str(launcher),
            startup_timeout=0.05,
        )
    )

    try:
        with pytest.raises(StartupTimeoutError) as exc_info:
            await client.initialize()
    finally:
        await client.close()

    assert exc_info.value.timeout_seconds == 0.05


@pytest.mark.asyncio
async def test_request_timeout_is_local_and_connection_remains_usable(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request("thread/start", save_as="thread_start", params={"ephemeral": True}),
        sleep_action(120),
        send_response(request_ref="thread_start", result={"threadId": "thread_slow"}),
        expect_request(
            "thread/resume",
            save_as="thread_resume",
            params={"threadId": "thread_slow"},
        ),
        send_response(
            request_ref="thread_resume",
            result={"threadId": "thread_slow", "status": "resumed"},
        ),
    )
    launcher = _write_fake_codex_launcher(tmp_path, script)

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        initialize_result = await client.initialize()
        assert initialize_result == {"protocolVersion": 2}

        with pytest.raises(RequestTimeoutError) as exc_info:
            await client.request("thread/start", {"ephemeral": True}, timeout=0.05)

        assert exc_info.value.method == "thread/start"
        assert exc_info.value.request_id == 2

        await asyncio.sleep(0.15)
        resumed = await client.request(
            "thread/resume",
            {"threadId": "thread_slow"},
            timeout=IO_TIMEOUT_SECONDS,
        )

    assert resumed == {"threadId": "thread_slow", "status": "resumed"}


@pytest.mark.asyncio
async def test_late_response_after_timeout_is_logged_and_connection_stays_usable(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="codex_agent_sdk.rpc.router")
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request("thread/start", save_as="thread_start", params={"ephemeral": True}),
        sleep_action(120),
        send_response(request_ref="thread_start", result={"threadId": "thread_slow"}),
        expect_request(
            "thread/resume",
            save_as="thread_resume",
            params={"threadId": "thread_slow"},
        ),
        send_response(
            request_ref="thread_resume",
            result={"threadId": "thread_slow", "status": "resumed"},
        ),
    )
    launcher = _write_fake_codex_launcher(tmp_path, script, stem="late_response_launcher.py")

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()

        with pytest.raises(RequestTimeoutError):
            await client.request("thread/start", {"ephemeral": True}, timeout=0.05)

        await asyncio.sleep(0.15)
        resumed = await client.request(
            "thread/resume",
            {"threadId": "thread_slow"},
            timeout=IO_TIMEOUT_SECONDS,
        )

    assert resumed == {"threadId": "thread_slow", "status": "resumed"}
    assert "ignoring late JSON-RPC response" in caplog.text


@pytest.mark.asyncio
async def test_unexpected_eof_releases_pending_request_waiter(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request("thread/start", params={"ephemeral": False}),
        close_connection(),
    )
    launcher = _write_fake_codex_launcher(tmp_path, script)

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()

        with pytest.raises(TransportClosedError) as exc_info:
            await asyncio.wait_for(
                client.request("thread/start", {"ephemeral": False}),
                timeout=IO_TIMEOUT_SECONDS,
            )

    assert "EOF" in str(exc_info.value)


@pytest.mark.asyncio
async def test_explicit_close_releases_pending_request_waiter(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request("thread/start", params={"ephemeral": False}),
        sleep_action(300),
    )
    launcher = _write_fake_codex_launcher(tmp_path, script)
    client = AppServerClient(AppServerConfig(codex_bin=str(launcher)))

    try:
        await client.initialize()
        request_task = asyncio.create_task(client.request("thread/start", {"ephemeral": False}))
        await asyncio.sleep(0.05)
        await client.close()

        with pytest.raises(TransportClosedError) as exc_info:
            await asyncio.wait_for(request_task, timeout=IO_TIMEOUT_SECONDS)
    finally:
        await client.close()

    assert "closed before request completion" in str(exc_info.value)


@pytest.mark.asyncio
async def test_multiple_requests_can_be_outstanding_and_resolve_out_of_order(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request("thread/start", save_as="thread_start", params={"ephemeral": True}),
        expect_request(
            "thread/resume",
            save_as="thread_resume",
            params={"threadId": "thread_existing"},
        ),
        send_response(
            request_ref="thread_resume",
            result={"threadId": "thread_existing", "status": "resumed"},
        ),
        send_response(
            request_ref="thread_start",
            result={"threadId": "thread_new", "status": "started"},
        ),
    )
    launcher = _write_fake_codex_launcher(tmp_path, script, stem="out_of_order_launcher.py")

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        start_task = asyncio.create_task(client.request("thread/start", {"ephemeral": True}))
        await asyncio.sleep(0)
        resume_task = asyncio.create_task(
            client.request("thread/resume", {"threadId": "thread_existing"})
        )

        start_result, resume_result = await asyncio.gather(start_task, resume_task)

    assert start_result == {"threadId": "thread_new", "status": "started"}
    assert resume_result == {"threadId": "thread_existing", "status": "resumed"}


@pytest.mark.asyncio
async def test_unknown_response_id_fails_connection(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        emit_raw('{"id":"req-999","result":{"ok":true}}'),
        sleep_action(50),
    )
    launcher = _write_fake_codex_launcher(tmp_path, script, stem="unknown_response_launcher.py")

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        await asyncio.sleep(0.05)

        with pytest.raises(UnknownResponseIdError) as exc_info:
            await client.request("thread/start", {"ephemeral": True})

    assert exc_info.value.request_id == "req-999"


@pytest.mark.asyncio
async def test_duplicate_response_id_fails_connection(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request("thread/start", save_as="thread_start", params={"ephemeral": True}),
        send_response(
            request_ref="thread_start",
            result={"threadId": "thread_new", "status": "started"},
        ),
        send_response(
            request_ref="thread_start",
            result={"threadId": "thread_new", "status": "started"},
        ),
        sleep_action(50),
    )
    launcher = _write_fake_codex_launcher(tmp_path, script, stem="duplicate_response_launcher.py")

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        started = await client.request("thread/start", {"ephemeral": True})
        await asyncio.sleep(0.05)

        with pytest.raises(DuplicateResponseError) as exc_info:
            await client.request("thread/resume", {"threadId": "thread_new"})

    assert started == {"threadId": "thread_new", "status": "started"}
    assert exc_info.value.method == "thread/start"


@pytest.mark.asyncio
async def test_server_request_iterator_receives_server_initiated_requests(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        send_server_request(
            "approval/requested",
            request_id="approval-1",
            params={
                "threadId": "thread_1",
                "turnId": "turn_1",
                "kind": "command",
            },
        ),
        sleep_action(300),
    )
    launcher = _write_fake_codex_launcher(tmp_path, script, stem="server_request_launcher.py")

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        request = await asyncio.wait_for(
            anext(client.iter_server_requests()),
            timeout=IO_TIMEOUT_SECONDS,
        )

    assert request.method == "approval/requested"
    assert request.request_id == "approval-1"
    assert request.params == {
        "threadId": "thread_1",
        "turnId": "turn_1",
        "kind": "command",
    }


@pytest.mark.asyncio
async def test_notification_iterator_wakes_on_close_and_failure(tmp_path: Path) -> None:
    close_script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        sleep_action(300),
    )
    eof_script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        close_connection(),
    )

    close_launcher = _write_fake_codex_launcher(tmp_path, close_script, stem="close_launcher.py")
    eof_launcher = _write_fake_codex_launcher(tmp_path, eof_script, stem="eof_launcher.py")

    close_client = AppServerClient(AppServerConfig(codex_bin=str(close_launcher)))
    try:
        await close_client.initialize()
        notifications = close_client.iter_notifications()
        close_task: asyncio.Task[object] = asyncio.create_task(
            _read_next_notification(notifications)
        )
        await asyncio.sleep(0.05)
        await close_client.close()

        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(close_task, timeout=IO_TIMEOUT_SECONDS)
    finally:
        await close_client.close()

    async with AppServerClient(AppServerConfig(codex_bin=str(eof_launcher))) as eof_client:
        await eof_client.initialize()
        notifications = eof_client.iter_notifications()

        with pytest.raises(TransportClosedError):
            await asyncio.wait_for(anext(notifications), timeout=IO_TIMEOUT_SECONDS)


def _write_fake_codex_launcher(
    tmp_path: Path,
    script: FakeAppServerScript,
    *,
    stem: str = "fake_codex.py",
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


async def _read_next_notification(
    notifications: AsyncIterator[JsonRpcNotification],
) -> JsonRpcNotification:
    return await anext(notifications)

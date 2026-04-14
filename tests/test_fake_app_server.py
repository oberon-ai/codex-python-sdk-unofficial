from __future__ import annotations

import asyncio
import json
import sys
import time
from asyncio.subprocess import PIPE, Process
from pathlib import Path
from typing import Any, cast

import pytest

from codex_agent_sdk.testing.fake_app_server import (
    FakeAppServerScript,
    close_connection,
    emit_invalid_json,
    expect_notification,
    expect_request,
    load_fake_app_server_script,
    send_response,
    sleep_action,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SCRIPT = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "fake_server_scripts"
    / "100_turn_start_and_complete.script.jsonl"
)
MODULE_NAME = "codex_agent_sdk.testing.fake_app_server"
IO_TIMEOUT_SECONDS = 2.0


@pytest.mark.asyncio
async def test_fixture_script_supports_notifications_and_server_requests() -> None:
    process = await _launch_fake_app_server(FIXTURE_SCRIPT)

    try:
        await _write_json(
            process,
            {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "pytest"}}},
        )
        initialize_response = await _read_json(process)
        initialize_result = _require_dict(initialize_response["result"])
        server_info = _require_dict(initialize_result["serverInfo"])
        assert initialize_response["id"] == 1
        assert server_info["name"] == "fake-codex-app-server"

        await _write_json(process, {"method": "initialized", "params": {}})

        await _write_json(
            process,
            {"id": 2, "method": "thread/start", "params": {"ephemeral": True, "cwd": "."}},
        )
        thread_response = await _read_json(process)
        thread_started = await _read_json(process)
        thread_result = _require_dict(thread_response["result"])
        assert thread_result["threadId"] == "thread_100"
        assert thread_started["method"] == "thread/started"

        await _write_json(
            process,
            {
                "id": 3,
                "method": "turn/start",
                "params": {"threadId": "thread_100", "input": [{"type": "text", "text": "hello"}]},
            },
        )
        turn_response = await _read_json(process)
        turn_started = await _read_json(process)
        approval_request = await _read_json(process)

        turn_result = _require_dict(turn_response["result"])
        approval_params = _require_dict(approval_request["params"])
        assert turn_result["turnId"] == "turn_100"
        assert turn_started["method"] == "turn/started"
        assert approval_request["method"] == "approval/requested"
        assert approval_params["kind"] == "command"

        await _write_json(
            process,
            {"id": approval_request["id"], "result": {"decision": "approved"}},
        )
        turn_completed = await _read_json(process)
        completed_params = _require_dict(turn_completed["params"])
        usage = _require_dict(completed_params["usage"])
        assert turn_completed["method"] == "turn/completed"
        assert usage["outputTokens"] == 34

        assert await _read_line(process) == ""
    finally:
        stderr = await _finish_process(process)

    assert process.returncode == 0, stderr


@pytest.mark.asyncio
async def test_builder_script_supports_delayed_overload_responses(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request("thread/start", save_as="thread_start", params={"ephemeral": False}),
        sleep_action(75),
        send_response(
            request_ref="thread_start",
            error={"code": -32001, "message": "Server overloaded; retry later."},
        ),
    )
    script_path = tmp_path / "200_delayed_overload.script.jsonl"
    script.write_jsonl(script_path)

    process = await _launch_fake_app_server(script_path)

    try:
        await _write_json(process, {"id": 1, "method": "initialize", "params": {}})
        initialize_response = await _read_json(process)
        assert initialize_response["id"] == 1

        await _write_json(process, {"method": "initialized", "params": {}})

        start = time.perf_counter()
        await _write_json(
            process,
            {"id": 2, "method": "thread/start", "params": {"ephemeral": False, "cwd": "."}},
        )
        overloaded_response = await _read_json(process)
        elapsed = time.perf_counter() - start

        response_error = _require_dict(overloaded_response["error"])
        assert elapsed >= 0.05
        assert response_error["code"] == -32001
        assert response_error["message"] == "Server overloaded; retry later."
        assert await _read_line(process) == ""
    finally:
        stderr = await _finish_process(process)

    assert process.returncode == 0, stderr


@pytest.mark.asyncio
async def test_builder_script_can_emit_invalid_json_then_close(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        emit_invalid_json("{broken-json"),
        close_connection(),
    )
    script_path = tmp_path / "210_invalid_json_and_close.script.jsonl"
    script.write_jsonl(script_path)

    process = await _launch_fake_app_server(script_path)

    try:
        await _write_json(process, {"id": 1, "method": "initialize", "params": {}})
        initialize_response = await _read_json(process)
        assert initialize_response["id"] == 1

        await _write_json(process, {"method": "initialized", "params": {}})
        assert await _read_line(process) == "{broken-json"
        assert await _read_line(process) == ""
    finally:
        stderr = await _finish_process(process)

    assert process.returncode == 0, stderr


def test_script_fixture_loads_and_round_trips() -> None:
    script = load_fake_app_server_script(FIXTURE_SCRIPT)

    assert script.actions
    assert script.actions[0]["action"] == "expect_request"
    assert "approval/requested" in script.to_jsonl()


async def _launch_fake_app_server(script_path: Path) -> Process:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        MODULE_NAME,
        "--script",
        str(script_path),
        stdin=PIPE,
        stdout=PIPE,
        stderr=PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    return process


async def _write_json(process: Process, envelope: dict[str, Any]) -> None:
    stdin = process.stdin
    assert stdin is not None
    stdin.write(json.dumps(envelope).encode("utf-8") + b"\n")
    await stdin.drain()


async def _read_json(process: Process) -> dict[str, Any]:
    line = await _read_line(process)
    assert line, "expected a JSON message, got EOF"
    payload = json.loads(line)
    assert isinstance(payload, dict), f"expected JSON object, got {payload!r}"
    return cast(dict[str, Any], payload)


async def _read_line(process: Process) -> str:
    stdout = process.stdout
    assert stdout is not None
    raw_line = await asyncio.wait_for(stdout.readline(), timeout=IO_TIMEOUT_SECONDS)
    if raw_line == b"":
        return ""
    return raw_line.decode("utf-8").rstrip("\n")


async def _finish_process(process: Process) -> str:
    stdin = process.stdin
    if stdin is not None and not stdin.is_closing():
        stdin.close()
        await stdin.wait_closed()

    try:
        await asyncio.wait_for(process.wait(), timeout=IO_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await asyncio.wait_for(process.wait(), timeout=IO_TIMEOUT_SECONDS)

    stderr = process.stderr
    assert stderr is not None
    return (await stderr.read()).decode("utf-8")


def _require_dict(value: Any) -> dict[str, Any]:
    assert isinstance(value, dict), f"expected dict, got {value!r}"
    return cast(dict[str, Any], value)

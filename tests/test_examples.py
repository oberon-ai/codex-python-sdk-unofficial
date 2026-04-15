from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from codex_agent_sdk.testing import (
    FakeAppServerScript,
    expect_notification,
    expect_request,
    expect_response,
    send_notification,
    send_response,
    send_server_request,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_ROOT = REPO_ROOT / "examples"
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
FAKE_SERVER_MODULE = "codex_agent_sdk.testing.fake_app_server"


def test_workspace_brief_runs_without_required_args(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized", params={}),
        expect_request(
            "thread/start",
            save_as="thread_start",
            params={
                "approvalPolicy": "never",
                "cwd": str(REPO_ROOT),
                "ephemeral": True,
                "sandbox": "read-only",
            },
        ),
        send_response(
            request_ref="thread_start",
            result=_build_thread_start_result(thread_id="thread_workspace", cwd=str(REPO_ROOT)),
        ),
        expect_request(
            "turn/start",
            save_as="turn_start",
            params={
                "approvalPolicy": "never",
                "cwd": str(REPO_ROOT),
                "input": [{"type": "text", "text": _workspace_prompt()}],
                "sandboxPolicy": {"type": "readOnly"},
                "threadId": "thread_workspace",
            },
        ),
        send_response(
            request_ref="turn_start",
            result=_build_turn_start_result(turn_id="turn_workspace"),
        ),
        send_notification(
            "turn/started",
            params={
                "threadId": "thread_workspace",
                "turn": _build_turn_payload(turn_id="turn_workspace", status="inProgress"),
            },
        ),
        send_notification(
            "item/agentMessage/delta",
            params={
                "delta": "Project summary.",
                "itemId": "item_workspace",
                "threadId": "thread_workspace",
                "turnId": "turn_workspace",
            },
        ),
        send_notification(
            "thread/tokenUsage/updated",
            params=_build_turn_token_usage_payload(
                thread_id="thread_workspace",
                turn_id="turn_workspace",
            ),
        ),
        send_notification(
            "turn/completed",
            params={
                "threadId": "thread_workspace",
                "turn": _build_turn_payload(turn_id="turn_workspace", status="completed"),
            },
        ),
    )
    fake_codex = _write_fake_codex_launcher(tmp_path, script, stem="codex")

    completed = _run_example(
        "workspace_brief.py",
        env_overrides={"PATH": f"{tmp_path}:{os.environ.get('PATH', '')}"},
        cwd=REPO_ROOT,
    )

    assert fake_codex.exists()
    assert completed.returncode == 0, completed.stderr
    assert "assistant> Project summary." in completed.stdout
    assert "status: completed" in completed.stdout
    assert "total tokens: 46" in completed.stdout


def test_file_brief_summarizes_a_text_file(tmp_path: Path) -> None:
    input_file = tmp_path / "notes.txt"
    input_file.write_text("Ship the SDK examples this week.\n", encoding="utf-8")

    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized", params={}),
        expect_request(
            "thread/start",
            save_as="thread_start",
            params={
                "approvalPolicy": "never",
                "cwd": "/repo",
                "ephemeral": True,
                "model": "gpt-5.4",
                "sandbox": "read-only",
            },
        ),
        send_response(
            request_ref="thread_start",
            result=_build_thread_start_result(thread_id="thread_file", cwd="/repo"),
        ),
        expect_request("turn/start", save_as="turn_start"),
        send_response(
            request_ref="turn_start",
            result=_build_turn_start_result(turn_id="turn_file"),
        ),
        send_notification(
            "turn/started",
            params={
                "threadId": "thread_file",
                "turn": _build_turn_payload(turn_id="turn_file", status="inProgress"),
            },
        ),
        send_notification(
            "item/agentMessage/delta",
            params={
                "delta": "This file tracks a short delivery goal.",
                "itemId": "item_file",
                "threadId": "thread_file",
                "turnId": "turn_file",
            },
        ),
        send_notification(
            "thread/tokenUsage/updated",
            params=_build_turn_token_usage_payload(
                thread_id="thread_file",
                turn_id="turn_file",
            ),
        ),
        send_notification(
            "turn/completed",
            params={
                "threadId": "thread_file",
                "turn": _build_turn_payload(turn_id="turn_file", status="completed"),
            },
        ),
    )
    launcher = _write_fake_codex_launcher(tmp_path, script, stem="file_brief_fake_codex.py")

    completed = _run_example(
        "file_brief.py",
        str(input_file),
        "--codex-bin",
        str(launcher),
        "--cwd",
        "/repo",
        "--model",
        "gpt-5.4",
    )

    assert completed.returncode == 0, completed.stderr
    assert "assistant> This file tracks a short delivery goal." in completed.stdout
    assert "status: completed" in completed.stdout
    assert "total tokens: 46" in completed.stdout


def test_file_brief_rejects_missing_path() -> None:
    completed = _run_example("file_brief.py", str(REPO_ROOT / "missing.txt"))

    assert completed.returncode == 1
    assert "file does not exist" in completed.stderr


def test_file_brief_rejects_directories(tmp_path: Path) -> None:
    completed = _run_example("file_brief.py", str(tmp_path))

    assert completed.returncode == 1
    assert "expected a file, got directory" in completed.stderr


def test_file_brief_rejects_binary_looking_input(tmp_path: Path) -> None:
    binary_file = tmp_path / "binary.bin"
    binary_file.write_bytes(b"abc\x00def")

    completed = _run_example("file_brief.py", str(binary_file))

    assert completed.returncode == 1
    assert "refusing binary-looking input" in completed.stderr


def test_file_brief_rejects_invalid_utf8(tmp_path: Path) -> None:
    invalid_file = tmp_path / "invalid.txt"
    invalid_file.write_bytes(b"\xff\xfe")

    completed = _run_example("file_brief.py", str(invalid_file))

    assert completed.returncode == 1
    assert "file is not valid UTF-8" in completed.stderr


def test_file_brief_rejects_oversized_input(tmp_path: Path) -> None:
    large_file = tmp_path / "large.txt"
    large_file.write_text("x" * 32, encoding="utf-8")

    completed = _run_example("file_brief.py", str(large_file), "--max-bytes", "10")

    assert completed.returncode == 1
    assert "file is too large" in completed.stderr


def test_interactive_thread_handles_one_turn_and_an_approval(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized", params={}),
        expect_request(
            "thread/start",
            save_as="thread_start",
            params={
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "cwd": "/repo",
                "ephemeral": True,
                "model": "gpt-5.4",
                "sandbox": "read-only",
            },
        ),
        send_response(
            request_ref="thread_start",
            result=_build_thread_start_result(
                thread_id="thread_interactive",
                cwd="/repo",
                approval_policy="on-request",
            ),
        ),
        expect_request(
            "turn/start",
            save_as="turn_start",
            params={
                "threadId": "thread_interactive",
                "input": [{"type": "text", "text": "Hello there"}],
            },
        ),
        send_response(
            request_ref="turn_start",
            result=_build_turn_start_result(turn_id="turn_interactive"),
        ),
        send_notification(
            "turn/started",
            params={
                "threadId": "thread_interactive",
                "turn": _build_turn_payload(turn_id="turn_interactive", status="inProgress"),
            },
        ),
        send_server_request(
            "item/commandExecution/requestApproval",
            request_id="approval-interactive-1",
            params={
                "threadId": "thread_interactive",
                "turnId": "turn_interactive",
                "itemId": "item_command_interactive",
                "command": ["pytest", "-q"],
                "cwd": "/repo",
                "reason": "Run a targeted test command.",
            },
        ),
        expect_response(
            request_ref="approval-interactive-1",
            result={"decision": "accept"},
        ),
        send_notification(
            "serverRequest/resolved",
            params={
                "threadId": "thread_interactive",
                "requestId": "approval-interactive-1",
            },
        ),
        send_notification(
            "item/agentMessage/delta",
            params={
                "delta": "Let's explore the repo.",
                "itemId": "item_agent_interactive",
                "threadId": "thread_interactive",
                "turnId": "turn_interactive",
            },
        ),
        send_notification(
            "thread/tokenUsage/updated",
            params=_build_turn_token_usage_payload(
                thread_id="thread_interactive",
                turn_id="turn_interactive",
            ),
        ),
        send_notification(
            "turn/completed",
            params={
                "threadId": "thread_interactive",
                "turn": _build_turn_payload(turn_id="turn_interactive", status="completed"),
            },
        ),
    )
    launcher = _write_fake_codex_launcher(tmp_path, script, stem="interactive_fake_codex.py")

    completed = _run_example(
        "interactive_thread.py",
        "--codex-bin",
        str(launcher),
        "--cwd",
        "/repo",
        "--model",
        "gpt-5.4",
        input_text="Hello there\na\n/quit\n",
    )

    assert completed.returncode == 0, completed.stderr
    assert "thread> thread_interactive" in completed.stdout
    assert "Approval requested." in completed.stdout
    assert "command: pytest -q" in completed.stdout
    assert "assistant> Let's explore the repo." in completed.stdout
    assert "status: completed" in completed.stdout
    assert "bye" in completed.stdout


def _run_example(
    script_name: str,
    *args: str,
    input_text: str | None = None,
    env_overrides: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    if env_overrides is not None:
        env.update(env_overrides)

    return subprocess.run(
        [str(VENV_PYTHON), str(EXAMPLES_ROOT / script_name), *args],
        input=input_text,
        text=True,
        capture_output=True,
        cwd=str(REPO_ROOT if cwd is None else cwd),
        env=env,
        timeout=5,
        check=False,
    )


def _build_thread_payload(*, thread_id: str, cwd: str) -> dict[str, object]:
    return {
        "cliVersion": "codex-cli 0.118.0",
        "createdAt": 1_710_000_000,
        "cwd": cwd,
        "ephemeral": True,
        "id": thread_id,
        "modelProvider": "openai",
        "preview": "Example preview",
        "source": "appServer",
        "status": {"type": "idle"},
        "turns": [],
        "updatedAt": 1_710_000_001,
    }


def _build_thread_start_result(
    *,
    thread_id: str,
    cwd: str,
    approval_policy: str = "never",
) -> dict[str, object]:
    return {
        "approvalPolicy": approval_policy,
        "approvalsReviewer": "user",
        "cwd": cwd,
        "model": "gpt-5.4",
        "modelProvider": "openai",
        "sandbox": {"type": "readOnly"},
        "thread": _build_thread_payload(thread_id=thread_id, cwd=cwd),
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


def _workspace_prompt() -> str:
    return textwrap.dedent(
        """\
        Create a concise workspace brief for the current project.

        Cover:
        1. what kind of project this is
        2. the most important modules, entrypoints, or workflows you notice
        3. one sensible next step for a new contributor

        Keep it concrete and easy to scan.
        """
    )


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

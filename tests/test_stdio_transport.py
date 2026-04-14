from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path
from typing import Any, cast

import pytest

from codex_agent_sdk import (
    CodexNotFoundError,
    MessageDecodeError,
    StartupError,
    StartupTimeoutError,
    TransportClosedError,
)
from codex_agent_sdk.options import AppServerConfig
from codex_agent_sdk.transport import StdioTransport

IO_TIMEOUT_SECONDS = 1.0


def test_build_command_keeps_stdio_transport_explicit() -> None:
    config = AppServerConfig(
        codex_bin="/tmp/codex",
        extra_args=("--trace", "json"),
    )

    command = StdioTransport.build_command(config)

    assert command == (
        "/tmp/codex",
        "app-server",
        "--listen",
        "stdio://",
        "--trace",
        "json",
    )


@pytest.mark.asyncio
async def test_start_launches_process_with_cwd_env_and_metadata(tmp_path: Path) -> None:
    workdir = tmp_path / "process-cwd"
    workdir.mkdir()
    script = _write_executable_script(
        tmp_path / "fake_codex.py",
        """
        import json
        import os
        import sys

        payload = {
            "argv": sys.argv[1:],
            "cwd": os.getcwd(),
            "probe": os.environ.get("TRANSPORT_PROBE"),
            "pid": os.getpid(),
        }
        print(json.dumps(payload), flush=True)
        sys.stdin.read()
        """,
    )

    transport = StdioTransport(
        AppServerConfig(
            codex_bin=str(script),
            cwd=str(workdir),
            env={"TRANSPORT_PROBE": "enabled"},
            extra_args=("--probe-flag",),
        )
    )

    async with transport:
        info = transport.info
        assert info.command == (
            str(script),
            "app-server",
            "--listen",
            "stdio://",
            "--probe-flag",
        )
        assert info.cwd == str(workdir)
        assert info.pid is not None
        assert info.returncode is None
        assert transport.stderr_tail is None

        stdout = transport.stdout
        assert stdout is not None
        line = await asyncio.wait_for(stdout.readline(), timeout=IO_TIMEOUT_SECONDS)
        payload = cast(dict[str, Any], json.loads(line.decode("utf-8")))

        assert payload["argv"] == ["app-server", "--listen", "stdio://", "--probe-flag"]
        assert payload["cwd"] == str(workdir)
        assert payload["probe"] == "enabled"
        assert payload["pid"] == info.pid


@pytest.mark.asyncio
async def test_start_raises_rich_not_found_error_for_missing_binary(tmp_path: Path) -> None:
    missing_binary = tmp_path / "missing-codex"
    transport = StdioTransport(
        AppServerConfig(
            codex_bin=str(missing_binary),
            cwd=str(tmp_path),
        )
    )

    with pytest.raises(CodexNotFoundError) as exc_info:
        await transport.start()

    error = exc_info.value
    assert error.path == str(missing_binary)
    assert error.command == (
        str(missing_binary),
        "app-server",
        "--listen",
        "stdio://",
    )
    assert error.cwd == str(tmp_path)
    assert "command=" in str(error)


@pytest.mark.asyncio
async def test_start_raises_startup_error_for_early_process_exit(tmp_path: Path) -> None:
    script = _write_executable_script(
        tmp_path / "failing_codex.py",
        """
        import sys

        sys.stderr.write("synthetic startup failure\\n")
        sys.stderr.flush()
        raise SystemExit(17)
        """,
    )
    transport = StdioTransport(
        AppServerConfig(
            codex_bin=str(script),
            extra_args=("--fail-fast",),
        )
    )

    with pytest.raises(StartupError) as exc_info:
        await transport.start()

    error = exc_info.value
    assert error.exit_code == 17
    assert error.command == (
        str(script),
        "app-server",
        "--listen",
        "stdio://",
        "--fail-fast",
    )
    assert error.stderr_tail is not None
    assert "synthetic startup failure" in error.stderr_tail
    assert "synthetic startup failure" in str(error)
    assert transport.process is None


@pytest.mark.asyncio
async def test_start_uses_configured_startup_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow_exec(*args: object, **kwargs: object) -> Any:
        await asyncio.sleep(0.1)
        raise AssertionError("launcher should time out before spawn returns")

    monkeypatch.setattr(
        "codex_agent_sdk.transport.stdio.asyncio.create_subprocess_exec",
        slow_exec,
    )
    transport = StdioTransport(
        AppServerConfig(
            codex_bin="codex",
            startup_timeout=0.01,
        )
    )

    with pytest.raises(StartupTimeoutError) as exc_info:
        await transport.start()

    error = exc_info.value
    assert error.timeout_seconds == 0.01
    assert error.command == ("codex", "app-server", "--listen", "stdio://")


@pytest.mark.asyncio
async def test_read_stdout_envelope_decodes_chunked_jsonl_frames(tmp_path: Path) -> None:
    script = _write_executable_script(
        tmp_path / "chunked_codex.py",
        """
        import sys
        import time

        parts = [
            '{"id":1,',
            '"method":"thread/started",',
            '"params":{"threadId":"thread_123"}}',
            "\\n",
        ]
        for part in parts:
            sys.stdout.write(part)
            sys.stdout.flush()
            time.sleep(0.02)
        sys.stdin.read()
        """,
    )
    transport = StdioTransport(
        AppServerConfig(codex_bin=str(script)),
        stdout_read_chunk_bytes=5,
    )

    async with transport:
        envelope = await asyncio.wait_for(
            transport.read_stdout_envelope(),
            timeout=IO_TIMEOUT_SECONDS,
        )

    assert envelope == {
        "id": 1,
        "method": "thread/started",
        "params": {"threadId": "thread_123"},
    }


@pytest.mark.asyncio
async def test_read_stdout_envelope_raises_decode_error_for_invalid_json(tmp_path: Path) -> None:
    script = _write_executable_script(
        tmp_path / "invalid_json_codex.py",
        """
        import sys
        import time

        sys.stderr.write("reader probe stderr\\n")
        sys.stderr.flush()
        time.sleep(0.02)
        sys.stdout.write("{broken-json\\n")
        sys.stdout.flush()
        sys.stdin.read()
        """,
    )
    transport = StdioTransport(AppServerConfig(codex_bin=str(script)))

    async with transport:
        with pytest.raises(MessageDecodeError) as exc_info:
            await asyncio.wait_for(
                transport.read_stdout_envelope(),
                timeout=IO_TIMEOUT_SECONDS,
            )

    error = exc_info.value
    assert error.line == "{broken-json"
    assert error.stderr_tail is not None
    assert "reader probe stderr" in error.stderr_tail
    assert "reader probe stderr" in str(error)


@pytest.mark.asyncio
async def test_read_stdout_line_raises_decode_error_for_invalid_utf8(tmp_path: Path) -> None:
    script = _write_executable_script(
        tmp_path / "invalid_utf8_codex.py",
        """
        import sys

        sys.stdout.buffer.write(b"\\xff\\n")
        sys.stdout.buffer.flush()
        sys.stdin.read()
        """,
    )
    transport = StdioTransport(AppServerConfig(codex_bin=str(script)))

    async with transport:
        with pytest.raises(MessageDecodeError) as exc_info:
            await asyncio.wait_for(transport.read_stdout_line(), timeout=IO_TIMEOUT_SECONDS)

    error = exc_info.value
    assert isinstance(error.original_error, UnicodeDecodeError)
    assert error.line == "\ufffd"


@pytest.mark.asyncio
async def test_read_stdout_envelope_returns_none_on_clean_eof_between_frames(
    tmp_path: Path,
) -> None:
    script = _write_executable_script(
        tmp_path / "clean_eof_codex.py",
        """
        import json
        import sys
        import time

        print(json.dumps({"id": 7, "result": {"ok": True}}), flush=True)
        sys.stdout.close()
        time.sleep(0.3)
        """,
    )
    transport = StdioTransport(AppServerConfig(codex_bin=str(script)))

    async with transport:
        envelope = await asyncio.wait_for(
            transport.read_stdout_envelope(),
            timeout=IO_TIMEOUT_SECONDS,
        )
        eof_message = await asyncio.wait_for(
            transport.read_stdout_envelope(),
            timeout=IO_TIMEOUT_SECONDS,
        )

    assert envelope == {"id": 7, "result": {"ok": True}}
    assert eof_message is None


@pytest.mark.asyncio
async def test_read_stdout_line_raises_transport_closed_for_midframe_eof(tmp_path: Path) -> None:
    script = _write_executable_script(
        tmp_path / "partial_eof_codex.py",
        """
        import sys
        import time

        sys.stdout.write('{"id":1')
        sys.stdout.flush()
        sys.stdout.close()
        time.sleep(0.3)
        """,
    )
    transport = StdioTransport(AppServerConfig(codex_bin=str(script)))

    async with transport:
        with pytest.raises(TransportClosedError) as exc_info:
            await asyncio.wait_for(transport.read_stdout_line(), timeout=IO_TIMEOUT_SECONDS)

    assert "partial JSONL frame" in str(exc_info.value)


@pytest.mark.asyncio
async def test_read_stdout_line_raises_decode_error_when_frame_exceeds_limit(
    tmp_path: Path,
) -> None:
    script = _write_executable_script(
        tmp_path / "large_frame_codex.py",
        """
        import sys

        payload = '{"payload":"' + ('x' * 80) + '"}\\n'
        sys.stdout.write(payload)
        sys.stdout.flush()
        sys.stdin.read()
        """,
    )
    transport = StdioTransport(
        AppServerConfig(codex_bin=str(script)),
        stdout_max_frame_bytes=32,
        stdout_read_chunk_bytes=8,
    )

    async with transport:
        with pytest.raises(MessageDecodeError) as exc_info:
            await asyncio.wait_for(transport.read_stdout_line(), timeout=IO_TIMEOUT_SECONDS)

    error = exc_info.value
    assert isinstance(error.original_error, ValueError)
    assert "exceeded 32 bytes" in str(error.original_error)
    assert error.line.startswith('{"payload":"')


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

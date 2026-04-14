from __future__ import annotations

import asyncio
import json
import logging
import sys
import textwrap
from pathlib import Path
from typing import Any, cast

import pytest

from codex_agent_sdk import (
    CodexNotFoundError,
    MessageDecodeError,
    ProcessExitError,
    StartupError,
    StartupTimeoutError,
    TransportClosedError,
    TransportWriteError,
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
        assert info.stderr_tail is None
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
async def test_read_stdout_envelope_raises_process_exit_error_after_unexpected_exit(
    tmp_path: Path,
) -> None:
    script = _write_executable_script(
        tmp_path / "unexpected_exit_codex.py",
        """
        import json
        import sys
        import time

        print(json.dumps({"id": 7, "result": {"ok": True}}), flush=True)
        sys.stderr.write("unexpected exit after response\\n")
        sys.stderr.flush()
        time.sleep(0.3)
        raise SystemExit(23)
        """,
    )
    transport = StdioTransport(AppServerConfig(codex_bin=str(script)))

    async with transport:
        envelope = await asyncio.wait_for(
            transport.read_stdout_envelope(),
            timeout=IO_TIMEOUT_SECONDS,
        )

        with pytest.raises(ProcessExitError) as exc_info:
            await asyncio.wait_for(
                transport.read_stdout_envelope(),
                timeout=IO_TIMEOUT_SECONDS,
            )

    error = exc_info.value
    assert envelope == {"id": 7, "result": {"ok": True}}
    assert error.exit_code == 23
    assert error.stderr_tail is not None
    assert "unexpected exit after response" in error.stderr_tail
    assert transport.process_exit_error is error
    assert transport.info.returncode == 23


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


@pytest.mark.asyncio
async def test_write_stdin_envelope_writes_one_compact_jsonl_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _write_executable_script(
        tmp_path / "stdin_reader_codex.py",
        """
        import sys

        sys.stdin.read()
        """,
    )
    transport = StdioTransport(AppServerConfig(codex_bin=str(script)))

    async with transport:
        stdin = transport.stdin
        assert stdin is not None

        writes: list[bytes] = []
        drain_calls = 0
        writer_type = type(stdin)
        original_write = writer_type.write
        original_drain = writer_type.drain

        def recording_write(
            self: asyncio.StreamWriter,
            data: bytes | bytearray | memoryview,
        ) -> None:
            nonlocal writes
            if self is stdin:
                writes.append(bytes(data))
            original_write(self, data)

        async def recording_drain(self: asyncio.StreamWriter) -> None:
            nonlocal drain_calls
            if self is stdin:
                drain_calls += 1
            await original_drain(self)

        monkeypatch.setattr(writer_type, "write", recording_write)
        monkeypatch.setattr(writer_type, "drain", recording_drain)

        await transport.write_stdin_envelope(
            {
                "id": 1,
                "method": "thread/start",
                "params": {"cwd": ".", "includeHidden": False},
            }
        )

    assert writes == [
        b'{"id":1,"method":"thread/start","params":{"cwd":".","includeHidden":false}}\n'
    ]
    assert drain_calls == 1


@pytest.mark.asyncio
async def test_debug_logging_records_redacted_lifecycle_and_frame_metadata(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    script = _write_executable_script(
        tmp_path / "debug_logging_codex.py",
        """
        import json
        import sys

        print(
            json.dumps(
                {
                    "method": "item/updated",
                    "params": {
                        "text": "agent text that should stay redacted",
                        "path": "/private/repo/secret.txt",
                        "diff": "@@ -1 +1 @@\\n-secret\\n+visible",
                    },
                }
            ),
            flush=True,
        )
        sys.stdin.read()
        """,
    )
    logger = logging.getLogger("tests.codex_agent_sdk.transport_debug")
    caplog.set_level(logging.DEBUG, logger=logger.name)

    prompt = "Find the risky diff in this repository." * 6
    transport = StdioTransport(
        AppServerConfig(
            codex_bin=str(script),
            env={"MODE": "test", "API_TOKEN": "super-secret"},
            debug_logging=True,
            debug_logger=logger,
        )
    )

    async with transport:
        await transport.write_stdin_envelope(
            {
                "id": 11,
                "method": "turn/start",
                "params": {
                    "prompt": prompt,
                    "cwd": "/Users/kevin/private-repo",
                    "env": {"OPENAI_API_KEY": "shh", "MODE": "test"},
                },
            }
        )
        envelope = await asyncio.wait_for(
            transport.read_stdout_envelope(),
            timeout=IO_TIMEOUT_SECONDS,
        )

    assert envelope is not None
    assert envelope["method"] == "item/updated"

    records = [record for record in caplog.records if record.name == logger.name]
    assert [_record_extra(record, "codex_debug_event") for record in records] == [
        "transport_starting",
        "transport_started",
        "jsonrpc_frame",
        "jsonrpc_frame",
        "transport_closing",
        "transport_closed",
    ]

    start_record = records[0]
    assert _record_extra(start_record, "codex_command") == (
        script.name,
        "app-server",
        "--listen",
        "stdio://",
    )
    assert _record_extra(start_record, "codex_cwd_set") is False
    assert _record_extra(start_record, "codex_env_override_keys") == (
        "<redacted-env-key>",
        "MODE",
    )

    outbound_record = records[2]
    assert _record_extra(outbound_record, "codex_direction") == "outbound"
    assert _record_extra(outbound_record, "codex_kind") == "request"
    assert _record_extra(outbound_record, "codex_request_id") == 11
    assert _record_extra(outbound_record, "codex_method") == "turn/start"
    outbound_preview = cast(
        dict[str, object],
        _record_extra(outbound_record, "codex_frame_preview"),
    )
    outbound_params = cast(dict[str, object], outbound_preview["params"])
    assert outbound_params["prompt"] == f"<redacted prompt len={len(prompt)}>"
    assert cast(str, outbound_params["cwd"]).startswith("<redacted cwd len=")
    assert outbound_params["env"] == "<redacted env keys=2>"

    inbound_record = records[3]
    assert _record_extra(inbound_record, "codex_direction") == "inbound"
    assert _record_extra(inbound_record, "codex_kind") == "notification"
    assert _record_extra(inbound_record, "codex_request_id") is None
    assert _record_extra(inbound_record, "codex_method") == "item/updated"
    inbound_preview = cast(
        dict[str, object],
        _record_extra(inbound_record, "codex_frame_preview"),
    )
    inbound_params = cast(dict[str, object], inbound_preview["params"])
    assert cast(str, inbound_params["text"]).startswith("<redacted text len=")
    assert cast(str, inbound_params["path"]).startswith("<redacted path len=")
    assert cast(str, inbound_params["diff"]).startswith("<redacted diff len=")

    close_record = records[-1]
    assert _record_extra(close_record, "codex_returncode") == 0


@pytest.mark.asyncio
async def test_debug_logging_is_silent_when_disabled(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    script = _write_executable_script(
        tmp_path / "disabled_debug_logging_codex.py",
        """
        import sys

        sys.stdin.read()
        """,
    )
    logger = logging.getLogger("tests.codex_agent_sdk.transport_debug.disabled")
    caplog.set_level(logging.DEBUG, logger=logger.name)

    transport = StdioTransport(
        AppServerConfig(
            codex_bin=str(script),
            debug_logging=False,
            debug_logger=logger,
        )
    )

    async with transport:
        pass

    assert [record for record in caplog.records if record.name == logger.name] == []


@pytest.mark.asyncio
async def test_write_stdin_envelope_serializes_concurrent_callers_until_first_drain_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _write_executable_script(
        tmp_path / "stdin_reader_codex.py",
        """
        import sys

        sys.stdin.read()
        """,
    )
    transport = StdioTransport(AppServerConfig(codex_bin=str(script)))

    first_frame = b'{"id":1,"method":"thread/start"}\n'
    second_frame = b'{"id":2,"method":"turn/start"}\n'

    async with transport:
        stdin = transport.stdin
        assert stdin is not None

        writes: list[bytes] = []
        drain_calls = 0
        first_drain_started = asyncio.Event()
        release_first_drain = asyncio.Event()
        writer_type = type(stdin)
        original_write = writer_type.write
        original_drain = writer_type.drain

        def recording_write(
            self: asyncio.StreamWriter,
            data: bytes | bytearray | memoryview,
        ) -> None:
            if self is stdin:
                writes.append(bytes(data))
            original_write(self, data)

        async def gated_drain(self: asyncio.StreamWriter) -> None:
            nonlocal drain_calls
            if self is stdin:
                drain_calls += 1
                if drain_calls == 1:
                    first_drain_started.set()
                    await release_first_drain.wait()
            await original_drain(self)

        monkeypatch.setattr(writer_type, "write", recording_write)
        monkeypatch.setattr(writer_type, "drain", gated_drain)

        first_task = asyncio.create_task(
            transport.write_stdin_envelope({"id": 1, "method": "thread/start"})
        )
        await asyncio.wait_for(first_drain_started.wait(), timeout=IO_TIMEOUT_SECONDS)

        second_task = asyncio.create_task(
            transport.write_stdin_envelope({"id": 2, "method": "turn/start"})
        )
        await asyncio.sleep(0.05)

        assert writes == [first_frame]

        release_first_drain.set()
        await asyncio.gather(first_task, second_task)

    assert writes == [first_frame, second_frame]
    assert drain_calls == 2


@pytest.mark.asyncio
async def test_write_stdin_envelope_raises_after_transport_close(tmp_path: Path) -> None:
    script = _write_executable_script(
        tmp_path / "stdin_reader_codex.py",
        """
        import sys

        sys.stdin.read()
        """,
    )
    transport = StdioTransport(AppServerConfig(codex_bin=str(script)))

    await transport.start()
    await transport.close()

    with pytest.raises(TransportWriteError) as exc_info:
        await transport.write_stdin_envelope({"id": 1, "method": "thread/start"})

    assert "after transport close" in str(exc_info.value)


@pytest.mark.asyncio
async def test_write_stdin_envelope_raises_after_process_exit(tmp_path: Path) -> None:
    script = _write_executable_script(
        tmp_path / "exiting_codex.py",
        """
        import sys
        import time

        time.sleep(0.3)
        sys.stderr.write("writer target exited\\n")
        sys.stderr.flush()
        raise SystemExit(9)
        """,
    )
    transport = StdioTransport(AppServerConfig(codex_bin=str(script)))

    async with transport:
        process = transport.process
        assert process is not None
        await asyncio.wait_for(process.wait(), timeout=IO_TIMEOUT_SECONDS)

        with pytest.raises(ProcessExitError) as exc_info:
            await transport.write_stdin_envelope({"id": 1, "method": "thread/start"})

    error = exc_info.value
    assert error.exit_code == 9
    assert error.stderr_tail is not None
    assert "writer target exited" in error.stderr_tail
    assert transport.process_exit_error is error


@pytest.mark.asyncio
async def test_close_waits_for_clean_process_exit_without_escalation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _write_executable_script(
        tmp_path / "clean_shutdown_codex.py",
        """
        import sys

        sys.stdin.read()
        sys.stderr.write("graceful shutdown observed\\n")
        sys.stderr.flush()
        """,
    )
    transport = StdioTransport(AppServerConfig(codex_bin=str(script), shutdown_timeout=0.5))

    await transport.start()
    process = transport.process
    assert process is not None

    terminate_calls = 0
    kill_calls = 0
    process_type = type(process)
    original_terminate = process_type.terminate
    original_kill = process_type.kill

    def recording_terminate(self: Any) -> None:
        nonlocal terminate_calls
        if self is process:
            terminate_calls += 1
        original_terminate(self)

    def recording_kill(self: Any) -> None:
        nonlocal kill_calls
        if self is process:
            kill_calls += 1
        original_kill(self)

    monkeypatch.setattr(process_type, "terminate", recording_terminate)
    monkeypatch.setattr(process_type, "kill", recording_kill)

    await transport.close()

    assert terminate_calls == 0
    assert kill_calls == 0
    assert transport.process is None
    assert transport.returncode == 0
    assert transport.process_exit_error is None
    assert transport.info.stderr_tail is not None
    assert "graceful shutdown observed" in transport.info.stderr_tail


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="SIGTERM handling in this escalation test is POSIX-specific",
)
async def test_close_escalates_to_kill_when_process_ignores_stdin_eof_and_sigterm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _write_executable_script(
        tmp_path / "hung_shutdown_codex.py",
        """
        import signal
        import sys
        import time

        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        sys.stdin.read()
        sys.stderr.write("ignoring shutdown\\n")
        sys.stderr.flush()
        while True:
            time.sleep(0.1)
        """,
    )
    transport = StdioTransport(AppServerConfig(codex_bin=str(script), shutdown_timeout=0.6))

    await transport.start()
    process = transport.process
    assert process is not None

    terminate_calls = 0
    kill_calls = 0
    process_type = type(process)
    original_terminate = process_type.terminate
    original_kill = process_type.kill

    def recording_terminate(self: Any) -> None:
        nonlocal terminate_calls
        if self is process:
            terminate_calls += 1
        original_terminate(self)

    def recording_kill(self: Any) -> None:
        nonlocal kill_calls
        if self is process:
            kill_calls += 1
        original_kill(self)

    monkeypatch.setattr(process_type, "terminate", recording_terminate)
    monkeypatch.setattr(process_type, "kill", recording_kill)

    await transport.close()

    assert terminate_calls == 1
    assert kill_calls == 1
    assert transport.process is None
    assert transport.returncode is not None
    assert transport.info.stderr_tail is not None
    assert "ignoring shutdown" in transport.info.stderr_tail


@pytest.mark.asyncio
async def test_close_records_nonzero_exit_observed_during_shutdown(tmp_path: Path) -> None:
    script = _write_executable_script(
        tmp_path / "shutdown_failure_codex.py",
        """
        import sys

        sys.stdin.read()
        sys.stderr.write("shutdown failure\\n")
        sys.stderr.flush()
        raise SystemExit(5)
        """,
    )
    transport = StdioTransport(AppServerConfig(codex_bin=str(script), shutdown_timeout=0.5))

    await transport.start()
    await transport.close()

    error = transport.process_exit_error
    assert error is not None
    assert error.exit_code == 5
    assert error.stderr_tail is not None
    assert "shutdown failure" in error.stderr_tail
    assert transport.info.returncode == 5
    assert transport.info.stderr_tail is not None
    assert "shutdown failure" in transport.info.stderr_tail


@pytest.mark.asyncio
async def test_close_is_idempotent_for_concurrent_and_repeated_callers(tmp_path: Path) -> None:
    script = _write_executable_script(
        tmp_path / "idempotent_close_codex.py",
        """
        import sys

        sys.stdin.read()
        """,
    )
    transport = StdioTransport(AppServerConfig(codex_bin=str(script), shutdown_timeout=0.5))

    await transport.start()
    await asyncio.gather(transport.close(), transport.close())
    await transport.close()

    assert transport.process is None
    assert transport.returncode == 0


@pytest.mark.asyncio
async def test_write_stdin_envelope_cancellation_during_drain_closes_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _write_executable_script(
        tmp_path / "stdin_reader_codex.py",
        """
        import sys

        sys.stdin.read()
        """,
    )
    transport = StdioTransport(AppServerConfig(codex_bin=str(script)))

    async with transport:
        stdin = transport.stdin
        assert stdin is not None

        drain_entered = asyncio.Event()
        writer_type = type(stdin)
        original_drain = writer_type.drain

        async def stalled_drain(self: asyncio.StreamWriter) -> None:
            if self is stdin:
                drain_entered.set()
                await asyncio.Event().wait()
            await original_drain(self)

        monkeypatch.setattr(writer_type, "drain", stalled_drain)

        write_task = asyncio.create_task(
            transport.write_stdin_envelope({"id": 1, "method": "thread/start"})
        )
        await asyncio.wait_for(drain_entered.wait(), timeout=IO_TIMEOUT_SECONDS)
        write_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await write_task

        assert transport.process is None

        with pytest.raises(TransportWriteError):
            await transport.write_stdin_envelope({"id": 2, "method": "turn/start"})


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


def _record_extra(record: logging.LogRecord, key: str) -> object:
    return record.__dict__[key]

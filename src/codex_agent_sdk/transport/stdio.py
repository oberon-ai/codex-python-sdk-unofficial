"""Native-async subprocess transport for ``codex app-server`` over stdio."""

from __future__ import annotations

import asyncio
import os
from asyncio.subprocess import PIPE, Process
from collections import deque
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass

from ..errors import (
    CodexNotFoundError,
    MessageDecodeError,
    ProcessExitError,
    ShutdownTimeoutError,
    StartupError,
    StartupTimeoutError,
    TransportClosedError,
    TransportError,
    TransportWriteError,
)
from ..options import AppServerConfig
from ..rpc import JsonRpcEnvelope, parse_jsonrpc_envelope, serialize_jsonrpc_envelope

APP_SERVER_SUBCOMMAND = "app-server"
DEFAULT_CODEX_BIN = "codex"
DEFAULT_STDERR_TAIL_LINES = 200
DEFAULT_STDOUT_MAX_FRAME_BYTES = 1_048_576
DEFAULT_STDOUT_READ_CHUNK_BYTES = 16_384
STARTUP_EXIT_GRACE_PERIOD_SECONDS = 0.2
STDIO_LISTEN_URI = "stdio://"


@dataclass(frozen=True, slots=True)
class StdioTransportInfo:
    """Snapshot of the subprocess metadata currently known to the transport."""

    command: tuple[str, ...]
    cwd: str | None
    pid: int | None
    returncode: int | None
    stderr_tail: str | None


class StdioTransport:
    """Own the ``codex app-server`` subprocess and its stdio pipes."""

    def __init__(
        self,
        config: AppServerConfig | None = None,
        *,
        stderr_tail_lines: int = DEFAULT_STDERR_TAIL_LINES,
        stdout_max_frame_bytes: int = DEFAULT_STDOUT_MAX_FRAME_BYTES,
        stdout_read_chunk_bytes: int = DEFAULT_STDOUT_READ_CHUNK_BYTES,
    ) -> None:
        if stderr_tail_lines <= 0:
            raise ValueError("stderr_tail_lines must be positive")
        if stdout_max_frame_bytes <= 0:
            raise ValueError("stdout_max_frame_bytes must be positive")
        if stdout_read_chunk_bytes <= 0:
            raise ValueError("stdout_read_chunk_bytes must be positive")

        self.config = config or AppServerConfig()
        self._stderr_lines: deque[str] = deque(maxlen=stderr_tail_lines)
        self._stderr_task: asyncio.Task[None] | None = None
        self._process: Process | None = None
        self._last_pid: int | None = None
        self._last_returncode: int | None = None
        self._process_exit_error: ProcessExitError | None = None
        self._stdout_buffer = bytearray()
        self._stdout_eof = False
        self._stdout_max_frame_bytes = stdout_max_frame_bytes
        self._stdout_read_chunk_bytes = stdout_read_chunk_bytes
        self._stdout_read_lock = asyncio.Lock()
        self._stdin_write_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._close_requested = False

    async def __aenter__(self) -> StdioTransport:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        await self.close()

    @staticmethod
    def build_command(config: AppServerConfig) -> tuple[str, ...]:
        """Build the explicit argv used to launch ``codex app-server``."""

        codex_bin = config.codex_bin or DEFAULT_CODEX_BIN
        return (
            codex_bin,
            APP_SERVER_SUBCOMMAND,
            "--listen",
            STDIO_LISTEN_URI,
            *config.extra_args,
        )

    @staticmethod
    def build_environment(
        config: AppServerConfig,
        *,
        base_env: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Merge explicit environment overrides over a base environment."""

        merged = dict(os.environ if base_env is None else base_env)
        if config.env:
            merged.update(config.env)
        return merged

    @property
    def command(self) -> tuple[str, ...]:
        return self.build_command(self.config)

    @property
    def environment(self) -> dict[str, str]:
        return self.build_environment(self.config)

    @property
    def cwd(self) -> str | None:
        return self.config.cwd

    @property
    def process(self) -> Process | None:
        return self._process

    @property
    def stdin(self) -> asyncio.StreamWriter | None:
        process = self._process
        return None if process is None else process.stdin

    @property
    def stdout(self) -> asyncio.StreamReader | None:
        process = self._process
        return None if process is None else process.stdout

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None else self._last_pid

    @property
    def returncode(self) -> int | None:
        process = self._process
        if process is not None and process.returncode is not None:
            return process.returncode
        return self._last_returncode

    @property
    def info(self) -> StdioTransportInfo:
        return StdioTransportInfo(
            command=self.command,
            cwd=self.cwd,
            pid=self.pid,
            returncode=self.returncode,
            stderr_tail=self.stderr_tail,
        )

    @property
    def stderr_lines(self) -> tuple[str, ...]:
        return tuple(self._stderr_lines)

    @property
    def stderr_tail(self) -> str | None:
        if not self._stderr_lines:
            return None
        return "\n".join(self._stderr_lines)

    @property
    def process_exit_error(self) -> ProcessExitError | None:
        error = self._process_exit_error
        if error is not None:
            return error

        process = self._process
        if process is None or self._close_requested or process.returncode in (None, 0):
            return None

        error = ProcessExitError(
            exit_code=process.returncode,
            stderr_tail=self.stderr_tail,
        )
        self._process_exit_error = error
        self._last_returncode = process.returncode
        return error

    @property
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.returncode is None

    async def start(self) -> StdioTransport:
        """Launch the subprocess and retain ownership of its stdio pipes."""

        close_task = self._close_task
        if close_task is not None:
            await asyncio.shield(close_task)

        if self.is_running:
            return self

        command = self.command
        env = self.environment
        cwd = self.cwd
        self._close_requested = False
        self._stderr_lines.clear()
        self._last_returncode = None
        self._process_exit_error = None
        self._stdout_buffer.clear()
        self._stdout_eof = False

        try:
            process = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *command,
                    stdin=PIPE,
                    stdout=PIPE,
                    stderr=PIPE,
                    cwd=cwd,
                    env=env,
                ),
                timeout=self.config.startup_timeout,
            )
        except TimeoutError as exc:
            raise StartupTimeoutError(
                timeout_seconds=self.config.startup_timeout,
                command=command,
                cwd=cwd,
            ) from exc
        except FileNotFoundError as exc:
            if cwd is not None and exc.filename == cwd:
                raise StartupError(
                    f"app-server working directory not found: {cwd}",
                    command=command,
                    cwd=cwd,
                ) from exc
            raise CodexNotFoundError(
                exc.filename or command[0],
                command=command,
                cwd=cwd,
            ) from exc
        except NotADirectoryError as exc:
            raise StartupError(
                f"app-server working directory is not a directory: {cwd}",
                command=command,
                cwd=cwd,
            ) from exc
        except OSError as exc:
            raise StartupError(
                f"failed to launch app-server: {exc.strerror or str(exc)}",
                command=command,
                cwd=cwd,
            ) from exc

        self._process = process
        self._last_pid = process.pid
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(),
            name="codex-agent-sdk.stderr-drain",
        )

        try:
            await self._probe_for_early_exit(command=command, cwd=cwd)
        except BaseException:
            await self._cleanup_failed_start()
            raise

        return self

    async def read_stdout_line(self) -> str | None:
        """Read the next decoded JSONL frame from stdout.

        Returns ``None`` only for clean EOF between frames. If stdout closes while
        a frame is still in progress, the transport raises ``TransportClosedError``
        so callers do not confuse protocol breakage with graceful shutdown.
        """

        async with self._stdout_read_lock:
            frame = await self._read_stdout_frame_locked()
            if frame is None:
                return None
            return _decode_stdout_frame(frame, stderr_tail=self.stderr_tail)

    async def read_stdout_envelope(self) -> JsonRpcEnvelope | None:
        """Read and parse the next raw JSON-RPC envelope from stdout."""

        line = await self.read_stdout_line()
        if line is None:
            return None
        return parse_jsonrpc_envelope(line, stderr_tail=self.stderr_tail)

    async def write_stdin_envelope(self, envelope: JsonRpcEnvelope) -> None:
        """Serialize and flush one JSON-RPC envelope to stdin as exactly one frame."""

        frame = _encode_stdin_frame(envelope)

        async with self._stdin_write_lock:
            stdin = await self._require_stdin_for_write_locked()

            try:
                stdin.write(frame)
                await stdin.drain()
            except asyncio.CancelledError:
                await asyncio.shield(self._close_after_write_failure())
                raise
            except (BrokenPipeError, ConnectionResetError, OSError, RuntimeError) as exc:
                error = TransportWriteError(
                    "failed to write JSON-RPC envelope to app-server stdin",
                    stderr_tail=self.stderr_tail,
                    exit_code=self.returncode,
                    original_error=exc,
                )
                await self._close_after_write_failure()
                raise error from exc

    async def close(self) -> None:
        """Terminate the subprocess and wait for local cleanup."""

        async with self._close_lock:
            close_task = self._close_task
            if close_task is None:
                process = self._process
                stderr_task = self._stderr_task

                if process is None and stderr_task is None:
                    return

                self._close_requested = True
                close_task = asyncio.create_task(
                    self._close_impl(process=process, stderr_task=stderr_task),
                    name="codex-agent-sdk.transport-close",
                )
                self._close_task = close_task

        assert close_task is not None
        try:
            await asyncio.shield(close_task)
        finally:
            if close_task.done():
                async with self._close_lock:
                    if self._close_task is close_task:
                        self._close_task = None

    async def _probe_for_early_exit(
        self,
        *,
        command: tuple[str, ...],
        cwd: str | None,
    ) -> None:
        grace_period = min(self.config.startup_timeout, STARTUP_EXIT_GRACE_PERIOD_SECONDS)
        process = self._process
        if process is None:
            return

        if grace_period <= 0:
            if process.returncode is None:
                return
        else:
            wait_task = asyncio.create_task(process.wait())
            done, pending = await asyncio.wait({wait_task}, timeout=grace_period)
            if pending:
                wait_task.cancel()
                with suppress(asyncio.CancelledError):
                    await wait_task
                return

        await self._await_stderr_task(self._stderr_task)
        raise StartupError(
            "app-server exited during startup",
            command=command,
            cwd=cwd,
            exit_code=process.returncode,
            stderr_tail=self.stderr_tail,
        )

    async def _cleanup_failed_start(self) -> None:
        process = self._process
        stderr_task = self._stderr_task

        self._process = None
        self._stderr_task = None
        self._close_requested = False

        if process is not None:
            stdin = process.stdin
            if stdin is not None and not stdin.is_closing():
                stdin.close()
                with suppress(BrokenPipeError, ConnectionResetError):
                    await stdin.wait_closed()
            if process.returncode is None:
                process.kill()
                await process.wait()
            self._last_returncode = process.returncode

        await self._await_stderr_task(stderr_task)

    async def _require_stdin_for_write_locked(self) -> asyncio.StreamWriter:
        process = self._process
        if process is None or self._close_requested:
            raise TransportWriteError(
                "cannot write to app-server stdin after transport close",
                stderr_tail=self.stderr_tail,
                exit_code=self.returncode,
            )
        if process.returncode is not None:
            self._last_returncode = process.returncode
            if process.returncode != 0:
                raise await self._build_process_exit_error(process.returncode)
            raise TransportWriteError(
                "cannot write to app-server stdin after process exit",
                stderr_tail=self.stderr_tail,
                exit_code=process.returncode,
            )

        stdin = process.stdin
        if stdin is None or stdin.is_closing():
            raise TransportWriteError(
                "app-server stdin is not available for writing",
                stderr_tail=self.stderr_tail,
                exit_code=self.returncode,
            )
        return stdin

    async def _close_after_write_failure(self) -> None:
        with suppress(TransportError, OSError, RuntimeError):
            await self.close()

    async def _close_impl(
        self,
        *,
        process: Process | None,
        stderr_task: asyncio.Task[None] | None,
    ) -> None:
        if process is None:
            if stderr_task is not None:
                await self._await_stderr_task(stderr_task)
                if self._stderr_task is stderr_task:
                    self._stderr_task = None
            return

        escalated = False
        shutdown_timeout = self.config.shutdown_timeout

        try:
            await self._close_stdin(process)

            if not await self._wait_for_process_exit(process, timeout=shutdown_timeout):
                escalated = True
                with suppress(ProcessLookupError):
                    process.terminate()

                if not await self._wait_for_process_exit(process, timeout=shutdown_timeout):
                    with suppress(ProcessLookupError):
                        process.kill()

                    if not await self._wait_for_process_exit(process, timeout=shutdown_timeout):
                        raise ShutdownTimeoutError(
                            timeout_seconds=shutdown_timeout,
                            stderr_tail=self.stderr_tail,
                        )

            self._last_returncode = process.returncode
            await self._await_stderr_task(stderr_task)

            if process.returncode not in (None, 0) and not escalated:
                await self._build_process_exit_error(process.returncode)
        finally:
            if process.returncode is not None:
                self._last_returncode = process.returncode
            if process.returncode is not None and self._process is process:
                self._process = None
            if process.returncode is not None and self._stderr_task is stderr_task:
                self._stderr_task = None

    async def _close_stdin(self, process: Process) -> None:
        stdin = process.stdin
        if stdin is None or stdin.is_closing():
            return

        stdin.close()
        with suppress(BrokenPipeError, ConnectionResetError, RuntimeError):
            await stdin.wait_closed()

    async def _wait_for_process_exit(self, process: Process, *, timeout: float) -> bool:
        if process.returncode is not None:
            self._last_returncode = process.returncode
            return True

        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except TimeoutError:
            return False

        self._last_returncode = process.returncode
        return True

    async def _build_process_exit_error(self, exit_code: int | None) -> ProcessExitError:
        await self._await_stderr_task(self._stderr_task)

        error = self._process_exit_error
        if error is None:
            error = ProcessExitError(
                exit_code=exit_code,
                stderr_tail=self.stderr_tail,
            )
            self._process_exit_error = error
        return error

    async def _read_stdout_frame_locked(self) -> bytes | None:
        stdout = self.stdout
        if stdout is None:
            raise TransportClosedError(
                "app-server stdout is not available",
                stderr_tail=self.stderr_tail,
            )

        while True:
            newline_index = self._stdout_buffer.find(b"\n")
            if newline_index >= 0:
                frame = bytes(self._stdout_buffer[:newline_index])
                del self._stdout_buffer[: newline_index + 1]
                if frame.endswith(b"\r"):
                    frame = frame[:-1]
                return frame

            if self._stdout_eof:
                if self._stdout_buffer:
                    raise TransportClosedError(
                        "app-server transport closed with a partial JSONL frame on stdout",
                        stderr_tail=self.stderr_tail,
                    )
                process_exit_error = await self._get_process_exit_error_for_eof()
                if process_exit_error is not None:
                    raise process_exit_error
                return None

            chunk = await stdout.read(self._stdout_read_chunk_bytes)
            if chunk == b"":
                self._stdout_eof = True
                continue

            self._stdout_buffer.extend(chunk)
            if len(self._stdout_buffer) > self._stdout_max_frame_bytes:
                preview = _decode_frame_preview(bytes(self._stdout_buffer))
                size_error = ValueError(
                    "app-server JSONL frame exceeded "
                    f"{self._stdout_max_frame_bytes} bytes without a newline"
                )
                raise MessageDecodeError(
                    preview,
                    original_error=size_error,
                    stderr_tail=self.stderr_tail,
                ) from size_error

    async def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return

        while True:
            line = await process.stderr.readline()
            if line == b"":
                return
            self._stderr_lines.append(line.decode("utf-8", errors="replace").rstrip("\n"))

    async def _await_stderr_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None:
            return
        with suppress(asyncio.CancelledError):
            await task

    async def _get_process_exit_error_for_eof(self) -> ProcessExitError | None:
        if self._close_requested:
            return self._process_exit_error

        process = self._process
        if process is None or process.returncode in (None, 0):
            return self._process_exit_error

        return await self._build_process_exit_error(process.returncode)


def _decode_stdout_frame(frame: bytes, *, stderr_tail: str | None) -> str:
    try:
        return frame.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MessageDecodeError(
            _decode_frame_preview(frame),
            original_error=exc,
            stderr_tail=stderr_tail,
        ) from exc


def _decode_frame_preview(frame: bytes) -> str:
    return frame[:160].decode("utf-8", errors="replace")


def _encode_stdin_frame(envelope: JsonRpcEnvelope) -> bytes:
    return (serialize_jsonrpc_envelope(envelope) + "\n").encode("utf-8")


__all__ = [
    "APP_SERVER_SUBCOMMAND",
    "DEFAULT_CODEX_BIN",
    "DEFAULT_STDERR_TAIL_LINES",
    "DEFAULT_STDOUT_MAX_FRAME_BYTES",
    "DEFAULT_STDOUT_READ_CHUNK_BYTES",
    "STDIO_LISTEN_URI",
    "StdioTransport",
    "StdioTransportInfo",
]

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
    ShutdownTimeoutError,
    StartupError,
    StartupTimeoutError,
)
from ..options import AppServerConfig

APP_SERVER_SUBCOMMAND = "app-server"
DEFAULT_CODEX_BIN = "codex"
DEFAULT_STDERR_TAIL_LINES = 200
STARTUP_EXIT_GRACE_PERIOD_SECONDS = 0.2
STDIO_LISTEN_URI = "stdio://"


@dataclass(frozen=True, slots=True)
class StdioTransportInfo:
    """Snapshot of the subprocess metadata currently known to the transport."""

    command: tuple[str, ...]
    cwd: str | None
    pid: int | None
    returncode: int | None


class StdioTransport:
    """Own the ``codex app-server`` subprocess and its stdio pipes."""

    def __init__(
        self,
        config: AppServerConfig | None = None,
        *,
        stderr_tail_lines: int = DEFAULT_STDERR_TAIL_LINES,
    ) -> None:
        if stderr_tail_lines <= 0:
            raise ValueError("stderr_tail_lines must be positive")

        self.config = config or AppServerConfig()
        self._stderr_lines: deque[str] = deque(maxlen=stderr_tail_lines)
        self._stderr_task: asyncio.Task[None] | None = None
        self._process: Process | None = None
        self._last_pid: int | None = None
        self._last_returncode: int | None = None

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
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.returncode is None

    async def start(self) -> StdioTransport:
        """Launch the subprocess and retain ownership of its stdio pipes."""

        if self.is_running:
            return self

        command = self.command
        env = self.environment
        cwd = self.cwd
        self._stderr_lines.clear()
        self._last_returncode = None

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

    async def close(self) -> None:
        """Terminate the subprocess and wait for local cleanup."""

        process = self._process
        stderr_task = self._stderr_task
        self._process = None
        self._stderr_task = None

        if process is None:
            if stderr_task is not None:
                await self._await_stderr_task(stderr_task)
            return

        stdin = process.stdin
        if stdin is not None and not stdin.is_closing():
            stdin.close()
            with suppress(BrokenPipeError, ConnectionResetError):
                await stdin.wait_closed()

        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=self.config.shutdown_timeout)
            except TimeoutError:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=self.config.shutdown_timeout)
                except TimeoutError as inner_exc:
                    await self._await_stderr_task(stderr_task)
                    raise ShutdownTimeoutError(
                        timeout_seconds=self.config.shutdown_timeout,
                        stderr_tail=self.stderr_tail,
                    ) from inner_exc

        self._last_returncode = process.returncode
        await self._await_stderr_task(stderr_task)

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


__all__ = [
    "APP_SERVER_SUBCOMMAND",
    "DEFAULT_CODEX_BIN",
    "DEFAULT_STDERR_TAIL_LINES",
    "STDIO_LISTEN_URI",
    "StdioTransport",
    "StdioTransportInfo",
]

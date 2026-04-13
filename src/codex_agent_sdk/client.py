"""Public client entry points for the Codex SDK.

``AppServerClient`` is the low-level JSON-RPC escape hatch. ``CodexSDKClient``
is the stateful high-level thread client. Their concrete behavior lands in
later tasks, but the public names live here from the start.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from .approvals import ApprovalHandler
from .events import TurnEvent
from .options import AppServerConfig, CodexOptions
from .results import TurnHandle


class AppServerClient:
    """Low-level native-async client for ``codex app-server`` over stdio."""

    def __init__(self, config: AppServerConfig | None = None) -> None:
        self.config = config or AppServerConfig()

    async def __aenter__(self) -> AppServerClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying app-server connection."""

    async def initialize(self) -> object:
        """Perform the required initialize then initialized handshake."""

        raise NotImplementedError("App-server initialization is not implemented yet.")

    async def request(self, method: str, params: object | None = None) -> object:
        """Send a raw JSON-RPC request over the app-server connection."""

        raise NotImplementedError("Raw JSON-RPC requests are not implemented yet.")

    async def notify(self, method: str, params: object | None = None) -> None:
        """Send a raw JSON-RPC notification over the app-server connection."""

        raise NotImplementedError("Raw JSON-RPC notifications are not implemented yet.")

    def iter_notifications(self) -> AsyncIterator[object]:
        """Iterate raw JSON-RPC notifications from the server."""

        raise NotImplementedError("Notification streaming is not implemented yet.")

    def iter_server_requests(self) -> AsyncIterator[object]:
        """Iterate raw server-initiated JSON-RPC requests."""

        raise NotImplementedError("Server-request streaming is not implemented yet.")

    async def thread_start(self, **params: Any) -> object:
        """Start a new app-server thread."""

        raise NotImplementedError("Thread start helpers are not implemented yet.")

    async def thread_resume(self, **params: Any) -> object:
        """Resume an existing app-server thread."""

        raise NotImplementedError("Thread resume helpers are not implemented yet.")

    async def thread_fork(self, **params: Any) -> object:
        """Fork an app-server thread."""

        raise NotImplementedError("Thread fork helpers are not implemented yet.")

    async def turn_start(self, **params: Any) -> object:
        """Start a turn on the active thread."""

        raise NotImplementedError("Turn start helpers are not implemented yet.")

    async def turn_steer(self, **params: Any) -> object:
        """Steer an in-flight turn."""

        raise NotImplementedError("Turn steering helpers are not implemented yet.")

    async def turn_interrupt(self, **params: Any) -> None:
        """Interrupt an in-flight turn."""

        raise NotImplementedError("Turn interruption helpers are not implemented yet.")


class CodexSDKClient:
    """High-level stateful client for thread and turn workflows."""

    def __init__(
        self,
        options: CodexOptions | None = None,
        app_server: AppServerConfig | None = None,
        approval_handler: ApprovalHandler | None = None,
    ) -> None:
        self.options = options or CodexOptions()
        self.app_server = app_server or AppServerConfig()
        self.approval_handler = approval_handler
        self.thread_id: str | None = None
        self.active_turn_id: str | None = None
        self.thread_status: str | None = None

    async def __aenter__(self) -> CodexSDKClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying app-server connection for this client."""

    async def start_thread(
        self,
        *,
        options: CodexOptions | None = None,
        ephemeral: bool = False,
    ) -> str:
        """Start a new thread and make it the active thread."""

        raise NotImplementedError("Thread start flows are not implemented yet.")

    async def resume_thread(
        self,
        thread_id: str,
        *,
        options: CodexOptions | None = None,
    ) -> str:
        """Resume an existing thread and make it the active thread."""

        raise NotImplementedError("Thread resume flows are not implemented yet.")

    async def fork_thread(
        self,
        thread_id: str | None = None,
        *,
        options: CodexOptions | None = None,
        ephemeral: bool = False,
    ) -> str:
        """Fork a thread and make the new branch the active thread."""

        raise NotImplementedError("Thread fork flows are not implemented yet.")

    async def query(
        self,
        prompt: str | list[object],
        *,
        options: CodexOptions | None = None,
        output_schema: dict[str, object] | None = None,
    ) -> TurnHandle:
        """Start a turn on the active thread and return its handle."""

        raise NotImplementedError("High-level turn queries are not implemented yet.")

    async def steer(
        self,
        prompt: str | list[object],
        *,
        expected_turn_id: str | None = None,
    ) -> str:
        """Append steering input to the active turn."""

        raise NotImplementedError("Turn steering is not implemented yet.")

    async def interrupt(
        self,
        *,
        turn_id: str | None = None,
    ) -> None:
        """Interrupt the active turn or a specified turn."""

        raise NotImplementedError("Turn interruption is not implemented yet.")

    def receive_turn_events(
        self,
        *,
        turn_id: str | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """Return the canonical event stream for a turn."""

        raise NotImplementedError("Turn event streaming is not implemented yet.")

    def receive_response(
        self,
        *,
        turn_id: str | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """Compatibility alias for ``receive_turn_events()``."""

        return self.receive_turn_events(turn_id=turn_id)


__all__ = [
    "AppServerClient",
    "CodexSDKClient",
]

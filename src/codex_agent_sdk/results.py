"""Public turn-handle and turn-result abstractions.

These placeholders reserve the high-level turn objects described by the public
API contract while transport and routing work is still pending.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from .generated.stable import ThreadTokenUsage, Turn, TurnCompletedNotification, TurnError

if TYPE_CHECKING:
    from .events import TurnEvent

TurnEventIterator: TypeAlias = AsyncIterator["TurnEvent"]
TurnWaiter: TypeAlias = Callable[[], Awaitable["TurnResult"]]
TurnSteerer: TypeAlias = Callable[[str | list[object]], Awaitable[str]]
TurnInterrupter: TypeAlias = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class TurnResult:
    """Compact terminal summary returned for a finished turn."""

    thread_id: str
    turn_id: str
    status: str
    items: tuple[object, ...] = ()
    token_usage: object | None = None
    error: BaseException | None = None
    assistant_text: str | None = None
    structured_output: object | None = None


@dataclass(frozen=True, slots=True)
class TurnCompletion:
    """Low-level terminal turn payload plus the latest observed token usage."""

    completion: TurnCompletedNotification
    token_usage: ThreadTokenUsage | None = None

    @property
    def error(self) -> TurnError | None:
        return self.completion.turn.error

    @property
    def items(self) -> tuple[object, ...]:
        return tuple(self.completion.turn.items)

    @property
    def status(self) -> str:
        return self.completion.turn.status.value

    @property
    def thread_id(self) -> str:
        return self.completion.thread_id

    @property
    def turn(self) -> Turn:
        return self.completion.turn

    @property
    def turn_id(self) -> str:
        return self.completion.turn.id


class TurnHandle(AsyncIterator["TurnEvent"]):
    """Async turn handle returned by ``CodexSDKClient.query()``."""

    def __init__(
        self,
        *,
        thread_id: str,
        turn_id: str,
        event_iterator: TurnEventIterator | None = None,
        waiter: TurnWaiter | None = None,
        steerer: TurnSteerer | None = None,
        interrupter: TurnInterrupter | None = None,
    ) -> None:
        self.thread_id = thread_id
        self.turn_id = turn_id
        self._event_iterator = event_iterator
        self._waiter = waiter
        self._steerer = steerer
        self._interrupter = interrupter

    def __aiter__(self) -> TurnHandle:
        return self

    async def __anext__(self) -> TurnEvent:
        """Yield the next event for this turn."""

        if self._event_iterator is None:
            raise NotImplementedError(
                "Turn event iteration is not wired until the client layer exists."
            )
        return await self._event_iterator.__anext__()

    async def wait(self) -> TurnResult:
        """Wait for the terminal result of the turn."""

        if self._waiter is None:
            raise NotImplementedError("Turn waiting is not wired until the client layer exists.")
        return await self._waiter()

    async def steer(self, prompt: str | list[object]) -> str:
        """Append steering input to the active turn."""

        if self._steerer is None:
            raise NotImplementedError("Turn steering is not wired until the client layer exists.")
        return await self._steerer(prompt)

    async def interrupt(self) -> None:
        """Request interruption for the active turn."""

        if self._interrupter is None:
            raise NotImplementedError(
                "Turn interruption is not wired until the client layer exists."
            )
        await self._interrupter()


__all__ = [
    "TurnCompletion",
    "TurnHandle",
    "TurnResult",
]

"""Public one-shot query helper for the Codex SDK."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import TypeAlias

from .approvals import ApprovalHandler
from .client import AppServerClient, _stream_turn_events
from .events import TurnEvent
from .generated.stable import UserInput, UserInput1, UserInput2, UserInput3, UserInput4, UserInput5
from .options import AppServerConfig, CodexOptions

StructuredInputItem: TypeAlias = (
    UserInput
    | UserInput1
    | UserInput2
    | UserInput3
    | UserInput4
    | UserInput5
    | Mapping[str, object]
)
QueryPrompt: TypeAlias = str | StructuredInputItem | Sequence[StructuredInputItem]


async def query(
    *,
    prompt: QueryPrompt,
    options: CodexOptions | None = None,
    app_server: AppServerConfig | None = None,
    output_schema: Mapping[str, object] | None = None,
    approval_handler: ApprovalHandler | None = None,
) -> AsyncIterator[TurnEvent]:
    """Stream one turn from a temporary app-server client via the one-shot query helper.

    The helper accepts the common string prompt shorthand first. Advanced callers
    can pass one structured input item or a sequence of input items when they
    need explicit mentions, multimodal inputs, or other future protocol
    extensions without dropping to the low-level client.
    """

    effective_options = options or CodexOptions()
    effective_app_server = app_server or AppServerConfig()

    async with AppServerClient(effective_app_server) as client:
        client.set_approval_handler(approval_handler)
        await client.initialize()

        thread_response = await client.thread_start(
            approval_policy=effective_options.approval_policy,
            approvals_reviewer=effective_options.approvals_reviewer,
            base_instructions=effective_options.base_instructions,
            cwd=effective_options.cwd,
            developer_instructions=effective_options.developer_instructions,
            ephemeral=True,
            model=effective_options.model,
            personality=effective_options.personality,
            sandbox=effective_options.effective_sandbox_mode,
            service_tier=effective_options.service_tier,
        )
        thread_id = thread_response.thread.id
        notification_subscription = client.subscribe_thread_notifications(thread_id)
        server_request_subscription = client.subscribe_thread_server_requests(thread_id)

        try:
            turn_response = await client.turn_start(
                thread_id=thread_id,
                input=prompt,
                approval_policy=effective_options.approval_policy,
                approvals_reviewer=effective_options.approvals_reviewer,
                cwd=effective_options.cwd,
                effort=effective_options.effort,
                model=effective_options.model,
                output_schema=output_schema,
                personality=effective_options.personality,
                sandbox_policy=effective_options.effective_sandbox_policy,
                service_tier=effective_options.service_tier,
                summary=effective_options.summary,
            )
            turn_id = turn_response.turn.id

            async for event in _stream_turn_events(
                client,
                turn_id=turn_id,
                notifications=notification_subscription.iter_notifications(),
                notification_subscription=notification_subscription,
                server_requests=server_request_subscription.iter_requests(),
                server_request_subscription=server_request_subscription,
                close_message=(
                    "app-server connection closed before one-shot query completed "
                    f"for turn_id={turn_id!r}"
                ),
            ):
                yield event
        finally:
            notification_subscription.close()
            server_request_subscription.close()


__all__ = [
    "query",
]

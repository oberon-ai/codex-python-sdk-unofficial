from __future__ import annotations

import asyncio
import logging
import sys
import textwrap
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest

from codex_agent_sdk import (
    AlreadyInitializedError,
    AppServerClient,
    AppServerConfig,
    ClientStateError,
    DuplicateResponseError,
    InitializeResult,
    JsonRpcInvalidParamsError,
    JsonRpcServerError,
    NotInitializedError,
    RequestTimeoutError,
    ResponseValidationError,
    StartupTimeoutError,
    TransportClosedError,
    UnknownResponseIdError,
)
from codex_agent_sdk.generated.stable import (
    ApprovalsReviewer,
    AskForApproval,
    Personality,
    ReasoningEffort,
    ReasoningSummary,
    SandboxPolicy,
    ServiceTier,
    ThreadArchiveResponse,
    ThreadForkResponse,
    ThreadListResponse,
    ThreadReadResponse,
    ThreadResumeResponse,
    ThreadSetNameResponse,
    ThreadSortKey,
    ThreadSourceKind,
    ThreadStartParams,
    ThreadStartResponse,
    ThreadUnarchiveResponse,
    TurnInterruptResponse,
    TurnStartResponse,
    TurnSteerResponse,
    UserInput,
)
from codex_agent_sdk.rpc import JsonRpcNotification, JsonRpcRequest
from codex_agent_sdk.testing import (
    FakeAppServerScript,
    close_connection,
    emit_raw,
    expect_notification,
    expect_request,
    expect_response,
    send_notification,
    send_response,
    send_server_request,
    sleep_action,
)

IO_TIMEOUT_SECONDS = 1.0
FAKE_SERVER_MODULE = "codex_agent_sdk.testing.fake_app_server"


@pytest.mark.asyncio
async def test_initialize_sends_typed_params_and_returns_typed_result(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request(
            "initialize",
            save_as="initialize",
            params={
                "clientInfo": {
                    "name": "custom_codex_client",
                    "title": "Custom Codex Client",
                    "version": "1.2.3",
                },
                "capabilities": {
                    "experimentalApi": True,
                    "optOutNotificationMethods": [
                        "thread/started",
                        "item/agentMessage/delta",
                    ],
                },
            },
        ),
        send_response(
            request_ref="initialize",
            result={
                "protocolVersion": 2,
                "serverInfo": {
                    "name": "fake-codex-app-server",
                    "version": "0.0.0-test",
                },
                "capabilities": {"experimentalApi": False},
                "codexHome": "/tmp/fake-codex-home",
                "platformFamily": "unix",
                "platformOs": "linux",
                "userAgent": "fake-codex/0.0.0-test",
            },
        ),
        expect_notification("initialized", params={}),
    )
    launcher = _write_fake_codex_launcher(tmp_path, script, stem="initialize_details_launcher.py")

    async with AppServerClient(
        AppServerConfig(
            codex_bin=str(launcher),
            client_name="custom_codex_client",
            client_title="Custom Codex Client",
            client_version="1.2.3",
            experimental_api=True,
            opt_out_notification_methods=(
                "thread/started",
                "item/agentMessage/delta",
            ),
        )
    ) as client:
        initialize_result = await client.initialize()

        assert isinstance(initialize_result, InitializeResult)
        assert client.is_initialized is True
        assert client.initialize_result is initialize_result
        assert initialize_result.protocol_version == 2
        assert initialize_result.codex_home == "/tmp/fake-codex-home"
        assert initialize_result.platform_family == "unix"
        assert initialize_result.platform_os == "linux"
        assert initialize_result.user_agent == "fake-codex/0.0.0-test"
        assert initialize_result.server_info is not None
        assert initialize_result.server_info.name == "fake-codex-app-server"
        assert initialize_result.server_info.version == "0.0.0-test"
        assert initialize_result.capabilities is not None
        assert initialize_result.capabilities.experimental_api is False


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
async def test_request_before_initialize_is_blocked_locally(tmp_path: Path) -> None:
    client = AppServerClient(AppServerConfig(codex_bin=str(tmp_path / "missing-codex-binary")))

    try:
        with pytest.raises(NotInitializedError) as exc_info:
            await client.request("thread/start", {"ephemeral": True})
    finally:
        await client.close()

    assert exc_info.value.method == "thread/start"


@pytest.mark.asyncio
async def test_raw_handshake_methods_are_reserved(tmp_path: Path) -> None:
    client = AppServerClient(AppServerConfig(codex_bin=str(tmp_path / "missing-codex-binary")))

    try:
        with pytest.raises(ClientStateError, match="use AppServerClient.initialize"):
            await client.request("initialize", {"clientInfo": {"name": "raw"}})

        with pytest.raises(ClientStateError, match="sent automatically"):
            await client.notify("initialized", {})
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_repeated_initialize_is_rejected_locally_after_success(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized", params={}),
    )
    launcher = _write_fake_codex_launcher(tmp_path, script, stem="repeat_initialize_launcher.py")

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        first = await client.initialize()

        with pytest.raises(AlreadyInitializedError):
            await client.initialize()

    assert first.protocol_version == 2


@pytest.mark.asyncio
async def test_concurrent_initialize_calls_share_one_handshake(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        sleep_action(50),
        send_response(
            request_ref="initialize",
            result={"protocolVersion": 2, "codexHome": "/tmp/fake-codex-home"},
        ),
        expect_notification("initialized", params={}),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="concurrent_initialize_launcher.py",
    )
    client = AppServerClient(AppServerConfig(codex_bin=str(launcher)))

    try:
        first_task = asyncio.create_task(client.initialize())
        await asyncio.sleep(0.01)
        second_task = asyncio.create_task(client.initialize())

        first, second = await asyncio.gather(first_task, second_task)
    finally:
        await client.close()

    assert first is second
    assert first.protocol_version == 2
    assert first.codex_home == "/tmp/fake-codex-home"


@pytest.mark.asyncio
async def test_initialize_response_validation_failure_is_fatal(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(
            request_ref="initialize",
            result={"serverInfo": {"name": "fake-codex-app-server"}},
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="invalid_initialize_response_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        with pytest.raises(ResponseValidationError) as exc_info:
            await client.initialize()

        assert client.is_initialized is False
        assert client.initialize_result is None

    assert exc_info.value.method == "initialize"


@pytest.mark.asyncio
async def test_typed_request_serializes_model_params_and_returns_typed_model(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "thread/start",
            save_as="thread_start",
            params={
                "cwd": "/repo",
                "ephemeral": True,
                "model": "gpt-5.4",
            },
        ),
        send_response(
            request_ref="thread_start",
            result=_build_thread_start_result(),
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_request_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        result = await client.request(
            "thread/start",
            ThreadStartParams(
                cwd="/repo",
                ephemeral=True,
                model="gpt-5.4",
            ),
            response_model=ThreadStartResponse,
        )

    assert isinstance(result, ThreadStartResponse)
    assert result.model_dump()["approvalPolicy"] == "on-request"
    assert result.thread.id == "thread_123"
    assert result.thread.model_provider == "openai"


@pytest.mark.asyncio
async def test_typed_request_can_explicitly_return_raw_dict_escape_hatch(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request("thread/start", save_as="thread_start", params={"ephemeral": True}),
        send_response(
            request_ref="thread_start",
            result=_build_thread_start_result(),
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="raw_dict_response_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        result = await client.request(
            "thread/start",
            {"ephemeral": True},
            response_model=dict,
        )

    assert isinstance(result, dict)
    assert result["thread"]["id"] == "thread_123"
    assert result["modelProvider"] == "openai"


@pytest.mark.asyncio
async def test_typed_request_validation_failure_keeps_method_and_payload(
    tmp_path: Path,
) -> None:
    invalid_result = {"approvalPolicy": "on-request"}
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request("thread/start", save_as="thread_start", params={"ephemeral": True}),
        send_response(
            request_ref="thread_start",
            result=invalid_result,
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_request_validation_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        with pytest.raises(ResponseValidationError) as exc_info:
            await client.request(
                "thread/start",
                {"ephemeral": True},
                response_model=ThreadStartResponse,
            )

    assert exc_info.value.method == "thread/start"
    assert exc_info.value.payload == invalid_result


@pytest.mark.asyncio
async def test_typed_request_preserves_jsonrpc_error_mapping_and_request_id(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request("thread/start", save_as="thread_start", params={"ephemeral": True}),
        send_response(
            request_ref="thread_start",
            error={
                "code": -32602,
                "message": "bad thread/start params",
            },
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_request_error_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        with pytest.raises(JsonRpcInvalidParamsError) as exc_info:
            await client.request(
                "thread/start",
                {"ephemeral": True},
                response_model=ThreadStartResponse,
            )

    assert exc_info.value.method == "thread/start"
    assert exc_info.value.request_id == 2


@pytest.mark.asyncio
async def test_thread_start_wrapper_sends_expected_wire_shape_and_returns_typed_response(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "thread/start",
            save_as="thread_start",
            params={
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "cwd": "/repo",
                "developerInstructions": "Use pytest",
                "ephemeral": True,
                "model": "gpt-5.4",
                "modelProvider": "openai",
                "serviceName": "codex-cli",
            },
        ),
        send_response(
            request_ref="thread_start",
            result=_build_thread_start_result(),
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_wrapper_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        result = await client.thread_start(
            approval_policy=AskForApproval.model_validate("on-request"),
            approvals_reviewer=ApprovalsReviewer.user,
            cwd="/repo",
            developer_instructions="Use pytest",
            ephemeral=True,
            model="gpt-5.4",
            model_provider="openai",
            service_name="codex-cli",
        )

    assert isinstance(result, ThreadStartResponse)
    assert result.thread.id == "thread_123"


@pytest.mark.asyncio
async def test_thread_resume_wrapper_sends_expected_wire_shape_and_returns_typed_response(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "thread/resume",
            save_as="thread_resume",
            params={
                "approvalPolicy": "on-request",
                "approvalsReviewer": "guardian_subagent",
                "cwd": "/repo/resumed",
                "developerInstructions": "Resume carefully",
                "model": "gpt-5.5",
                "modelProvider": "openai",
                "threadId": "thread_resumed",
            },
        ),
        send_response(
            request_ref="thread_resume",
            result=_build_thread_start_result(thread_id="thread_resumed"),
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_resume_wrapper_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        result = await client.thread_resume(
            thread_id="thread_resumed",
            approval_policy=AskForApproval.model_validate("on-request"),
            approvals_reviewer=ApprovalsReviewer.guardian_subagent,
            cwd="/repo/resumed",
            developer_instructions="Resume carefully",
            model="gpt-5.5",
            model_provider="openai",
        )

    assert isinstance(result, ThreadResumeResponse)
    assert result.thread.id == "thread_resumed"


@pytest.mark.asyncio
async def test_thread_fork_wrapper_sends_expected_wire_shape_and_returns_typed_response(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "thread/fork",
            save_as="thread_fork",
            params={
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "cwd": "/repo/forked",
                "developerInstructions": "Fork carefully",
                "ephemeral": False,
                "model": "gpt-5.4",
                "modelProvider": "openai",
                "threadId": "thread_parent",
            },
        ),
        send_response(
            request_ref="thread_fork",
            result=_build_thread_start_result(thread_id="thread_forked"),
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_fork_wrapper_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        result = await client.thread_fork(
            thread_id="thread_parent",
            approval_policy=AskForApproval.model_validate("on-request"),
            approvals_reviewer=ApprovalsReviewer.user,
            cwd="/repo/forked",
            developer_instructions="Fork carefully",
            ephemeral=False,
            model="gpt-5.4",
            model_provider="openai",
        )

    assert isinstance(result, ThreadForkResponse)
    assert result.thread.id == "thread_forked"


@pytest.mark.asyncio
async def test_thread_list_wrapper_supports_pagination_params_and_returns_typed_response(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "thread/list",
            save_as="thread_list",
            params={
                "archived": False,
                "cursor": "cursor_123",
                "cwd": "/repo",
                "limit": 2,
                "modelProviders": ["openai", "azure"],
                "searchTerm": "failing",
                "sortKey": "updated_at",
                "sourceKinds": ["appServer", "cli"],
            },
        ),
        send_response(
            request_ref="thread_list",
            result={
                "data": [
                    _build_thread_payload(thread_id="thread_1"),
                    _build_thread_payload(thread_id="thread_2"),
                ],
                "nextCursor": "cursor_456",
            },
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_list_wrapper_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        result = await client.thread_list(
            archived=False,
            cursor="cursor_123",
            cwd="/repo",
            limit=2,
            model_providers=["openai", "azure"],
            search_term="failing",
            sort_key=ThreadSortKey.updated_at,
            source_kinds=[ThreadSourceKind.app_server, ThreadSourceKind.cli],
        )

    assert isinstance(result, ThreadListResponse)
    assert [thread.id for thread in result.data] == ["thread_1", "thread_2"]
    assert result.next_cursor == "cursor_456"


@pytest.mark.asyncio
async def test_thread_read_wrapper_supports_include_turns_and_returns_typed_response(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "thread/read",
            save_as="thread_read",
            params={
                "includeTurns": True,
                "threadId": "thread_123",
            },
        ),
        send_response(
            request_ref="thread_read",
            result={"thread": _build_thread_payload(thread_id="thread_123")},
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_read_wrapper_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        result = await client.thread_read(
            thread_id="thread_123",
            include_turns=True,
        )

    assert isinstance(result, ThreadReadResponse)
    assert result.thread.id == "thread_123"


@pytest.mark.asyncio
async def test_thread_archive_wrapper_sends_expected_wire_shape_and_returns_typed_response(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "thread/archive",
            save_as="thread_archive",
            params={"threadId": "thread_archived"},
        ),
        send_response(request_ref="thread_archive", result={}),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_archive_wrapper_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        result = await client.thread_archive(thread_id="thread_archived")

    assert isinstance(result, ThreadArchiveResponse)
    assert result.model_dump() == {}


@pytest.mark.asyncio
async def test_thread_unarchive_wrapper_sends_expected_wire_shape_and_returns_typed_response(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "thread/unarchive",
            save_as="thread_unarchive",
            params={"threadId": "thread_unarchived"},
        ),
        send_response(
            request_ref="thread_unarchive",
            result={"thread": _build_thread_payload(thread_id="thread_unarchived")},
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_unarchive_wrapper_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        result = await client.thread_unarchive(thread_id="thread_unarchived")

    assert isinstance(result, ThreadUnarchiveResponse)
    assert result.thread.id == "thread_unarchived"


@pytest.mark.asyncio
async def test_thread_set_name_wrapper_sends_expected_wire_shape_and_returns_typed_response(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "thread/name/set",
            save_as="thread_set_name",
            params={
                "name": "Investigation Scratchpad",
                "threadId": "thread_named",
            },
        ),
        send_response(request_ref="thread_set_name", result={}),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_set_name_wrapper_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        result = await client.thread_set_name(
            thread_id="thread_named",
            name="Investigation Scratchpad",
        )

    assert isinstance(result, ThreadSetNameResponse)
    assert result.model_dump() == {}


@pytest.mark.asyncio
async def test_turn_start_wrapper_coerces_text_input_and_returns_promptly(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "turn/start",
            save_as="turn_start",
            params={
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "cwd": "/repo",
                "effort": "high",
                "input": [{"type": "text", "text": "Find the failing tests."}],
                "model": "gpt-5.4",
                "outputSchema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                    },
                    "required": ["summary"],
                    "additionalProperties": False,
                },
                "personality": "pragmatic",
                "sandboxPolicy": {
                    "type": "workspaceWrite",
                    "networkAccess": True,
                    "writableRoots": ["/repo"],
                },
                "serviceTier": "flex",
                "summary": "detailed",
                "threadId": "thread_turn_text",
            },
        ),
        send_response(
            request_ref="turn_start",
            result=_build_turn_start_result(turn_id="turn_text"),
        ),
        sleep_action(300),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_turn_start_text_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        started_at = asyncio.get_running_loop().time()
        result = await client.turn_start(
            thread_id="thread_turn_text",
            input="Find the failing tests.",
            approval_policy=AskForApproval.model_validate("on-request"),
            approvals_reviewer=ApprovalsReviewer.user,
            cwd="/repo",
            effort=ReasoningEffort.high,
            model="gpt-5.4",
            output_schema={
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
                "additionalProperties": False,
            },
            personality=Personality.pragmatic,
            sandbox_policy=SandboxPolicy.model_validate(
                {
                    "type": "workspaceWrite",
                    "networkAccess": True,
                    "writableRoots": ["/repo"],
                }
            ),
            service_tier=ServiceTier.flex,
            summary=ReasoningSummary.model_validate("detailed"),
        )
        elapsed = asyncio.get_running_loop().time() - started_at

    assert isinstance(result, TurnStartResponse)
    assert result.turn.id == "turn_text"
    assert result.turn.status == "inProgress"
    assert elapsed < 0.2


@pytest.mark.asyncio
async def test_turn_start_wrapper_accepts_structured_input_items(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "turn/start",
            save_as="turn_start",
            params={
                "threadId": "thread_turn_items",
                "input": [
                    {"type": "mention", "name": "github", "path": "app://github"},
                    {"type": "text", "text": "Review the latest PR."},
                    {"type": "localImage", "path": "/tmp/screenshot.png"},
                ],
            },
        ),
        send_response(
            request_ref="turn_start",
            result=_build_turn_start_result(turn_id="turn_items"),
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_turn_start_items_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        result = await client.turn_start(
            thread_id="thread_turn_items",
            input=[
                UserInput.model_validate(
                    {"type": "mention", "name": "github", "path": "app://github"}
                ),
                {"type": "text", "text": "Review the latest PR."},
                {"type": "localImage", "path": "/tmp/screenshot.png"},
            ],
        )

    assert isinstance(result, TurnStartResponse)
    assert result.turn.id == "turn_items"


@pytest.mark.asyncio
async def test_turn_start_wrapper_accepts_one_structured_input_item(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "turn/start",
            save_as="turn_start",
            params={
                "threadId": "thread_turn_single_item",
                "input": [{"type": "text", "text": "Focus on the smallest diff."}],
            },
        ),
        send_response(
            request_ref="turn_start",
            result=_build_turn_start_result(turn_id="turn_single_item"),
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_turn_start_single_item_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        result = await client.turn_start(
            thread_id="thread_turn_single_item",
            input={"type": "text", "text": "Focus on the smallest diff."},
        )

    assert isinstance(result, TurnStartResponse)
    assert result.turn.id == "turn_single_item"


@pytest.mark.asyncio
async def test_turn_steer_wrapper_coerces_text_input_and_returns_typed_response(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "turn/steer",
            save_as="turn_steer",
            params={
                "expectedTurnId": "turn_active",
                "input": [{"type": "text", "text": "Focus on the smallest diff."}],
                "threadId": "thread_turn_text",
            },
        ),
        send_response(
            request_ref="turn_steer",
            result={"turnId": "turn_active"},
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_turn_steer_text_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        result = await client.turn_steer(
            thread_id="thread_turn_text",
            expected_turn_id="turn_active",
            input="Focus on the smallest diff.",
        )

    assert isinstance(result, TurnSteerResponse)
    assert result.turn_id == "turn_active"


@pytest.mark.asyncio
async def test_turn_steer_wrapper_preserves_non_steerable_turn_error(
    tmp_path: Path,
) -> None:
    error_data = {
        "activeTurnNotSteerable": {
            "turnKind": "review",
        }
    }
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "turn/steer",
            save_as="turn_steer",
            params={
                "expectedTurnId": "turn_review",
                "input": [{"type": "text", "text": "Continue from the current review."}],
                "threadId": "thread_review",
            },
        ),
        send_response(
            request_ref="turn_steer",
            error={
                "code": -32000,
                "message": "active turn cannot accept same-turn steering",
                "data": error_data,
            },
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_turn_steer_non_steerable_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        with pytest.raises(JsonRpcServerError) as exc_info:
            await client.turn_steer(
                thread_id="thread_review",
                expected_turn_id="turn_review",
                input="Continue from the current review.",
            )

    assert exc_info.value.code == -32000
    assert exc_info.value.method == "turn/steer"
    assert exc_info.value.request_id == 2
    assert exc_info.value.data == error_data


@pytest.mark.asyncio
async def test_turn_interrupt_wrapper_sends_expected_wire_shape_and_returns_typed_response(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        expect_request(
            "turn/interrupt",
            save_as="turn_interrupt",
            params={
                "threadId": "thread_turn_interrupt",
                "turnId": "turn_interrupt_me",
            },
        ),
        send_response(request_ref="turn_interrupt", result={}),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="typed_turn_interrupt_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        result = await client.turn_interrupt(
            thread_id="thread_turn_interrupt",
            turn_id="turn_interrupt_me",
        )

    assert isinstance(result, TurnInterruptResponse)
    assert result.model_dump() == {}


def test_thread_start_wrapper_rejects_unknown_keyword_argument() -> None:
    client = AppServerClient()
    thread_start = cast(Any, client.thread_start)

    with pytest.raises(TypeError, match="unsupported"):
        thread_start(ephemeral=True, unsupported=True)


def test_thread_resume_wrapper_requires_thread_id_keyword() -> None:
    client = AppServerClient()
    thread_resume = cast(Any, client.thread_resume)

    with pytest.raises(TypeError, match="thread_id"):
        thread_resume()


def test_thread_fork_wrapper_requires_thread_id_keyword() -> None:
    client = AppServerClient()
    thread_fork = cast(Any, client.thread_fork)

    with pytest.raises(TypeError, match="thread_id"):
        thread_fork()


def test_thread_list_wrapper_rejects_unknown_keyword_argument() -> None:
    client = AppServerClient()
    thread_list = cast(Any, client.thread_list)

    with pytest.raises(TypeError, match="unsupported"):
        thread_list(limit=1, unsupported=True)


def test_thread_read_wrapper_requires_thread_id_keyword() -> None:
    client = AppServerClient()
    thread_read = cast(Any, client.thread_read)

    with pytest.raises(TypeError, match="thread_id"):
        thread_read()


def test_thread_archive_wrapper_requires_thread_id_keyword() -> None:
    client = AppServerClient()
    thread_archive = cast(Any, client.thread_archive)

    with pytest.raises(TypeError, match="thread_id"):
        thread_archive()


def test_thread_unarchive_wrapper_requires_thread_id_keyword() -> None:
    client = AppServerClient()
    thread_unarchive = cast(Any, client.thread_unarchive)

    with pytest.raises(TypeError, match="thread_id"):
        thread_unarchive()


def test_thread_set_name_wrapper_requires_name_keyword() -> None:
    client = AppServerClient()
    thread_set_name = cast(Any, client.thread_set_name)

    with pytest.raises(TypeError, match="name"):
        thread_set_name(thread_id="thread_named")


def test_thread_set_name_wrapper_rejects_unknown_keyword_argument() -> None:
    client = AppServerClient()
    thread_set_name = cast(Any, client.thread_set_name)

    with pytest.raises(TypeError, match="unsupported"):
        thread_set_name(
            thread_id="thread_named",
            name="Investigation Scratchpad",
            unsupported=True,
        )


def test_turn_start_wrapper_requires_thread_id_keyword() -> None:
    client = AppServerClient()
    turn_start = cast(Any, client.turn_start)

    with pytest.raises(TypeError, match="thread_id"):
        turn_start(input="Find the failing tests.")


def test_turn_start_wrapper_requires_input_keyword() -> None:
    client = AppServerClient()
    turn_start = cast(Any, client.turn_start)

    with pytest.raises(TypeError, match="input"):
        turn_start(thread_id="thread_turn_text")


def test_turn_start_wrapper_rejects_unknown_keyword_argument() -> None:
    client = AppServerClient()
    turn_start = cast(Any, client.turn_start)

    with pytest.raises(TypeError, match="unsupported"):
        turn_start(
            thread_id="thread_turn_text",
            input="Find the failing tests.",
            unsupported=True,
        )


def test_turn_steer_wrapper_requires_thread_id_keyword() -> None:
    client = AppServerClient()
    turn_steer = cast(Any, client.turn_steer)

    with pytest.raises(TypeError, match="thread_id"):
        turn_steer(expected_turn_id="turn_active", input="Focus on the smallest diff.")


def test_turn_steer_wrapper_requires_expected_turn_id_keyword() -> None:
    client = AppServerClient()
    turn_steer = cast(Any, client.turn_steer)

    with pytest.raises(TypeError, match="expected_turn_id"):
        turn_steer(thread_id="thread_turn_text", input="Focus on the smallest diff.")


def test_turn_steer_wrapper_requires_input_keyword() -> None:
    client = AppServerClient()
    turn_steer = cast(Any, client.turn_steer)

    with pytest.raises(TypeError, match="input"):
        turn_steer(thread_id="thread_turn_text", expected_turn_id="turn_active")


def test_turn_steer_wrapper_rejects_unknown_keyword_argument() -> None:
    client = AppServerClient()
    turn_steer = cast(Any, client.turn_steer)

    with pytest.raises(TypeError, match="unsupported"):
        turn_steer(
            thread_id="thread_turn_text",
            expected_turn_id="turn_active",
            input="Focus on the smallest diff.",
            unsupported=True,
        )


def test_turn_interrupt_wrapper_requires_thread_id_keyword() -> None:
    client = AppServerClient()
    turn_interrupt = cast(Any, client.turn_interrupt)

    with pytest.raises(TypeError, match="thread_id"):
        turn_interrupt(turn_id="turn_interrupt_me")


def test_turn_interrupt_wrapper_requires_turn_id_keyword() -> None:
    client = AppServerClient()
    turn_interrupt = cast(Any, client.turn_interrupt)

    with pytest.raises(TypeError, match="turn_id"):
        turn_interrupt(thread_id="thread_turn_interrupt")


def test_turn_interrupt_wrapper_rejects_unknown_keyword_argument() -> None:
    client = AppServerClient()
    turn_interrupt = cast(Any, client.turn_interrupt)

    with pytest.raises(TypeError, match="unsupported"):
        turn_interrupt(
            thread_id="thread_turn_interrupt",
            turn_id="turn_interrupt_me",
            unsupported=True,
        )


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
        assert initialize_result.protocol_version == 2

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
async def test_cancelling_request_task_releases_waiter_and_connection_stays_usable(
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
        send_response(request_ref="thread_start", result={"threadId": "thread_cancelled"}),
        expect_request(
            "thread/resume",
            save_as="thread_resume",
            params={"threadId": "thread_cancelled"},
        ),
        send_response(
            request_ref="thread_resume",
            result={"threadId": "thread_cancelled", "status": "resumed"},
        ),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="cancelled_request_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()

        request_task = asyncio.create_task(client.request("thread/start", {"ephemeral": True}))
        await asyncio.sleep(0.05)
        request_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(request_task, timeout=IO_TIMEOUT_SECONDS)

        assert client._connection._requests.pending_count == 0

        await asyncio.sleep(0.15)
        resumed = await client.request(
            "thread/resume",
            {"threadId": "thread_cancelled"},
            timeout=IO_TIMEOUT_SECONDS,
        )

    assert resumed == {"threadId": "thread_cancelled", "status": "resumed"}
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
            "item/commandExecution/requestApproval",
            request_id="approval-1",
            params={
                "threadId": "thread_1",
                "turnId": "turn_1",
                "itemId": "item_1",
                "command": ["pytest", "-q"],
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

    assert request.method == "item/commandExecution/requestApproval"
    assert request.request_id == "approval-1"
    assert request.params == {
        "threadId": "thread_1",
        "turnId": "turn_1",
        "itemId": "item_1",
        "command": ["pytest", "-q"],
    }


@pytest.mark.asyncio
async def test_client_can_manually_respond_to_server_request(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        send_server_request(
            "item/fileChange/requestApproval",
            request_id="file-approval-1",
            params={
                "threadId": "thread_1",
                "turnId": "turn_1",
                "itemId": "item_1",
            },
        ),
        expect_response(
            request_ref="file-approval-1",
            result={"decision": "accept"},
        ),
        sleep_action(50),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="manual_server_request_response_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        request = await asyncio.wait_for(
            anext(client.iter_server_requests()),
            timeout=IO_TIMEOUT_SECONDS,
        )
        await client.respond_server_request(request.request_id, {"decision": "accept"})


@pytest.mark.asyncio
async def test_client_server_request_handler_can_auto_reply(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        send_server_request(
            "item/tool/requestUserInput",
            request_id="input-1",
            params={
                "threadId": "thread_1",
                "turnId": "turn_1",
                "questions": [
                    {
                        "id": "ticket",
                        "header": "Ticket",
                        "question": "Which ticket?",
                    }
                ],
            },
        ),
        expect_response(
            request_ref="input-1",
            result={
                "answers": [
                    {
                        "id": "ticket",
                        "value": "ABC-123",
                    }
                ]
            },
        ),
        sleep_action(50),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="server_request_handler_launcher.py",
    )

    handler_called = asyncio.Event()

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:

        async def _handler(request: JsonRpcRequest) -> object:
            assert request.method == "item/tool/requestUserInput"
            handler_called.set()
            return {
                "answers": [
                    {
                        "id": "ticket",
                        "value": "ABC-123",
                    }
                ]
            }

        client.register_server_request_handler("item/tool/requestUserInput", _handler)
        await client.initialize()
        await asyncio.wait_for(handler_called.wait(), timeout=IO_TIMEOUT_SECONDS)


@pytest.mark.asyncio
async def test_client_server_request_handler_errors_reply_with_jsonrpc_error(
    tmp_path: Path,
) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        send_server_request(
            "mcpServer/elicitation/request",
            request_id="elicitation-1",
            params={
                "threadId": "thread_1",
                "turnId": "turn_1",
                "serverName": "github",
                "mode": "form",
            },
        ),
        expect_response(
            request_ref="elicitation-1",
            error={
                "code": -32603,
                "message": "client server-request handler failed",
            },
        ),
        sleep_action(50),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="server_request_handler_error_launcher.py",
    )
    handler_called = asyncio.Event()

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:

        async def _handler(request: JsonRpcRequest) -> object:
            handler_called.set()
            raise RuntimeError(f"boom for {request.request_id!r}")

        client.register_server_request_handler("mcpServer/elicitation/request", _handler)
        await client.initialize()
        await asyncio.wait_for(handler_called.wait(), timeout=IO_TIMEOUT_SECONDS)


@pytest.mark.asyncio
async def test_notification_subscriptions_can_coexist_and_filter(tmp_path: Path) -> None:
    script = FakeAppServerScript.from_actions(
        expect_request("initialize", save_as="initialize"),
        send_response(request_ref="initialize", result={"protocolVersion": 2}),
        expect_notification("initialized"),
        sleep_action(100),
        send_notification(
            "thread/updated",
            params={"threadId": "thread_1", "status": "running"},
        ),
        send_notification(
            "turn/started",
            params={"threadId": "thread_1", "turnId": "turn_1", "status": "running"},
        ),
        send_notification(
            "turn/started",
            params={"threadId": "thread_2", "turnId": "turn_2", "status": "running"},
        ),
        sleep_action(300),
    )
    launcher = _write_fake_codex_launcher(
        tmp_path,
        script,
        stem="notification_subscriptions_launcher.py",
    )

    async with AppServerClient(AppServerConfig(codex_bin=str(launcher))) as client:
        await client.initialize()
        catch_all = client.iter_notifications()
        thread_notifications = client.subscribe_thread_notifications(
            "thread_1"
        ).iter_notifications()
        turn_notifications = client.subscribe_turn_notifications(
            "turn_1",
            thread_id="thread_1",
        ).iter_notifications()

        first = await asyncio.wait_for(anext(catch_all), timeout=IO_TIMEOUT_SECONDS)
        second = await asyncio.wait_for(anext(catch_all), timeout=IO_TIMEOUT_SECONDS)
        third = await asyncio.wait_for(anext(catch_all), timeout=IO_TIMEOUT_SECONDS)

        thread_first = await asyncio.wait_for(
            anext(thread_notifications),
            timeout=IO_TIMEOUT_SECONDS,
        )
        thread_second = await asyncio.wait_for(
            anext(thread_notifications),
            timeout=IO_TIMEOUT_SECONDS,
        )
        turn_only = await asyncio.wait_for(
            anext(turn_notifications),
            timeout=IO_TIMEOUT_SECONDS,
        )

    assert [first.method, second.method, third.method] == [
        "thread/updated",
        "turn/started",
        "turn/started",
    ]
    assert first.params == {"threadId": "thread_1", "status": "running"}
    assert second.params == {"threadId": "thread_1", "turnId": "turn_1", "status": "running"}
    assert third.params == {"threadId": "thread_2", "turnId": "turn_2", "status": "running"}
    assert thread_first == first
    assert thread_second == second
    assert turn_only == second


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


def _build_thread_payload(*, thread_id: str = "thread_123") -> dict[str, object]:
    return {
        "cliVersion": "codex-cli 0.118.0",
        "createdAt": 1_710_000_000,
        "cwd": "/repo",
        "ephemeral": True,
        "id": thread_id,
        "modelProvider": "openai",
        "preview": "Find the smallest failing test.",
        "source": "appServer",
        "status": {"type": "idle"},
        "turns": [],
        "updatedAt": 1_710_000_001,
    }


def _build_thread_start_result(*, thread_id: str = "thread_123") -> dict[str, object]:
    return {
        "approvalPolicy": "on-request",
        "approvalsReviewer": "user",
        "cwd": "/repo",
        "model": "gpt-5.4",
        "modelProvider": "openai",
        "sandbox": {"type": "dangerFullAccess"},
        "thread": _build_thread_payload(thread_id=thread_id),
    }


def _build_turn_payload(
    *,
    turn_id: str = "turn_123",
    status: str = "inProgress",
) -> dict[str, object]:
    return {
        "id": turn_id,
        "items": [],
        "status": status,
    }


def _build_turn_start_result(*, turn_id: str = "turn_123") -> dict[str, object]:
    return {"turn": _build_turn_payload(turn_id=turn_id)}


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

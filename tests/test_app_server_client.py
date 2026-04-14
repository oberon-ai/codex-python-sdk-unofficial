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
    ThreadResumeResponse,
    ThreadStartParams,
    ThreadStartResponse,
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

import asyncio
import base64
import json
import logging
import os
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv
from fastmcp import Client
from fastmcp.exceptions import McpError, ToolError

from enums import JobStatus
from executor import DockerExecutor
from models import ExecutorConfig, JobResult


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(s.getsockname()[1])


async def _wait_server_ready(base_url: str, api_key: str, timeout_s: float = 12.0) -> None:
    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            async with Client(base_url, auth=api_key) as client:
                await client.list_tools()
            return
        except Exception as exc:
            last_err = exc
            await asyncio.sleep(0.2)
    msg = f"Server did not become ready: {last_err}"
    raise RuntimeError(msg)


@pytest.fixture(scope="session")
def server_ctx() -> Generator[dict[str, str], None, None]:
    load_dotenv()
    api_key = os.getenv("MCP_API_KEY", "").strip()
    if not api_key:
        msg = "MCP_API_KEY is required for tests"
        raise RuntimeError(msg)

    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}/mcp"
    env = os.environ.copy()
    env["MCP_PORT"] = str(port)

    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=Path.cwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        asyncio.run(_wait_server_ready(base_url=base_url, api_key=api_key))
        yield {"base_url": base_url, "api_key": api_key}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _make_executor_config(
    *,
    seccomp_profile: str | None = None,
    apparmor_profile: str | None = None,
    max_concurrent_jobs: int = 2,
    queue_wait_seconds: float = 1.0,
) -> ExecutorConfig:
    return ExecutorConfig(
        image="python:3.12-alpine",
        timeout_seconds=5.0,
        memory_mb=256,
        cpu_count=1.0,
        pids_limit=64,
        output_limit_bytes=65536,
        max_concurrent_jobs=max_concurrent_jobs,
        queue_wait_seconds=queue_wait_seconds,
        seccomp_profile=seccomp_profile,
        apparmor_profile=apparmor_profile,
    )


def test_get_docker_cmd_security_opts_before_image() -> None:
    executor = DockerExecutor(
        config=_make_executor_config(
            seccomp_profile="/profiles/seccomp.json",
            apparmor_profile="/profiles/apparmor.profile",
        )
    )
    cmd = executor._get_docker_cmd(workspace=Path("/tmp/workspace"), has_stdin=False)

    image_idx = cmd.index("python:3.12-alpine")
    seccomp_idx = cmd.index("seccomp=/profiles/seccomp.json")
    apparmor_idx = cmd.index("apparmor=/profiles/apparmor.profile")

    assert seccomp_idx < image_idx
    assert apparmor_idx < image_idx


def test_get_docker_cmd_omits_security_opts_when_profiles_absent() -> None:
    executor = DockerExecutor(config=_make_executor_config())
    cmd = executor._get_docker_cmd(workspace=Path("/tmp/workspace"), has_stdin=False)

    assert "--security-opt" in cmd
    assert all(not arg.startswith("seccomp=") for arg in cmd)
    assert all(not arg.startswith("apparmor=") for arg in cmd)


def test_run_container_file_not_found_returns_structured_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def _raise_file_not_found(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise_file_not_found)
    executor = DockerExecutor(config=_make_executor_config())

    with caplog.at_level(logging.INFO):
        result = asyncio.run(
            executor._run_container(
                execution_id="exec-1",
                workspace=tmp_path,
                stdin=None,
                start=time.perf_counter(),
            )
        )

    assert result.status == "failed"
    assert result.error_type == "EXECUTOR_UNAVAILABLE"
    assert result.error_message == "docker binary not found"
    assert '"event": "sandbox.start_failed"' in caplog.text
    assert '"error_type": "EXECUTOR_UNAVAILABLE"' in caplog.text


def test_run_container_permission_error_returns_structured_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def _raise_permission_error(*_args, **_kwargs):
        raise PermissionError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise_permission_error)
    executor = DockerExecutor(config=_make_executor_config())

    with caplog.at_level(logging.INFO):
        result = asyncio.run(
            executor._run_container(
                execution_id="exec-2",
                workspace=tmp_path,
                stdin=None,
                start=time.perf_counter(),
            )
        )

    assert result.status == "failed"
    assert result.error_type == "EXECUTOR_START_FAILED"
    assert result.error_message == "failed to start docker process"
    assert '"event": "sandbox.start_failed"' in caplog.text
    assert '"error_type": "EXECUTOR_START_FAILED"' in caplog.text


def test_backpressure_returns_executor_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_run_container(*_args, **_kwargs):
        await asyncio.sleep(0.2)
        return JobResult(
            status=JobStatus.COMPLETED,
            stdout="ok",
            stderr="",
            exit_code=0,
            duration_ms=1,
            truncated=False,
        )

    monkeypatch.setattr(DockerExecutor, "_run_container", _fake_run_container)

    executor = DockerExecutor(
        config=_make_executor_config(max_concurrent_jobs=1, queue_wait_seconds=0.05)
    )

    async def _case() -> tuple[JobResult, JobResult]:
        first = asyncio.create_task(executor.run("print('first')"))
        await asyncio.sleep(0.01)
        second = await executor.run("print('second')")
        first_result = await first
        return first_result, second

    first_result, second_result = asyncio.run(_case())

    assert first_result.status == "completed"
    assert second_result.status == "failed"
    assert second_result.error_type == "EXECUTOR_BUSY"


def test_auth_rejected(server_ctx: dict[str, str]) -> None:
    async def _case() -> None:
        client = Client(server_ctx["base_url"], auth="wrong-key")
        with pytest.raises(McpError):
            async with client:
                await client.list_tools()

    asyncio.run(_case())


def test_auth_missing_bearer_rejected(server_ctx: dict[str, str]) -> None:
    async def _case() -> None:
        client = Client(server_ctx["base_url"])
        with pytest.raises(McpError):
            async with client:
                await client.list_tools()

    asyncio.run(_case())


def test_auth_basic_rejected(server_ctx: dict[str, str]) -> None:
    class BasicAuth(httpx.Auth):
        def auth_flow(self, request: httpx.Request):
            payload = base64.b64encode(b"user:password").decode("ascii")
            request.headers["Authorization"] = f"Basic {payload}"
            yield request

    async def _case() -> None:
        client = Client(server_ctx["base_url"], auth=BasicAuth())
        with pytest.raises(McpError):
            async with client:
                await client.list_tools()

    asyncio.run(_case())


def test_auth_bearer_valid(server_ctx: dict[str, str]) -> None:
    async def _case() -> None:
        async with Client(server_ctx["base_url"], auth=server_ctx["api_key"]) as client:
            tools = await client.list_tools()
        assert tools

    asyncio.run(_case())


def test_validation_empty_code(server_ctx: dict[str, str]) -> None:
    async def _case() -> None:
        async with Client(server_ctx["base_url"], auth=server_ctx["api_key"]) as client:
            with pytest.raises(ToolError, match="INVALID_INPUT"):
                await client.call_tool("run_code", {"code": "   "}, task=False)

    asyncio.run(_case())


def test_validation_max_size(server_ctx: dict[str, str]) -> None:
    huge_code = "print('x')\n" + ("#" * 40000)

    async def _case() -> None:
        async with Client(server_ctx["base_url"], auth=server_ctx["api_key"]) as client:
            with pytest.raises(ToolError, match="INVALID_INPUT"):
                await client.call_tool("run_code", {"code": huge_code}, task=False)

    asyncio.run(_case())


def test_happy_path_with_input(server_ctx: dict[str, str]) -> None:
    code = (
        "import json,sys\n"
        "payload=json.loads(sys.stdin.read() or '{}')\n"
        "nums=payload.get('numbers', [])\n"
        "print(json.dumps({'count': len(nums), 'sum': sum(nums)}))\n"
    )
    input_payload = json.dumps({"numbers": [2, 3, 5, 7]})

    async def _case() -> None:
        async with Client(server_ctx["base_url"], auth=server_ctx["api_key"]) as client:
            result = await client.call_tool(
                "run_code",
                {"code": code, "stdin": input_payload},
                task=False,
            )
        data = result.data
        assert data["status"] == "completed"
        parsed = json.loads(data["stdout"].strip())
        assert parsed == {"count": 4, "sum": 17}

    asyncio.run(_case())


def test_runtime_error(server_ctx: dict[str, str]) -> None:
    async def _case() -> None:
        async with Client(server_ctx["base_url"], auth=server_ctx["api_key"]) as client:
            result = await client.call_tool(
                "run_code",
                {"code": "raise RuntimeError('boom')"},
                task=False,
            )
        data = result.data
        assert data["status"] == "failed"
        assert data["exit_code"] is not None
        assert "RuntimeError" in data["stderr"]

    asyncio.run(_case())


def test_timeout(server_ctx: dict[str, str]) -> None:
    async def _case() -> None:
        async with Client(server_ctx["base_url"], auth=server_ctx["api_key"]) as client:
            result = await client.call_tool(
                "run_code",
                {"code": "while True:\n    pass\n"},
                task=False,
            )
        data = result.data
        assert data["status"] == "timeout"
        assert data["exit_code"] is None

    asyncio.run(_case())


def test_truncation(server_ctx: dict[str, str]) -> None:
    async def _case() -> None:
        async with Client(server_ctx["base_url"], auth=server_ctx["api_key"]) as client:
            result = await client.call_tool(
                "run_code",
                {"code": "print('A'*200000)"},
                task=False,
            )
        data = result.data
        assert data["status"] == "completed"
        assert data["truncated"] is True

    asyncio.run(_case())


def test_task_flow(server_ctx: dict[str, str]) -> None:
    async def _case() -> None:
        async with Client(server_ctx["base_url"], auth=server_ctx["api_key"]) as client:
            task = await client.call_tool(
                "run_code",
                {"code": "print('task-ok')"},
                task=True,
            )
            status = await task.wait(timeout=15.0)
            assert status.status in {"completed", "failed", "cancelled"}
            result = await task.result()
        data = result.data
        assert data["status"] == "completed"
        assert "task-ok" in data["stdout"]

    asyncio.run(_case())

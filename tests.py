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
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import McpError

from enums import JobStatus
from executor import DockerExecutor
from models import ExecutorConfig, JobResult


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


async def _wait_server_ready(base_url: str, api_key: str, timeout_s: float = 12.0) -> None:
    deadline = time.time() + timeout_s
    last_error: Exception | None = None

    while time.time() < deadline:
        try:
            async with Client(base_url, auth=api_key) as client:
                await client.list_tools()
            return
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.2)

    msg = f"Server did not become ready: {last_error}"
    raise RuntimeError(msg)


@pytest.fixture(scope="session")
def server_ctx() -> Generator[dict[str, str], None, None]:
    api_key = "test-suite-api-key"
    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}/mcp"

    env = os.environ.copy()
    env["MCP_API_KEY"] = api_key
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


async def _call_run_code(
    server_ctx: dict[str, str], code: str, stdin: str | None = None, task: bool = False
) -> dict[str, Any]:
    base_url = server_ctx["base_url"]
    api_key = server_ctx["api_key"]

    async with Client(base_url, auth=api_key) as client:
        result = await client.call_tool("run_code", {"code": code, "stdin": stdin}, task=task)
        if not task:
            return result.data

        status = await result.wait(timeout=15.0)
        assert status.status in {"completed", "failed", "cancelled"}
        task_result = await result.result()
        return task_result.data


class BasicAuth(httpx.Auth):
    def auth_flow(self, request: httpx.Request):
        payload = base64.b64encode(b"user:password").decode("ascii")
        request.headers["Authorization"] = f"Basic {payload}"
        yield request


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


def test_auth_rejects_missing_bearer(server_ctx: dict[str, str]) -> None:
    client = Client(server_ctx["base_url"])

    async def _case() -> None:
        with pytest.raises(McpError):
            async with client:
                await client.list_tools()

    asyncio.run(_case())


def test_auth_rejects_wrong_bearer(server_ctx: dict[str, str]) -> None:
    client = Client(server_ctx["base_url"], auth="wrong-key")

    async def _case() -> None:
        with pytest.raises(McpError):
            async with client:
                await client.list_tools()

    asyncio.run(_case())


def test_auth_rejects_basic_auth(server_ctx: dict[str, str]) -> None:
    client = Client(server_ctx["base_url"], auth=BasicAuth())

    async def _case() -> None:
        with pytest.raises(McpError):
            async with client:
                await client.list_tools()

    asyncio.run(_case())


def test_practical_sales_summary_from_json_input(server_ctx: dict[str, str]) -> None:
    code = (
        "import json, sys\n"
        "orders = json.loads(sys.stdin.read())\n"
        "revenue = sum(item['price'] * item['qty'] for item in orders)\n"
        "top = max(orders, key=lambda x: x['price'] * x['qty'])['sku']\n"
        "summary = {'orders': len(orders), 'revenue': revenue, 'top_sku': top}\n"
        "print(json.dumps(summary, sort_keys=True))\n"
    )
    stdin = json.dumps(
        [
            {"sku": "A-100", "price": 15, "qty": 3},
            {"sku": "B-220", "price": 7, "qty": 10},
            {"sku": "C-777", "price": 55, "qty": 1},
        ]
    )

    data = asyncio.run(_call_run_code(server_ctx, code=code, stdin=stdin))

    assert data["status"] == "completed"
    assert json.loads(data["stdout"].strip()) == {"orders": 3, "revenue": 170, "top_sku": "B-220"}


def test_practical_log_analysis_top_ip(server_ctx: dict[str, str]) -> None:
    code = (
        "from collections import Counter\n"
        "import json, sys\n"
        "ips=[line.split()[0] for line in sys.stdin.read().strip().splitlines() if line.strip()]\n"
        "counts=Counter(ips)\n"
        "ip,count=counts.most_common(1)[0]\n"
        "print(json.dumps({'top_ip': ip, 'hits': count}, sort_keys=True))\n"
    )
    stdin = "\n".join(
        [
            "10.0.0.1 GET /health 200",
            "10.0.0.5 GET /api/orders 200",
            "10.0.0.1 POST /api/payments 201",
            "10.0.0.1 GET /api/orders 200",
            "10.0.0.5 GET /api/orders 500",
        ]
    )

    data = asyncio.run(_call_run_code(server_ctx, code=code, stdin=stdin))

    assert data["status"] == "completed"
    assert json.loads(data["stdout"].strip()) == {"hits": 3, "top_ip": "10.0.0.1"}


def test_practical_shortest_path_task_mode(server_ctx: dict[str, str]) -> None:
    expected_hops = 3
    code = (
        "from collections import deque\n"
        "import json,sys\n"
        "payload=json.loads(sys.stdin.read())\n"
        "g=payload['graph']\n"
        "start,end=payload['start'],payload['end']\n"
        "q=deque([(start,[start])])\n"
        "seen={start}\n"
        "while q:\n"
        "  node,path=q.popleft()\n"
        "  if node==end:\n"
        "    print(json.dumps({'path': path, 'hops': len(path)-1}))\n"
        "    break\n"
        "  for nxt in g.get(node,[]):\n"
        "    if nxt not in seen:\n"
        "      seen.add(nxt); q.append((nxt,path+[nxt]))\n"
    )
    stdin = json.dumps(
        {
            "graph": {
                "WH-A": ["WH-B", "WH-C"],
                "WH-B": ["WH-D"],
                "WH-C": ["WH-E"],
                "WH-D": ["WH-F"],
                "WH-E": ["WH-F"],
                "WH-F": [],
            },
            "start": "WH-A",
            "end": "WH-F",
        }
    )

    data = asyncio.run(_call_run_code(server_ctx, code=code, stdin=stdin, task=True))

    assert data["status"] == "completed"
    parsed = json.loads(data["stdout"].strip())
    assert parsed["hops"] == expected_hops
    assert parsed["path"][0] == "WH-A"
    assert parsed["path"][-1] == "WH-F"


def test_runtime_error_returns_failed_status(server_ctx: dict[str, str]) -> None:
    code = "raise RuntimeError('business-rule-violation')"

    data = asyncio.run(_call_run_code(server_ctx, code=code))

    assert data["status"] == "failed"
    assert data["exit_code"] is not None
    assert "RuntimeError" in data["stderr"]


def test_timeout_for_infinite_loop(server_ctx: dict[str, str]) -> None:
    code = "while True:\n    pass\n"

    data = asyncio.run(_call_run_code(server_ctx, code=code))

    assert data["status"] == "timeout"
    assert data["exit_code"] is None


def test_truncation_for_large_output(server_ctx: dict[str, str]) -> None:
    code = "print('LOG'*50000)"

    data = asyncio.run(_call_run_code(server_ctx, code=code))

    assert data["status"] == "completed"
    assert data["truncated"] is True


def test_get_docker_cmd_places_security_opts_before_image() -> None:
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


def test_get_docker_cmd_without_profiles_omits_seccomp_and_apparmor() -> None:
    executor = DockerExecutor(config=_make_executor_config())

    cmd = executor._get_docker_cmd(workspace=Path("/tmp/workspace"), has_stdin=False)

    assert all(not arg.startswith("seccomp=") for arg in cmd)
    assert all(not arg.startswith("apparmor=") for arg in cmd)


def test_run_container_file_not_found_maps_to_executor_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _raise_file_not_found(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise_file_not_found)
    executor = DockerExecutor(config=_make_executor_config())

    with caplog.at_level(logging.INFO):
        result = asyncio.run(
            executor._run_container(
                execution_id="exec-unavailable",
                workspace=tmp_path,
                stdin=None,
                start=time.perf_counter(),
            )
        )

    assert result.status == "failed"
    assert result.error_type == "EXECUTOR_UNAVAILABLE"
    assert result.error_message == "docker binary not found"
    assert '"event": "sandbox.start_failed"' in caplog.text


def test_run_container_permission_error_maps_to_executor_start_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _raise_permission_error(*_args, **_kwargs):
        raise PermissionError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise_permission_error)
    executor = DockerExecutor(config=_make_executor_config())

    with caplog.at_level(logging.INFO):
        result = asyncio.run(
            executor._run_container(
                execution_id="exec-start-failed",
                workspace=tmp_path,
                stdin=None,
                start=time.perf_counter(),
            )
        )

    assert result.status == "failed"
    assert result.error_type == "EXECUTOR_START_FAILED"
    assert result.error_message == "failed to start docker process"
    assert '"event": "sandbox.start_failed"' in caplog.text


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

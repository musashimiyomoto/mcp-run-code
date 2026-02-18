from typing import Generator
import asyncio
import json
import os
import socket
import subprocess
import sys
import time

import pytest
from dotenv import load_dotenv
from fastmcp import Client
from fastmcp.exceptions import ToolError


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(s.getsockname()[1])


async def _wait_server_ready(
    base_url: str, api_key: str, timeout_s: float = 12.0
) -> None:
    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            async with Client(base_url, auth=api_key) as client:
                await client.list_tools()
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            await asyncio.sleep(0.2)
    raise RuntimeError(f"Server did not become ready: {last_err}")


@pytest.fixture(scope="session")
def server_ctx() -> Generator[dict[str, str], None, None]:
    load_dotenv()
    api_key = os.getenv("MCP_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MCP_API_KEY is required for tests")

    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}/mcp"
    env = os.environ.copy()
    env["MCP_PORT"] = str(port)

    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=os.getcwd(),
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


def test_auth_rejected(server_ctx: dict[str, str]) -> None:
    async def _case() -> None:
        client = Client(server_ctx["base_url"], auth="wrong-key")
        with pytest.raises(Exception):
            async with client:
                await client.list_tools()

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
                {"code": code, "input_data": input_payload},
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

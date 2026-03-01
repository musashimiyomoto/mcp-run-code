from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from constants import UTF_8
from executor import DockerExecutor
from middleware import ApiKeyMiddleware
from models import ExecutorConfig
from settings import settings

mcp = FastMCP("Code Executor MCP", tasks=True, middleware=[ApiKeyMiddleware()])

executor = DockerExecutor(
    config=ExecutorConfig(
        image=settings.docker_image,
        timeout_seconds=settings.timeout_seconds,
        memory_mb=settings.memory_mb,
        cpu_count=settings.cpu_count,
        pids_limit=settings.pids_limit,
        output_limit_bytes=settings.max_output_bytes,
        max_concurrent_jobs=settings.max_concurrent_jobs,
        queue_wait_seconds=settings.queue_wait_seconds,
        seccomp_profile=settings.docker_seccomp_profile,
        apparmor_profile=settings.docker_apparmor_profile,
    )
)


@mcp.tool(task=True)
async def run_code(code: str, stdin: str | None = None) -> dict[str, Any]:
    if not code or not code.strip():
        msg = "INVALID_INPUT: 'code' must not be empty"
        raise ToolError(msg)

    if len(code.encode(encoding=UTF_8)) > settings.max_code_bytes:
        msg = f"INVALID_INPUT: code is too large; max {settings.max_code_bytes} bytes"
        raise ToolError(msg)

    result = await executor.run(code=code, stdin=stdin)

    return result.model_dump()


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=settings.port)

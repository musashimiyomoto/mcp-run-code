from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from executor import DockerExecutor, ExecutorConfig
from models import JobStatus
from settings import settings
from middleware import ApiKeyMiddleware


mcp = FastMCP("Code Executor MCP", tasks=True, middleware=[ApiKeyMiddleware()])
executor = DockerExecutor(
    ExecutorConfig(
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
async def run_code(
    code: str,
    stdin: str | None = None,
    input_data: str | None = None,
) -> dict[str, JobStatus | str | int | bool | None]:
    if not code or not code.strip():
        raise ToolError("INVALID_INPUT: 'code' must not be empty")

    if len(code.encode("utf-8")) > settings.max_code_bytes:
        raise ToolError(
            f"INVALID_INPUT: code is too large; max {settings.max_code_bytes} bytes"
        )

    try:
        result = await executor.run_python(
            code=code, stdin=input_data if input_data is not None else stdin
        )
    except Exception as exc:
        return {
            "status": "failed",
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "duration_ms": None,
            "truncated": False,
            "error_type": "EXECUTOR_ERROR",
            "error_message": str(exc),
        }

    return {
        "status": result.status,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "truncated": result.truncated,
        "error_type": result.error_type,
        "error_message": result.error_message,
    }


if __name__ == "__main__":
    mcp.run(transport="http", port=settings.port)

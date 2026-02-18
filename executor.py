import asyncio
import contextlib
import os
import tempfile
import time
from pathlib import Path

from models import JobResult, OutputState, ExecutorConfig


class DockerExecutor:
    def __init__(self, config: ExecutorConfig) -> None:
        self._config = config

    async def run_python(self, code: str, stdin: str | None) -> JobResult:
        start = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="mcp-job-") as temp_dir:
            workspace = Path(temp_dir)
            os.chmod(workspace, 0o755)
            script_path = workspace / "main.py"
            script_path.write_text(code, encoding="utf-8")
            os.chmod(script_path, 0o644)
            return await self._run_container(
                workspace=workspace, stdin=stdin, start=start
            )

    async def _run_container(
        self,
        workspace: Path,
        stdin: str | None,
        start: float,
    ) -> JobResult:
        cmd = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--pids-limit",
            str(self._config.pids_limit),
            "--memory",
            f"{self._config.memory_mb}m",
            "--cpus",
            str(self._config.cpu_count),
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,source={workspace},target=/workspace,readonly",
            self._config.image,
            "python",
            "-B",
            "/workspace/main.py",
        ]
        if stdin is not None:
            cmd.insert(3, "-i")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE
            if stdin is not None
            else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdout is not None
        assert proc.stderr is not None

        state = OutputState(
            stdout=bytearray(),
            stderr=bytearray(),
            total_kept=0,
            limit=self._config.output_limit_bytes,
            truncated=False,
        )
        stdout_task = asyncio.create_task(
            self._read_stream(proc.stdout, state.stdout, state)
        )
        stderr_task = asyncio.create_task(
            self._read_stream(proc.stderr, state.stderr, state)
        )

        if stdin is not None and proc.stdin is not None:
            proc.stdin.write(stdin.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
            with contextlib.suppress(Exception):
                await proc.stdin.wait_closed()

        try:
            await asyncio.wait_for(proc.wait(), timeout=self._config.timeout_seconds)
            await asyncio.gather(stdout_task, stderr_task)
        except asyncio.TimeoutError:
            proc.kill()
            with contextlib.suppress(ProcessLookupError):
                await proc.wait()

            await asyncio.gather(stdout_task, stderr_task)

            return JobResult(
                status="timeout",
                stdout=state.stdout.decode("utf-8", errors="replace"),
                stderr=state.stderr.decode("utf-8", errors="replace"),
                exit_code=None,
                duration_ms=int((time.perf_counter() - start) * 1000),
                truncated=state.truncated,
                error_type=None,
                error_message=None,
            )

        return JobResult(
            status="completed" if proc.returncode == 0 else "failed",
            stdout=state.stdout.decode("utf-8", errors="replace"),
            stderr=state.stderr.decode("utf-8", errors="replace"),
            exit_code=proc.returncode,
            duration_ms=int((time.perf_counter() - start) * 1000),
            truncated=state.truncated,
            error_type=None,
            error_message=None,
        )

    async def _read_stream(
        self, stream: asyncio.StreamReader, target: bytearray, state: OutputState
    ) -> None:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            state.append(target, chunk)

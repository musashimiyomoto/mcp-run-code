import asyncio
import contextlib
import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path

from models import JobResult, OutputState, ExecutorConfig

logger = logging.getLogger(__name__)


class DockerExecutor:
    def __init__(self, config: ExecutorConfig) -> None:
        self._config = config
        self._semaphore = asyncio.Semaphore(config.max_concurrent_jobs)

    async def run_python(self, code: str, stdin: str | None) -> JobResult:
        execution_id = uuid.uuid4().hex
        start = time.perf_counter()
        acquired = False
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(), timeout=self._config.queue_wait_seconds
            )
            acquired = True
        except asyncio.TimeoutError:
            self._audit(
                event="rejected_busy",
                execution_id=execution_id,
                queue_wait_seconds=self._config.queue_wait_seconds,
                max_concurrent_jobs=self._config.max_concurrent_jobs,
            )
            return JobResult(
                status="failed",
                stdout="",
                stderr="",
                exit_code=None,
                duration_ms=0,
                truncated=False,
                error_type="EXECUTOR_BUSY",
                error_message=(
                    "Executor is busy. Try again later or increase MCP_MAX_CONCURRENT_JOBS."
                ),
            )

        self._audit(event="started", execution_id=execution_id)
        try:
            with tempfile.TemporaryDirectory(prefix="mcp-job-") as temp_dir:
                workspace = Path(temp_dir)
                os.chmod(workspace, 0o755)
                if workspace.is_symlink():
                    raise RuntimeError("Workspace must not be a symlink")
                script_path = workspace / "main.py"
                script_path.write_text(code, encoding="utf-8")
                os.chmod(script_path, 0o644)
                if script_path.is_symlink():
                    raise RuntimeError("Script path must not be a symlink")
                result = await self._run_container(
                    workspace=workspace, stdin=stdin, start=start
                )
    
            self._audit(
                event="finished",
                execution_id=execution_id,
                status=result.status,
                duration_ms=result.duration_ms,
                exit_code=result.exit_code,
                truncated=result.truncated,
                error_type=result.error_type,
            )
            return result
        finally:
            if acquired:
                self._semaphore.release()

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
            "--ipc",
            "none",
            "--cap-drop",
            "ALL",
            "--pids-limit",
            str(self._config.pids_limit),
            "--ulimit",
            "nproc=64:64",
            "--ulimit",
            "nofile=1024:1024",
            "--memory",
            f"{self._config.memory_mb}m",
            "--cpus",
            str(self._config.cpu_count),
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--tmpfs",
            "/run:rw,noexec,nosuid,nodev,size=16m",
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,source={workspace},target=/workspace,readonly,bind-propagation=rprivate",
            self._config.image,
            "python",
            "-B",
            "/workspace/main.py",
        ]
        if self._config.seccomp_profile:
            cmd.extend(["--security-opt", f"seccomp={self._config.seccomp_profile}"])
        if self._config.apparmor_profile:
            cmd.extend(["--security-opt", f"apparmor={self._config.apparmor_profile}"])
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

    def _audit(self, event: str, **fields: object) -> None:
        payload = {"event": f"sandbox.{event}", **fields}
        logger.info(json.dumps(payload, ensure_ascii=True, sort_keys=True))

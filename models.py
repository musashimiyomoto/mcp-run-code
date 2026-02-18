from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

JobStatus = Literal["queued", "running", "completed", "failed", "timeout"]


@dataclass(slots=True)
class JobResult:
    status: JobStatus
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int | None
    truncated: bool
    error_type: str | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class ExecutorConfig:
    image: str
    timeout_seconds: float
    memory_mb: int
    cpu_count: float
    pids_limit: int
    output_limit_bytes: int
    max_concurrent_jobs: int
    queue_wait_seconds: float
    seccomp_profile: str | None
    apparmor_profile: str | None


@dataclass(slots=True)
class OutputState:
    stdout: bytearray
    stderr: bytearray
    total_kept: int
    limit: int
    truncated: bool

    def append(self, target: bytearray, chunk: bytes) -> None:
        if self.total_kept >= self.limit:
            self.truncated = True
            return

        available = self.limit - self.total_kept
        if len(chunk) <= available:
            target.extend(chunk)
            self.total_kept += len(chunk)
            return

        target.extend(chunk[:available])
        self.total_kept += available
        self.truncated = True


@dataclass(frozen=True, slots=True)
class Settings:
    api_key: str
    max_code_bytes: int
    max_output_bytes: int
    timeout_seconds: float
    memory_mb: int
    cpu_count: float
    pids_limit: int
    docker_image: str
    port: int
    max_concurrent_jobs: int
    queue_wait_seconds: float
    docker_seccomp_profile: str | None
    docker_apparmor_profile: str | None

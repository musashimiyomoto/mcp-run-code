from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal["queued", "running", "completed", "failed", "timeout"]


class JobResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: JobStatus
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int | None
    truncated: bool
    error_type: str | None = None
    error_message: str | None = None


class ExecutorConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    image: str
    timeout_seconds: float = Field(gt=0)
    memory_mb: int = Field(gt=0)
    cpu_count: float = Field(gt=0)
    pids_limit: int = Field(gt=0)
    output_limit_bytes: int = Field(gt=0)
    max_concurrent_jobs: int = Field(gt=0)
    queue_wait_seconds: float = Field(gt=0)
    seccomp_profile: str | None = None
    apparmor_profile: str | None = None


class OutputState(BaseModel):
    stdout: bytearray = Field(default_factory=bytearray)
    stderr: bytearray = Field(default_factory=bytearray)
    total_kept: int = 0
    limit: int
    truncated: bool = False

    model_config = ConfigDict(arbitrary_types_allowed=True)

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

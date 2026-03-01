from pydantic import BaseModel, ConfigDict, Field

from enums import JobStatus


class JobResult(BaseModel):
    model_config = ConfigDict(frozen=True, use_enum_values=True)

    status: JobStatus = Field(default=..., description="JobStatus value as a string")
    stdout: str = Field(default=..., description="Captured standard output, truncated to max_output_bytes")
    stderr: str = Field(default=..., description="Captured standard error")
    exit_code: int | None = Field(default=None, description="Exit code of the process")
    duration_ms: int | None = Field(default=None, description="Duration in milliseconds")
    truncated: bool = Field(default=False, description="Whether output was truncated")
    error_type: str | None = Field(default=None, description="Error type if job failed")
    error_message: str | None = Field(default=None, description="Error message if job failed")


class ExecutorConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    image: str = Field(default=..., description="Docker image to use for execution")
    timeout_seconds: float = Field(gt=0, description="Timeout in seconds")
    memory_mb: int = Field(gt=0, description="Memory limit in MB")
    cpu_count: float = Field(gt=0, description="CPU count")
    pids_limit: int = Field(gt=0, description="PID limit")
    output_limit_bytes: int = Field(gt=0, description="Output limit in bytes")
    max_concurrent_jobs: int = Field(gt=0, description="Maximum concurrent jobs")
    queue_wait_seconds: float = Field(gt=0, description="Queue wait timeout in seconds")
    seccomp_profile: str | None = Field(default=None, description="Seccomp profile path")
    apparmor_profile: str | None = Field(default=None, description="AppArmor profile path")


class OutputState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    stdout: bytearray = Field(default_factory=bytearray, description="Captured standard output as a bytearray")
    stderr: bytearray = Field(default_factory=bytearray, description="Captured standard error as a bytearray")
    total_kept: int = Field(default=0, description="Total bytes kept so far")
    limit: int = Field(default=..., description="Output limit in bytes")
    truncated: bool = Field(default=False, description="Whether output was truncated")

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

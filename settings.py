from pathlib import Path
from typing import Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MCP_",
        case_sensitive=False,
    )

    api_key: str = Field(min_length=1)
    max_code_bytes: int = Field(default=32 * 1024, gt=0)
    max_output_bytes: int = Field(default=64 * 1024, gt=0)
    timeout_seconds: float = Field(default=5.0, gt=0)
    memory_mb: int = Field(default=256, gt=0)
    cpu_count: float = Field(default=1.0, gt=0)
    pids_limit: int = Field(default=64, gt=0)
    docker_image: str = Field(default="python:3.12-alpine")
    port: int = Field(default=8000, gt=0)
    max_concurrent_jobs: int = Field(default=4, gt=0)
    queue_wait_seconds: float = Field(default=1.0, gt=0)
    docker_seccomp_profile: str | None = None
    docker_apparmor_profile: str | None = None

    @field_validator("docker_image")
    @classmethod
    def validate_image_name(cls, v: str) -> str:
        if ":" not in v:
            msg = "MCP_DOCKER_IMAGE must include an explicit tag"
            raise ValueError(msg)
        if v.endswith(":latest"):
            msg = "MCP_DOCKER_IMAGE=:latest is not allowed for production safety"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def validate_profiles(self) -> Self:
        if self.docker_seccomp_profile:
            path = Path(self.docker_seccomp_profile).expanduser().resolve()
            if not path.is_file():
                msg = f"MCP_DOCKER_SECCOMP_PROFILE file does not exist: {path}"
                raise ValueError(msg)
            self.docker_seccomp_profile = str(path)
        return self


settings = Settings()

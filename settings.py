from dotenv import load_dotenv
import os
from pathlib import Path

from models import Settings

load_dotenv()


def _get_env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid integer for {name}: {raw}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be > 0")
    return value


def _get_env_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid float for {name}: {raw}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be > 0")
    return value


def _get_env_optional_str(name: str) -> str | None:
    raw = os.getenv(name, "").strip()
    return raw if raw else None


def _validate_image_name(image: str) -> None:
    if ":" not in image:
        raise RuntimeError("MCP_DOCKER_IMAGE must include an explicit tag")
    if image.endswith(":latest"):
        raise RuntimeError("MCP_DOCKER_IMAGE=:latest is not allowed for production safety")


def _validate_optional_file(path_value: str | None, env_name: str) -> str | None:
    if path_value is None:
        return None
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"{env_name} file does not exist: {path}")
    return str(path)


def _load_settings() -> Settings:
    api_key = os.getenv("MCP_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MCP_API_KEY is required")

    docker_image = os.getenv("MCP_DOCKER_IMAGE", "python:3.12-alpine")
    _validate_image_name(docker_image)

    seccomp_profile = _validate_optional_file(
        _get_env_optional_str("MCP_DOCKER_SECCOMP_PROFILE"),
        "MCP_DOCKER_SECCOMP_PROFILE",
    )
    apparmor_profile = _get_env_optional_str("MCP_DOCKER_APPARMOR_PROFILE")

    return Settings(
        api_key=api_key,
        max_code_bytes=_get_env_int("MCP_MAX_CODE_BYTES", 32 * 1024),
        max_output_bytes=_get_env_int("MCP_MAX_OUTPUT_BYTES", 64 * 1024),
        timeout_seconds=_get_env_float("MCP_TIMEOUT_SECONDS", 5.0),
        memory_mb=_get_env_int("MCP_MEMORY_MB", 256),
        cpu_count=_get_env_float("MCP_CPU_COUNT", 1.0),
        pids_limit=_get_env_int("MCP_PIDS_LIMIT", 64),
        docker_image=docker_image,
        port=_get_env_int("MCP_PORT", 8000),
        max_concurrent_jobs=_get_env_int("MCP_MAX_CONCURRENT_JOBS", 4),
        queue_wait_seconds=_get_env_float("MCP_QUEUE_WAIT_SECONDS", 1.0),
        docker_seccomp_profile=seccomp_profile,
        docker_apparmor_profile=apparmor_profile,
    )


settings = _load_settings()

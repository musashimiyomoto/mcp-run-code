from dotenv import load_dotenv
import os

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


def _load_settings() -> Settings:
    api_key = os.getenv("MCP_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MCP_API_KEY is required")

    return Settings(
        api_key=api_key,
        max_code_bytes=_get_env_int("MCP_MAX_CODE_BYTES", 32 * 1024),
        max_output_bytes=_get_env_int("MCP_MAX_OUTPUT_BYTES", 64 * 1024),
        timeout_seconds=_get_env_float("MCP_TIMEOUT_SECONDS", 5.0),
        memory_mb=_get_env_int("MCP_MEMORY_MB", 256),
        cpu_count=_get_env_float("MCP_CPU_COUNT", 1.0),
        pids_limit=_get_env_int("MCP_PIDS_LIMIT", 64),
        docker_image=os.getenv("MCP_DOCKER_IMAGE", "python:3.12-alpine"),
        port=_get_env_int("MCP_PORT", 8000),
    )


settings = _load_settings()

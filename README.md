[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![ty](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ty/main/assets/badge/v0.json)](https://github.com/astral-sh/ty)

# Code Executor MCP

Secure code execution sandbox for Model Context Protocol (MCP).

## Features

- Secure Python execution in Docker containers
- Memory, CPU, and PIDS limits
- API Key authentication
- Task-based execution support
- Truncated output handling

## Configuration

Configure using environment variables or a `.env` file:

- `MCP_API_KEY`: Required for authentication
- `MCP_PORT`: Server port (default: 8000)
- `MCP_DOCKER_IMAGE`: Sandbox image (default: python:3.12-alpine)

## Usage

### Local Development

```bash
uv sync
uv run main.py
```

### Docker

```bash
docker build -t mcp-manager .
docker run -p 8000:8000 -v /var/run/docker.sock:/var/run/docker.sock mcp-manager
```

## Testing

```bash
uv run pytest tests.py
```

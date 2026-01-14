# Repository Guidelines

## Project Structure & Modules
- `server.py`: FastMCP server exposing 1Password tools; entrypoint for stdio or HTTP transport.
- `pyproject.toml` / `uv.lock`: Python 3.12+ project metadata and locked dependencies (`fastmcp`, `onepassword-sdk`).
- `Dockerfile`: Container build for running the MCP server.
- `README.md`: Setup and client configuration examples (Claude Desktop, browser-use MCP).
- Config samples: `claude_desktop_config.json.example`, `smithery.yaml` for installation via Smithery.

## Setup, Build, and Run
- Install deps: `uv sync` (uses `pyproject.toml` and `uv.lock`).
- Run locally (stdio): `uv run server.py` with `MCP_TRANSPORT=stdio`.
- Run HTTP transport: `MCP_HOST=0.0.0.0 MCP_PORT=6975 uv run server.py`.
- Required env: `OP_SERVICE_ACCOUNT_TOKEN`; optional `OP_VAULT` (default `AI`), `MCP_PATH` (default `/mcp`).
- Docker: `docker build -t onepassword-mcp .` then `docker run -e OP_SERVICE_ACCOUNT_TOKEN=... -p 6975:6975 onepassword-mcp`.

## Coding Style & Naming Conventions
- Follow PEP 8; use 4-space indentation and clear, imperative function docstrings for tools.
- Prefer explicit type hints on tool signatures and return shapes for agent clarity.
- Keep environment variable names uppercase with underscores; keep tool names descriptive (`get_1password_credentials`, `get_1password_secret`).
- When adding modules, keep them flat in the repo root or a `src/` directory; group tests in `tests/`.

## Testing Guidelines
- Unit tests live in `tests/` using stdlib `unittest` with fake clients.
- Add coverage for each MCP tool (intent resolution, listing, upserts) and a smoke test for server startup if extended.
- Run tests with `uv run python -m unittest discover tests`; keep test names descriptive (`test_resolve_secret_prefers_password`).

## Commit & Pull Request Guidelines
- Use concise, imperative commit messages (`Add HTTP transport defaults`, `Mock 1Password client in tests`).
- Include a brief summary of changes, linked issue/feature, and any env or config impacts in PR descriptions.
- Add screenshots or logs when altering transport behavior or Docker build outputs; note new env vars or defaults.
- Ensure `uv sync` and `uv run pytest` succeed (or document why they cannot) before requesting review.

## Security & Secrets
- Never commit real `OP_SERVICE_ACCOUNT_TOKEN` values or generated credentials; use placeholders in examples.
- Validate new code paths do not log secrets; scrub sensitive env vars from debug output and error messages.

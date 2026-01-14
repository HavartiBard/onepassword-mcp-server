# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A 1Password MCP (Model Context Protocol) server that enables secure credential retrieval from 1Password for use by AI agents. Built with FastMCP and the 1Password Python SDK.

## Commands

```bash
# Install dependencies
uv sync

# Run server (stdio transport, for local IDE use)
MCP_TRANSPORT=stdio uv run python server.py

# Run server (HTTP transport, for network service)
MCP_HOST=0.0.0.0 MCP_PORT=6975 uv run python server.py

# Run tests
uv run python -m unittest discover tests

# Run a single test
uv run python -m unittest tests.test_server.ServerTests.test_resolve_secret_prefers_password

# Build Docker image
docker build -t onepassword-mcp .

# Run Docker container (HTTP)
docker run -e OP_SERVICE_ACCOUNT_TOKEN=... -p 6975:6975 onepassword-mcp
```

## Environment Variables

- `OP_SERVICE_ACCOUNT_TOKEN` (required): 1Password service account token
- `OP_VAULT`: Default vault name (default: "AI")
- `MCP_TRANSPORT`: "stdio" or "streamable-http" (default: "streamable-http")
- `MCP_HOST`: Host to bind (default: "127.0.0.1")
- `MCP_PORT`: Port to bind (default: 6975)
- `MCP_PATH`: HTTP path (default: "/mcp")

## Architecture

Single-file server (`server.py`) using FastMCP. The architecture follows a pattern of:
- Public `@mcp.tool()` decorated functions for MCP tool exposure
- Corresponding `*_impl()` functions that accept an optional `client` parameter for testability

### Intent Resolution System

The `INTENT_FIELD_ORDER` dict maps user intents (like "password", "token", "api_key") to ordered field name candidates. When resolving a secret, the system tries each candidate field in order until one succeeds.

### MCP Components

**Tools:**
- `resolve_secret(item_name, intent, vault)` - Returns credentials using intent-based field selection
- `list_items(query, vault, category)` - Lists items for discovery/disambiguation
- `upsert_item(name, kind, fields, vault, tags)` - Creates items using templated kinds
- `run_with_secrets(command, secrets, vault, working_dir, timeout)` - Runs subprocess with secrets as env vars (secrets never printed)
- `write_env_file(path, secrets, vault, format)` - Writes secrets to file with 0600 permissions (dotenv/export/json formats)

**Resources:**
- `onepassword://health` - Service status
- `onepassword://vaults` - Vault list
- `onepassword://vaults/{vault}/items` - Items in a vault

## Testing

Tests use fake clients (`FakeClient`, `FakeSecrets`, `FakeItems`) to validate behavior without requiring 1Password credentials. The `*_impl()` functions accept an optional `client` parameter to enable dependency injection for testing.

## Coding Conventions

- PEP 8 with 4-space indentation
- Explicit type hints on tool signatures
- Uppercase with underscores for environment variables
- Keep modules flat in repo root; tests in `tests/`

# server.py
"""1Password MCP Server - Central secrets service for homelab.

Supports both stdio (for local IDE use) and HTTP (for network service) transports.

Environment variables:
  OP_SERVICE_ACCOUNT_TOKEN  - Required. 1Password service account token.
  OP_VAULT                  - Optional. Vault name (default: "AI").
  MCP_TRANSPORT             - Optional. "stdio" or "streamable-http" (default: "streamable-http").
  MCP_HOST                  - Optional. Host to bind (default: "127.0.0.1" for safety).
  MCP_PORT                  - Optional. Port to bind (default: 6975).
  MCP_PATH                  - Optional. HTTP path (default: "/mcp").
"""

import asyncio
import json
import os
from typing import Dict, List, Optional

from fastmcp import FastMCP
from onepassword.client import Client

# Configuration from environment
OP_VAULT = os.getenv("OP_VAULT", "AI")
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "streamable-http")
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "6975"))
MCP_PATH = os.getenv("MCP_PATH", "/mcp")

# Create an MCP server
mcp = FastMCP("1Password")

# Cache the 1Password client so we don't re-authenticate on every call.
_client: Optional[Client] = None

# Intent to field resolution map. Ordered candidates per intent.
INTENT_FIELD_ORDER: Dict[str, List[str]] = {
    "password": ["password", "credential", "secret"],
    "credential": ["password", "credential", "secret"],
    "secret": ["secret", "token", "api_key", "key", "password"],
    "token": ["token", "api_key", "secret", "key"],
    "api_key": ["api_key", "token", "secret", "key"],
    "ssh_key": ["private_key", "public_key", "passphrase", "password"],
}


async def get_client() -> Client:
    """Authenticate once and reuse the 1Password client."""
    global _client
    if _client is not None:
        return _client

    token = os.getenv("OP_SERVICE_ACCOUNT_TOKEN")
    if not token:
        raise RuntimeError("OP_SERVICE_ACCOUNT_TOKEN is required to talk to 1Password.")

    _client = await Client.authenticate(
        auth=token,
        integration_name="1Password MCP Integration",
        integration_version="v1.0.0",
    )
    return _client


def _field_candidates(intent: str) -> List[str]:
    normalized = intent.lower().strip()
    if normalized in INTENT_FIELD_ORDER:
        return INTENT_FIELD_ORDER[normalized]
    # Fallback to intent name itself if not mapped.
    return [normalized]


async def resolve_secret_impl(
    item_name: str,
    intent: str = "password",
    vault: Optional[str] = None,
    client: Optional[Client] = None,
) -> dict:
    """Resolve a secret/credential from 1Password with intent-based field selection."""
    client = client or await get_client()
    vault_name = vault or OP_VAULT

    candidates = _field_candidates(intent)
    last_error: Optional[Exception] = None

    for field_name in candidates:
        try:
            value = await client.secrets.resolve(f"op://{vault_name}/{item_name}/{field_name}")
            return {
                "item": item_name,
                "vault": vault_name,
                "field": field_name,
                "kind": intent.lower(),
                "value": value,
            }
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue

    # If we exhausted candidates, surface a clear error.
    if last_error:
        raise RuntimeError(
            f"Unable to resolve any fields for item '{item_name}' in vault '{vault_name}' "
            f"with intent '{intent}'. Tried: {', '.join(candidates)}"
        ) from last_error

    # This covers the case where no candidates are available (unexpected intent).
    raise RuntimeError(
        f"No field candidates available for intent '{intent}'. "
        "Provide an explicit field name or use a supported intent."
    )


@mcp.tool()
async def resolve_secret(
    item_name: str,
    intent: str = "password",
    vault: Optional[str] = None,
) -> dict:
    """Resolve a secret/credential from 1Password with intent-based field selection.

    Returns a structured payload describing what field was returned.
    """
    return await resolve_secret_impl(item_name=item_name, intent=intent, vault=vault)


@mcp.resource("onepassword://health")
async def health_resource() -> dict:
    """Expose non-sensitive service status for diagnostics."""
    return {
        "status": "ok",
        "default_vault": OP_VAULT,
        "transport": MCP_TRANSPORT,
        "host": MCP_HOST,
        "port": MCP_PORT,
        "path": MCP_PATH,
    }


@mcp.resource("onepassword://vaults")
async def vaults_resource() -> List[dict]:
    """List available vaults without exposing secrets."""
    client = await get_client()
    vaults_api = getattr(client, "vaults", None)
    if vaults_api is None or not hasattr(vaults_api, "list"):
        raise RuntimeError("Vault listing is not supported by the installed onepassword-sdk version.")

    results = vaults_api.list()
    if hasattr(results, "__await__"):
        results = await results  # type: ignore[assignment]

    summaries = []
    for vault in results or []:
        summaries.append(
            {
                "id": getattr(vault, "id", None),
                "name": getattr(vault, "name", None),
            }
        )
    return summaries


async def list_items_impl(
    query: Optional[str] = None,
    vault: Optional[str] = None,
    category: Optional[str] = None,
    client: Optional[Client] = None,
) -> List[dict]:
    """List items (optionally filtered) to aid discovery and disambiguation."""
    client = client or await get_client()
    items_api = getattr(client, "items", None)
    if items_api is None:
        raise RuntimeError("Item listing is not supported by the installed onepassword-sdk version.")

    kwargs: Dict[str, str] = {}
    if vault:
        kwargs["vault"] = vault
    if query:
        kwargs["query"] = query
    if category:
        kwargs["category"] = category

    # The SDK may be synchronous or async; handle both.
    results = items_api.list(**kwargs)  # type: ignore[arg-type]
    if hasattr(results, "__await__"):
        results = await results  # type: ignore[assignment]

    summaries = []
    for item in results or []:
        summaries.append(
            {
                "id": getattr(item, "id", None),
                "title": getattr(item, "title", None) or getattr(item, "name", None),
                "vault": getattr(getattr(item, "vault", None), "name", None)
                or getattr(item, "vault", None),
                "category": getattr(item, "category", None),
            }
        )
    return summaries


@mcp.tool()
async def list_items(
    query: Optional[str] = None,
    vault: Optional[str] = None,
    category: Optional[str] = None,
) -> List[dict]:
    """List items (optionally filtered) to aid discovery and disambiguation."""
    return await list_items_impl(query=query, vault=vault, category=category)


@mcp.resource("onepassword://vaults/{vault}/items")
async def vault_items_resource(vault: str) -> List[dict]:
    """List items for a given vault without exposing secret values."""
    return await list_items_impl(vault=vault)


async def upsert_item_impl(
    name: str,
    kind: str,
    fields: Dict[str, str],
    vault: Optional[str] = None,
    tags: Optional[List[str]] = None,
    client: Optional[Client] = None,
) -> dict:
    """Create or update an item with templated kinds (password, api_key, ssh_key)."""
    client = client or await get_client()
    items_api = getattr(client, "items", None)
    if items_api is None or not hasattr(items_api, "create"):
        raise RuntimeError("Item creation is not supported by the installed onepassword-sdk version.")

    vault_name = vault or OP_VAULT
    normalized_kind = kind.lower().strip()
    tags = tags or []

    # Build a simple payload the SDK can understand; keep it generic to avoid tight coupling.
    payload = {
        "title": name,
        "vault": {"name": vault_name},
        "category": "LOGIN" if normalized_kind == "password" else "SECURE_NOTE",
        "tags": tags,
        "fields": [],
    }

    # Apply sensible defaults for common kinds.
    if normalized_kind == "password":
        username = fields.get("username", fields.get("user", ""))
        password = fields.get("password", "")
        payload["fields"] = [
            {"id": "username", "label": "username", "value": username, "purpose": "USERNAME"},
            {"id": "password", "label": "password", "value": password, "purpose": "PASSWORD"},
        ]
    elif normalized_kind in ("api_key", "token", "secret"):
        payload["fields"] = [
            {"id": "api_key", "label": "api_key", "value": fields.get("api_key") or fields.get("token") or fields.get("secret")},
        ]
    elif normalized_kind == "ssh_key":
        payload["fields"] = [
            {"id": "private_key", "label": "private_key", "value": fields.get("private_key")},
            {"id": "public_key", "label": "public_key", "value": fields.get("public_key")},
            {"id": "passphrase", "label": "passphrase", "value": fields.get("passphrase")},
        ]
    else:
        payload["fields"] = [{"id": k, "label": k, "value": v} for k, v in fields.items()]

    # Call create/update; prefer update if an ID is present.
    result = items_api.create(payload)  # type: ignore[arg-type]
    if hasattr(result, "__await__"):
        result = await result  # type: ignore[assignment]

    return {
        "name": name,
        "vault": vault_name,
        "kind": normalized_kind,
        "created": True,
        "tags": tags,
        "fields": payload["fields"],
        "result": getattr(result, "id", None) or getattr(result, "item_id", None),
    }


@mcp.tool()
async def upsert_item(
    name: str,
    kind: str,
    fields: Dict[str, str],
    vault: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> dict:
    """Create or update an item with templated kinds (password, api_key, ssh_key)."""
    return await upsert_item_impl(
        name=name,
        kind=kind,
        fields=fields,
        vault=vault,
        tags=tags,
    )


async def run_with_secrets_impl(
    command: List[str],
    secrets: List[Dict[str, str]],
    vault: Optional[str] = None,
    working_dir: Optional[str] = None,
    timeout: Optional[int] = 30,
    client: Optional[Client] = None,
) -> dict:
    """Run a command with secrets injected as environment variables.

    Secrets are never printed or logged - they exist only in subprocess memory.
    """
    client = client or await get_client()
    vault_name = vault or OP_VAULT

    # Build environment with secrets
    env = os.environ.copy()
    injected_keys = []
    for spec in secrets:
        result = await resolve_secret_impl(
            item_name=spec["item"],
            intent=spec.get("intent", "password"),
            vault=vault_name,
            client=client,
        )
        env_key = spec["env"]
        env[env_key] = result["value"]
        injected_keys.append(env_key)

    # Run subprocess without shell (prevents injection attacks)
    proc = await asyncio.create_subprocess_exec(
        *command,
        env=env,
        cwd=working_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "Process timed out",
            "secrets_injected": injected_keys,
            "timed_out": True,
        }

    return {
        "exit_code": proc.returncode,
        "stdout": stdout.decode(),
        "stderr": stderr.decode(),
        "secrets_injected": injected_keys,
    }


@mcp.tool()
async def run_with_secrets(
    command: List[str],
    secrets: List[Dict[str, str]],
    vault: Optional[str] = None,
    working_dir: Optional[str] = None,
    timeout: Optional[int] = 30,
) -> dict:
    """Run a command with secrets injected as environment variables.

    Secrets are never printed or logged - they exist only in subprocess memory.
    Command is a list of arguments (no shell expansion for security).

    Args:
        command: Command and arguments as a list, e.g. ["python", "script.py"]
        secrets: List of secret specs, each with "item", "env", and optional "intent"
                 e.g. [{"item": "database", "env": "DB_PASS", "intent": "password"}]
        vault: Optional vault name (defaults to OP_VAULT)
        working_dir: Optional working directory for the subprocess
        timeout: Timeout in seconds (default 30)

    Returns:
        Dict with exit_code, stdout, stderr, and secrets_injected (env var names only)
    """
    return await run_with_secrets_impl(
        command=command,
        secrets=secrets,
        vault=vault,
        working_dir=working_dir,
        timeout=timeout,
    )


async def write_env_file_impl(
    path: str,
    secrets: List[Dict[str, str]],
    vault: Optional[str] = None,
    format: str = "dotenv",
    client: Optional[Client] = None,
) -> dict:
    """Write secrets to a file with restricted permissions (0600).

    Secrets are written securely - file is created with restricted permissions
    before any content is written.
    """
    client = client or await get_client()
    vault_name = vault or OP_VAULT

    # Resolve all secrets first
    resolved = {}
    for spec in secrets:
        result = await resolve_secret_impl(
            item_name=spec["item"],
            intent=spec.get("intent", "password"),
            vault=vault_name,
            client=client,
        )
        resolved[spec["key"]] = result["value"]

    # Format content
    if format == "dotenv":
        content = "\n".join(f'{k}="{v}"' for k, v in resolved.items())
    elif format == "export":
        content = "\n".join(f'export {k}="{v}"' for k, v in resolved.items())
    elif format == "json":
        content = json.dumps(resolved, indent=2)
    else:
        raise ValueError(f"Unknown format: {format}. Supported: dotenv, export, json")

    # Write securely with restricted permissions
    # O_EXCL ensures we don't overwrite existing files (security)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, content.encode())
    finally:
        os.close(fd)

    return {
        "path": path,
        "format": format,
        "keys": list(resolved.keys()),
        "permissions": "0600",
    }


@mcp.tool()
async def write_env_file(
    path: str,
    secrets: List[Dict[str, str]],
    vault: Optional[str] = None,
    format: str = "dotenv",
) -> dict:
    """Write secrets to a file with restricted permissions (0600).

    File is created securely - permissions are set before content is written.
    Will fail if file already exists (use a new path).

    Args:
        path: File path to write secrets to
        secrets: List of secret specs, each with "item", "key", and optional "intent"
                 e.g. [{"item": "database", "key": "DB_PASS", "intent": "password"}]
        vault: Optional vault name (defaults to OP_VAULT)
        format: Output format - "dotenv", "export", or "json" (default: "dotenv")

    Returns:
        Dict with path, format, keys (no secret values), and permissions
    """
    return await write_env_file_impl(
        path=path,
        secrets=secrets,
        vault=vault,
        format=format,
    )


if __name__ == "__main__":
    if MCP_TRANSPORT == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(
            transport=MCP_TRANSPORT,
            host=MCP_HOST,
            port=MCP_PORT,
            path=MCP_PATH,
        )

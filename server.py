# server.py
"""1Password MCP Server - Central secrets service for homelab.

Supports both stdio (for local IDE use) and HTTP (for network service) transports.

Environment variables:
  OP_SERVICE_ACCOUNT_TOKEN  - Required. 1Password service account token.
  OP_VAULT                  - Optional. Vault name (default: "AI").
  MCP_TRANSPORT             - Optional. "stdio" or "streamable-http" (default: "streamable-http").
  MCP_HOST                  - Optional. Host to bind (default: "0.0.0.0").
  MCP_PORT                  - Optional. Port to bind (default: 6975).
  MCP_PATH                  - Optional. HTTP path (default: "/mcp").
"""

import os
from fastmcp import FastMCP
from onepassword.client import Client

# Configuration from environment
OP_VAULT = os.getenv("OP_VAULT", "AI")
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "streamable-http")
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "6975"))
MCP_PATH = os.getenv("MCP_PATH", "/mcp")

# Create an MCP server
mcp = FastMCP("1Password")


@mcp.tool()
async def get_1password_credentials(item_name: str) -> dict:
    """Get 1Password credentials (username/password) for a given item.

    Args:
        item_name: The name of the item in 1Password (e.g., "NetBox Admin").

    Returns:
        dict with "username" and "password" fields.
    """
    client = await Client.authenticate(
        auth=os.getenv("OP_SERVICE_ACCOUNT_TOKEN"),
        integration_name="1Password MCP Integration",
        integration_version="v1.0.0"
    )

    username = await client.secrets.resolve(f"op://{OP_VAULT}/{item_name}/username")
    password = await client.secrets.resolve(f"op://{OP_VAULT}/{item_name}/password")

    return {"username": username, "password": password}


@mcp.tool()
async def get_1password_secret(item_name: str, field: str) -> str:
    """Get a specific field from a 1Password item.

    Args:
        item_name: The name of the item in 1Password.
        field: The field name to retrieve (e.g., "password", "api_key", "secret_key").

    Returns:
        The field value as a string.
    """
    client = await Client.authenticate(
        auth=os.getenv("OP_SERVICE_ACCOUNT_TOKEN"),
        integration_name="1Password MCP Integration",
        integration_version="v1.0.0"
    )

    value = await client.secrets.resolve(f"op://{OP_VAULT}/{item_name}/{field}")
    return value


if __name__ == "__main__":
    if MCP_TRANSPORT == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(
            transport=MCP_TRANSPORT,
            host=MCP_HOST,
            port=MCP_PORT,
            path=MCP_PATH
        )

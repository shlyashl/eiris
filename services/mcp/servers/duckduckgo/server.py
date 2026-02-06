import os
import httpx

httpx.TimeoutError = httpx.TimeoutException

from duckduckgo_mcp_server.server import mcp


if __name__ == "__main__":
    mcp.settings.host = os.getenv("MCP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.getenv("MCP_PORT", "9002"))
    mcp.settings.transport_security = None
    mcp.run(transport="streamable-http")

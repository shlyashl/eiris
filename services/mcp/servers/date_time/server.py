from fastmcp import FastMCP
from datetime import datetime
from zoneinfo import ZoneInfo
import os

mcp = FastMCP("datetime-server")

@mcp.tool
def get_current_time() -> str:
    """Вернуть текущие дату и время в ISO формате."""
    return datetime.now(ZoneInfo("Europe/Moscow")).isoformat()

if __name__ == "__main__":
    # For containerized deployment prefer network transport.
    # FastMCP docs recommend pinning to v2: `fastmcp<3`.
    # See: https://gofastmcp.com/getting-started/welcome
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "9001"))
    mcp.run(transport="http", host=host, port=port)

from fastmcp import FastMCP
import json
import os

from services.eiris.common.clickhouse.client import CH

MAX_INPUT_BYTES = 1024 * 1024

mcp = FastMCP("system-prompt")

DB = os.getenv("SYSTEM_PROMPT_DB", "eiris")
TABLE = os.getenv("SYSTEM_PROMPT_TABLE", "system_prompt_state")
ch_ssl = os.getenv("CH_SSL", "false").strip().lower() in ("1", "true", "yes", "y", "on")
ch = CH(
    {
        "host": os.environ["CH_HOST"],
        "port": os.environ["CH_PORT"],
        "user": os.environ["CH_USER"],
        "password": os.environ["CH_PASSWORD"],
        "ssl": ch_ssl,
    }
)


@mcp.tool
def get_system_prompt() -> str:
    sql = (
        f"SELECT prompt FROM {DB}.{TABLE} "
        f"ORDER BY updated_at DESC LIMIT 1 FORMAT JSONEachRow"
    )
    resp = ch._send_to_ch(data=sql.encode("utf-8"))
    for line in resp.text.splitlines():
        if line:
            return str(json.loads(line).get("prompt") or "")
    return ""


@mcp.tool
def set_system_prompt(content: str) -> str:
    data = content.encode("utf-8")
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("content too large")
    ch.insert_data(
        TABLE,
        [{"id": 1, "prompt": content}],
        db_name=DB,
    )
    return "ok"


if __name__ == "__main__":
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "9014"))
    mcp.run(transport="streamable-http", host=host, port=port)

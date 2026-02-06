from fastmcp import FastMCP
import json
import os

from services.eiris.common.clickhouse.client import CH

mcp = FastMCP("clickhouse")

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
def ch_exec(query: str) -> str:
    resp = ch._send_to_ch(data=query.encode("utf-8"))
    return resp.text


@mcp.tool
def ch_select(query: str, limit: int = 10) -> dict:
    sql = query.strip().rstrip(";")
    lead = sql.lstrip().lower()
    if not (lead.startswith("select") or lead.startswith("with")):
        resp = ch._send_to_ch(data=sql.encode("utf-8"))
        return {"text": resp.text}
    lower = sql.lower()
    if "limit" not in lower:
        sql += f"\nLIMIT {limit}"
    if "format" not in lower:
        sql += "\nFORMAT JSONEachRow"
    resp = ch._send_to_ch(data=sql.encode("utf-8"))
    rows = []
    for line in resp.text.splitlines():
        if line:
            rows.append(json.loads(line))
    return {"rows": rows}


if __name__ == "__main__":
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "9016"))
    mcp.run(transport="streamable-http", host=host, port=port)

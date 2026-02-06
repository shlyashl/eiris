from fastmcp import FastMCP
import json
import os
import yaml

from services.eiris.common.clickhouse.client import CH
config_path = os.getenv("CONFIG_PATH", "/config/services.yaml")
with open(config_path, "r", encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh)
DEFAULT_LIMIT = int(cfg["user_messages"]["default_limit"])

mcp = FastMCP("user-messages")

DB = os.getenv("USER_MESSAGES_DB", "eiris")
TABLE = os.getenv("USER_MESSAGES_TABLE", "chat_messages")
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


def _sql_str(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


@mcp.tool
def search_user_messages(query: str, user_id: int, limit: int = DEFAULT_LIMIT) -> dict:
    q = _sql_str(query)
    uid = int(user_id)
    lim = int(limit)
    sql = (
        f"WITH embed_e5('{q}') AS q "
        f"SELECT text, dotProduct(embedding, q) AS score "
        f"FROM {DB}.{TABLE} "
        f"WHERE role = 'user' AND user_id = {uid} "
        f"ORDER BY score DESC "
        f"LIMIT {lim} "
        f"FORMAT JSONEachRow"
    )
    resp = ch._send_to_ch(data=sql.encode("utf-8"))
    texts = []
    for line in resp.text.splitlines():
        if line:
            row = json.loads(line)
            texts.append(str(row.get("text") or ""))
    return {"texts": texts}


if __name__ == "__main__":
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "9018"))
    mcp.run(transport="streamable-http", host=host, port=port)

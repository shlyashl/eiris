from fastmcp import FastMCP
import asyncio
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
import urllib.request

import websockets
from croniter import croniter
import yaml
from services.eiris.common.clickhouse.client import CH

mcp = FastMCP("self-dialog")

config_path = os.getenv("CONFIG_PATH", "/config/services.yaml")
with open(config_path, "r", encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh)
self_cfg = cfg["self_dialog"]
DB = str(self_cfg["db"])
TABLE = str(self_cfg["schedule_table"])
HISTORY_DB = str(self_cfg["history_db"])
HISTORY_TABLE = str(self_cfg["history_table"])
SESSION_ID = str(self_cfg["default_session_id"])
ROLE = "system"
WS_URL = os.getenv("WS_API_URL", "ws://ws-api:8000/ws")
POLL_SEC = float(os.getenv("SCHEDULE_POLL_SEC", "1"))
TG_TOKEN = os.environ["TG_TOKEN"]
TG_API_URL = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"

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


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _sql_str(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _fetch(sql: str) -> list[dict]:
    resp = ch._send_to_ch(data=(sql + "\nFORMAT JSONEachRow").encode("utf-8"))
    rows = []
    for line in resp.text.splitlines():
        if line:
            rows.append(json.loads(line))
    return rows


def _init_table() -> None:
    ddl = f"""
CREATE TABLE IF NOT EXISTS {DB}.{TABLE}
(
    id          String,
    question    String,
    role        String,
    delay_sec   Int32,
    cron        String,
    next_run_at DateTime64(3, 'UTC'),
    active      UInt8,
    session_id  String,
    created_at  DateTime64(3, 'UTC') DEFAULT now64(3),
    updated_at  DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (id)
"""
    ch._send_to_ch(data=ddl.encode("utf-8"))
    try:
        ch._send_to_ch(data=f"ALTER TABLE {DB}.{TABLE} MODIFY ORDER BY (id)".encode("utf-8"))
    except Exception:
        pass
    try:
        ch._send_to_ch(
            data=f"ALTER TABLE {DB}.{TABLE} ADD COLUMN IF NOT EXISTS role String DEFAULT 'user'".encode(
                "utf-8"
            )
        )
    except Exception:
        pass


async def _ask_ws(question: str, session_id: str) -> None:
    req_id = uuid.uuid4().hex
    async with websockets.connect(WS_URL) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "generate",
                    "id": req_id,
                    "prompt": question,
                    "role": ROLE,
                    "session_id": session_id,
                    "chat_id": 0,
                    "user_id": 0,
                    "tg_msg_id": 0,
                }
            )
        )
        while True:
            payload = json.loads(await ws.recv())
            if payload.get("type") == "done":
                break


def _run_ask(question: str, session_id: str) -> None:
    asyncio.run(_ask_ws(question, session_id))


def _mark_next(row: dict) -> None:
    now = datetime.now(timezone.utc)
    rid = _sql_str(str(row["id"]))
    question = str(row.get("question") or "")
    delay_sec = int(row.get("delay_sec") or 0)
    session_id = str(row.get("session_id") or SESSION_ID)
    cron = str(row.get("cron") or "")
    if cron:
        next_dt = croniter(cron, now).get_next(datetime)
        active = 1
    else:
        next_dt = now
        active = 0
    ch._send_to_ch(
        data=f"ALTER TABLE {DB}.{TABLE} DELETE WHERE id = '{rid}'".encode("utf-8")
    )
    ch._send_to_ch(data=f"OPTIMIZE TABLE {DB}.{TABLE} FINAL".encode("utf-8"))
    ch.insert_data(
        TABLE,
        [
            {
                "id": row["id"],
                "question": question,
                "role": ROLE,
                "delay_sec": delay_sec,
                "cron": cron,
                "next_run_at": _fmt_dt(next_dt),
                "active": active,
                "session_id": session_id,
            }
        ],
        db_name=DB,
    )


def _scheduler() -> None:
    while True:
        try:
            due = _fetch(
                f"SELECT id, question, delay_sec, cron, session_id "
                f"FROM {DB}.{TABLE} "
                f"WHERE active = 1 AND next_run_at <= now64(3) "
                f"ORDER BY next_run_at LIMIT 20"
            )
            for row in due:
                _mark_next(row)
                q = str(row.get("question") or "")
                sid = str(row.get("session_id") or SESSION_ID)
                threading.Thread(target=_run_ask, args=(q, sid), daemon=True).start()
        except Exception:
            pass
        time.sleep(POLL_SEC)


@mcp.tool
def self_ask(
    question: str,
    delay_sec: int = 0,
    schedule: bool = False,
    cron: str = "",
) -> dict:
    if cron and not schedule:
        schedule = True
    if schedule and not cron:
        cron = "* * * * *"
    if cron and not croniter.is_valid(cron):
        raise ValueError("bad cron")
    if delay_sec < 0:
        raise ValueError("delay_sec must be >= 0")
    now = datetime.now(timezone.utc)
    if delay_sec > 0:
        next_dt = now + timedelta(seconds=delay_sec)
    elif schedule:
        next_dt = croniter(cron, now).get_next(datetime)
    else:
        next_dt = now
    rid = uuid.uuid4().hex
    ch.insert_data(
        TABLE,
        [
            {
                "id": rid,
                "question": question,
                "role": ROLE,
                "delay_sec": int(delay_sec),
                "cron": cron or "",
                "next_run_at": _fmt_dt(next_dt),
                "active": 1,
                "session_id": SESSION_ID,
            }
        ],
        db_name=DB,
    )
    return {"id": rid, "next_run_at": _fmt_dt(next_dt), "scheduled": bool(schedule)}


@mcp.tool
def send_tg_message(user_id: int, text: str) -> dict:
    data = json.dumps({"chat_id": int(user_id), "text": text}).encode("utf-8")
    req = urllib.request.Request(TG_API_URL, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    msg_id = 0
    if result.get("ok") and result.get("result"):
        msg_id = int(result["result"].get("message_id") or 0)
    ch.insert_data(
        HISTORY_TABLE,
        [
            {
                "session_id": f"tg:{int(user_id)}",
                "chat_id": int(user_id),
                "user_id": 0,
                "role": "assistant",
                "request_id": uuid.uuid4().hex,
                "tg_msg_id": msg_id,
                "text": text,
            }
        ],
        db_name=HISTORY_DB,
    )
    return result


@mcp.tool
def list_self_asks(active_only: bool = True, limit: int = 100) -> dict:
    where = "WHERE active = 1" if active_only else ""
    sql = (
        f"SELECT id, question, role, delay_sec, cron, next_run_at, active, session_id, created_at, updated_at "
        f"FROM {DB}.{TABLE} {where} "
        f"ORDER BY next_run_at LIMIT {int(limit)}"
    )
    rows = _fetch(sql)
    for row in rows:
        row["role"] = ROLE
    return {"rows": rows}


@mcp.tool
def update_self_ask(
    id: str,
    question: str | None = None,
    delay_sec: int | None = None,
    cron: str | None = None,
    active: int | None = None,
) -> dict:
    rid = _sql_str(id)
    rows = _fetch(
        f"SELECT id, question, role, delay_sec, cron, next_run_at, active "
        f"FROM {DB}.{TABLE} WHERE id = '{rid}' LIMIT 1"
    )
    if not rows:
        raise ValueError("not found")
    cur = rows[0]
    new_question = question if question is not None else cur["question"]
    new_delay = int(delay_sec) if delay_sec is not None else int(cur["delay_sec"])
    new_cron = cron if cron is not None else str(cur["cron"] or "")
    new_active = int(active) if active is not None else int(cur["active"])
    if new_cron and not croniter.is_valid(new_cron):
        raise ValueError("bad cron")
    now = datetime.now(timezone.utc)
    if new_active:
        if new_cron:
            next_dt = croniter(new_cron, now).get_next(datetime)
        else:
            next_dt = now + timedelta(seconds=new_delay)
        next_str = _fmt_dt(next_dt)
    else:
        next_str = str(cur["next_run_at"])
    ch._send_to_ch(
        data=f"ALTER TABLE {DB}.{TABLE} DELETE WHERE id = '{rid}'".encode("utf-8")
    )
    ch._send_to_ch(data=f"OPTIMIZE TABLE {DB}.{TABLE} FINAL".encode("utf-8"))
    ch.insert_data(
        TABLE,
        [
            {
                "id": id,
                "question": str(new_question),
                "role": ROLE,
                "delay_sec": new_delay,
                "cron": new_cron,
                "next_run_at": next_str,
                "active": new_active,
                "session_id": SESSION_ID,
            }
        ],
        db_name=DB,
    )
    return {"id": id, "next_run_at": next_str, "active": new_active}


@mcp.tool
def delete_self_ask(id: str, optimize: bool = True) -> str:
    rid = _sql_str(id)
    ch._send_to_ch(
        data=f"ALTER TABLE {DB}.{TABLE} DELETE WHERE id = '{rid}'".encode("utf-8")
    )
    if optimize:
        ch._send_to_ch(data=f"OPTIMIZE TABLE {DB}.{TABLE} FINAL".encode("utf-8"))
    return "ok"


_init_table()
threading.Thread(target=_scheduler, daemon=True).start()


if __name__ == "__main__":
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "9017"))
    mcp.run(transport="streamable-http", host=host, port=port)

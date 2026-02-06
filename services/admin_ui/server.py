from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import json
import os
import yaml

from services.eiris.common.clickhouse.client import CH

app = FastAPI()

config_path = os.getenv("CONFIG_PATH", "/config/services.yaml")
with open(config_path, "r", encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh)
admin_cfg = cfg["admin_ui"]
DB = str(admin_cfg["db"])
SCHEDULE_TABLE = str(admin_cfg["schedule_table"])
SYSTEM_TABLE = str(admin_cfg["system_prompt_table"])
MEMORY_TABLE = str(admin_cfg["user_memory_table"])

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


def _fetch(sql: str) -> list[dict]:
    resp = ch._send_to_ch(data=(sql + "\nFORMAT JSONEachRow").encode("utf-8"))
    rows = []
    for line in resp.text.splitlines():
        if line:
            rows.append(json.loads(line))
    return rows


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _rows(rows: list[dict], keys: list[str]) -> str:
    return "".join(
        "<tr>"
        + "".join(f"<td>{_esc(str(r.get(k, '')))}</td>" for k in keys)
        + "</tr>"
        for r in rows
    )


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    prompts = _fetch(
        f"SELECT prompt, updated_at FROM {DB}.{SYSTEM_TABLE} ORDER BY updated_at DESC LIMIT 5"
    )
    schedules = _fetch(
        f"SELECT id, question, delay_sec, cron, next_run_at, active, session_id, created_at, updated_at "
        f"FROM {DB}.{SCHEDULE_TABLE} ORDER BY updated_at DESC LIMIT 50"
    )
    memories = _fetch(
        f"SELECT id, user_id, kind, period, period_date_start, text, updated_at "
        f"FROM {DB}.{MEMORY_TABLE} ORDER BY updated_at DESC LIMIT 50"
    )
    latest_prompt = str(prompts[0]["prompt"]) if prompts else ""
    return HTMLResponse(
        f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>eiris admin</title>
  <style>
    body {{ font-family: sans-serif; margin: 24px; }}
    textarea {{ width: 100%; min-height: 120px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 8px 0 24px; }}
    th, td {{ border: 1px solid #ccc; padding: 6px 8px; text-align: left; }}
    input {{ width: 100%; }}
    .section {{ margin-bottom: 32px; }}
  </style>
</head>
<body>
  <h1>eiris admin</h1>

  <div class="section">
    <h2>system_prompt_state</h2>
    <form method="post" action="/system_prompt">
      <textarea name="prompt">{_esc(latest_prompt)}</textarea>
      <button type="submit">Save</button>
    </form>
    <table>
      <tr><th>updated_at</th><th>prompt</th></tr>
      {_rows(prompts, ["updated_at", "prompt"])}
    </table>
  </div>

  <div class="section">
    <h2>self_dialog_schedule</h2>
    <form method="post" action="/self_dialog">
      <label>id</label><input name="id"/>
      <label>question</label><input name="question"/>
      <label>delay_sec</label><input name="delay_sec" type="number"/>
      <label>cron</label><input name="cron"/>
      <label>next_run_at (YYYY-MM-DD HH:MM:SS.mmm)</label><input name="next_run_at"/>
      <label>active</label><input name="active" type="number"/>
      <label>session_id</label><input name="session_id"/>
      <button type="submit">Save</button>
    </form>
    <table>
      <tr>
        <th>id</th><th>question</th><th>delay_sec</th><th>cron</th>
        <th>next_run_at</th><th>active</th><th>session_id</th>
        <th>created_at</th><th>updated_at</th>
      </tr>
      {_rows(schedules, ["id", "question", "delay_sec", "cron", "next_run_at", "active", "session_id", "created_at", "updated_at"])}
    </table>
  </div>

  <div class="section">
    <h2>user_memory</h2>
    <form method="post" action="/user_memory">
      <label>id</label><input name="id"/>
      <label>user_id</label><input name="user_id" type="number"/>
      <label>kind</label><input name="kind"/>
      <label>period</label><input name="period"/>
      <label>period_date_start (YYYY-MM-DD)</label><input name="period_date_start"/>
      <label>text</label><input name="text"/>
      <button type="submit">Save</button>
    </form>
    <table>
      <tr>
        <th>id</th><th>user_id</th><th>kind</th><th>period</th>
        <th>period_date_start</th><th>text</th><th>updated_at</th>
      </tr>
      {_rows(memories, ["id", "user_id", "kind", "period", "period_date_start", "text", "updated_at"])}
    </table>
  </div>
</body>
</html>
"""
    )


@app.post("/system_prompt")
def set_system_prompt(prompt: str = Form(...)) -> RedirectResponse:
    ch.insert_data(SYSTEM_TABLE, [{"id": 1, "prompt": prompt}], db_name=DB)
    return RedirectResponse("/", status_code=303)


@app.post("/self_dialog")
def save_self_dialog(
    id: str = Form(...),
    question: str = Form(...),
    delay_sec: int = Form(...),
    cron: str = Form(""),
    next_run_at: str = Form(...),
    active: int = Form(...),
    session_id: str = Form(...),
) -> RedirectResponse:
    rid = _sql_str(id)
    ch._send_to_ch(
        data=f"ALTER TABLE {DB}.{SCHEDULE_TABLE} DELETE WHERE id = '{rid}'".encode("utf-8")
    )
    ch._send_to_ch(data=f"OPTIMIZE TABLE {DB}.{SCHEDULE_TABLE} FINAL".encode("utf-8"))
    ch.insert_data(
        SCHEDULE_TABLE,
        [
            {
                "id": id,
                "question": question,
                "role": "system",
                "delay_sec": int(delay_sec),
                "cron": cron,
                "next_run_at": next_run_at,
                "active": int(active),
                "session_id": session_id,
            }
        ],
        db_name=DB,
    )
    return RedirectResponse("/", status_code=303)


@app.post("/user_memory")
def save_user_memory(
    id: str = Form(...),
    user_id: int = Form(...),
    kind: str = Form(...),
    period: str = Form(...),
    period_date_start: str = Form(...),
    text: str = Form(...),
) -> RedirectResponse:
    ch.insert_data(
        MEMORY_TABLE,
        [
            {
                "id": id,
                "user_id": int(user_id),
                "kind": kind,
                "period": period,
                "period_date_start": period_date_start,
                "text": text,
            }
        ],
        db_name=DB,
    )
    return RedirectResponse("/", status_code=303)


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("ADMIN_HOST", "0.0.0.0")
    port = int(os.getenv("ADMIN_PORT", "9020"))
    uvicorn.run(app, host=host, port=port)

import asyncio
import http.client
import json
import os
import threading
import yaml
from urllib.parse import urljoin, urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services.eiris.common import get_logger
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from services.eiris.common.clickhouse.client import CH

router = APIRouter()
logger = get_logger(__name__)


async def _call_mcp_tool(mcp_url: str, name: str, arguments: dict) -> tuple[str, bool]:
    transport = StreamableHttpTransport(url=mcp_url)
    client = Client(transport)
    try:
        async with client:
            result = await client.call_tool(name, arguments)
    except Exception as e:
        return str(e), True
    is_error = bool(getattr(result, "isError", False) or getattr(result, "is_error", False))
    if getattr(result, "data", None) is not None:
        return str(result.data), is_error
    content = getattr(result, "content", None)
    if content:
        first = content[0]
        return getattr(first, "text", str(first)), is_error
    return "", is_error


def _slot_action(base_url: str, slot_id: int, action: str, filename: str) -> None:
    url = urljoin(base_url.rstrip("/") + "/", f"slots/{slot_id}?action={action}")
    parsed = urlparse(url)
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=600)
    try:
        body = json.dumps({"filename": filename})
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
    finally:
        conn.close()


def _stream_llama_server_chat(
    base_url: str,
    messages: list[dict[str, str]],
    tools: list[dict],
    max_tokens: int,
    temperature: float,
    cache_reuse: int,
    slot_id: int,
    cancel_flag: threading.Event,
):
    logger.info("Подключаемся к llama-server")
    url = urljoin(base_url.rstrip("/") + "/", "v1/chat/completions") 
    logger.debug("LLAMA URL: %s", url)
    parsed = urlparse(url)  # разбираем URL на части
    logger.debug("LLAMA HOST=%s PORT=%s", parsed.hostname, parsed.port or 80)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=600)
    tool_calls: dict[int, dict] = {}
    tool_calls_emitted = False
    payload_obj = {  # собираем тело запроса
        "model": os.getenv("model"),
        "messages": messages,
        "stream": True,  # включаем поток
        "timings_per_token": True,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "reasoning_effort": "high",
        "tools": tools,
        "tool_choice": "auto",
        "cache_prompt": bool(slot_id >= 0),
        "n_cache_reuse": cache_reuse,
        "id_slot": slot_id,
    }
    payload = json.dumps(payload_obj)
    logger.info("LLAMA INPUT JSON: %s", json.dumps(payload_obj, ensure_ascii=False))
    conn.request("POST", parsed.path, body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse() 
    try:
        while True:  # читаем стрим построчно
            if cancel_flag.is_set():  # если отмена
                break  # выходим
            line = resp.readline()  # читаем строку
            if not line:  # поток закончился
                break  # выходим
            text = line.decode("utf-8", "ignore").strip()  # декодируем
            logger.debug(f"LLAMA RAW CHUNK{text}")
            if not text.startswith("data:"):  # игнорим не-данные
                continue
            data = text[5:].strip()  # убираем "data:"
            if data == "[DONE]":  # конец стрима
                break
            obj = json.loads(data)  # парсим JSON
            choice = obj.get("choices", [{}])[0]
            finish_reason = choice.get("finish_reason")
            timings = obj.get("timings")
            if timings:
                yield {"timings": timings}
            delta = choice.get("delta") or {}  # берем delta
            tool_calls_delta = delta.get("tool_calls") or []
            if tool_calls_delta:
                for call in tool_calls_delta:
                    idx = call.get("index", 0)
                    entry = tool_calls.setdefault(
                        idx,
                        {
                            "id": call.get("id") or f"call_{idx}",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    function = call.get("function") or {}
                    if function.get("name"):
                        entry["function"]["name"] = function["name"]
                    if function.get("arguments"):
                        entry["function"]["arguments"] += function["arguments"]
                if finish_reason in ("tool", "tool_calls") and tool_calls:
                    tool_calls_emitted = True
                    yield {"tool_calls": list(tool_calls.values())}
                    break
                continue
            function_call = delta.get("function_call")
            if function_call:
                entry = tool_calls.setdefault(
                    0,
                    {
                        "id": function_call.get("id") or "call_0",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if function_call.get("name"):
                    entry["function"]["name"] = function_call["name"]
                if function_call.get("arguments"):
                    entry["function"]["arguments"] += function_call["arguments"]
                continue
            reasoning = delta.get("reasoning_content") or ""
            content = delta.get("content") or ""
            if reasoning:
                yield {"channel": "commentary", "text": reasoning}
            elif content:
                yield {"channel": "final", "text": content}
            if finish_reason in ("tool", "tool_calls") and tool_calls:
                tool_calls_emitted = True
                yield {"tool_calls": list(tool_calls.values())}
                break
        if tool_calls and not tool_calls_emitted:
            yield {"tool_calls": list(tool_calls.values())}
    finally:
        conn.close()


@router.websocket("/ws")
async def ws_llama(ws: WebSocket):
    logger.info("Начинаем обработку WebSocket")
    await ws.accept()
    logger.info("WebSocket принят")
    server_url = f"http://{os.getenv('llama_server_host')}:{os.getenv('llama_server_port')}"
    logger.info("Собрали URL llama-server: %s", server_url)
    current_id = None
    cancel_flag = threading.Event()
    gen_task = None
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
    history_db = os.environ["history_db"]
    history_table = os.environ["history_table"]
    history_limit = int(os.environ["history_limit"])
    history_days_limit = int(os.environ["history_days_limit"])
    user_memory_db = os.environ["user_memory_db"]
    user_memory_table = os.environ["user_memory_table"]
    mcp_servers = yaml.safe_load(os.environ["mcp_servers"])
    mcp_tool_map = yaml.safe_load(os.environ["mcp_tool_map"])
    tools = yaml.safe_load(os.environ["tools"])
    logger.debug(
        "CH CREDS host=%s port=%s user=%s ssl=%s",
        os.environ["CH_HOST"],
        os.environ["CH_PORT"],
        os.environ["CH_USER"],
        ch_ssl,
    )

    async def run_generate(
        req_id: str,
        prompt: str,
        prompt_role: str,
        max_tokens: int,
        temperature: float,
        session_id: str,
        chat_id: int,
        user_id: int,
        tg_msg_id: int,
    ):
        nonlocal current_id  # даем доступ к current_id из ws_llama
        current_id = req_id  # фиксируем, какой запрос сейчас активный
        cancel_flag.clear()  # сбрасываем флаг отмены перед новым запуском
        cache_reuse = int(os.environ["cache_reuse"])
        slot_id = 0 if session_id else -1

        full_text = []  # собираем полный текст
        comment_text = []  # собираем reasoning
        ws_closed = False
        async def safe_send(payload: dict) -> None:
            nonlocal ws_closed
            if ws_closed:
                return
            try:
                await ws.send_json(payload)
            except WebSocketDisconnect:
                ws_closed = True
            except RuntimeError:
                ws_closed = True
        def _fetch_rows(sql: str) -> list[dict]:
            resp = ch._send_to_ch(data=(sql + "\nFORMAT JSONEachRow").encode("utf-8"))
            rows = []
            for line in resp.text.splitlines():
                if line:
                    rows.append(json.loads(line))
            return rows
        history = []
        if chat_id:
            history = _fetch_rows(
                f"SELECT ts, role, text FROM {history_db}.{history_table} "
                f"WHERE chat_id = {int(chat_id)} "
                f"AND role IN ('user','assistant') "
                f"AND ts >= toDateTime(toDate(now()) - {history_days_limit}) "
                f"ORDER BY ts"
            )
        messages = []
        new_rows = []
        server = mcp_servers[mcp_tool_map["get_system_prompt"]]
        mcp_url = f"http://{server['host']}:{server['port']}/mcp"
        system_prompt, is_error = await _call_mcp_tool(mcp_url, "get_system_prompt", {})
        if is_error:
            system_prompt = ""
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
            if session_id:
                new_rows.append(
                    {
                        "session_id": session_id,
                        "chat_id": chat_id,
                        "user_id": 0,
                        "role": "system",
                        "request_id": req_id,
                        "tg_msg_id": 0,
                        "text": system_prompt,
                    }
                )
        if user_id:
            facts_rows = _fetch_rows(
                f"SELECT text FROM {user_memory_db}.{user_memory_table} FINAL "
                f"WHERE user_id = {int(user_id)} AND kind = 'facts' "
                "ORDER BY updated_at DESC LIMIT 1"
            )
            if facts_rows:
                facts_text = str(facts_rows[0].get("text") or "")
                if facts_text:
                    messages.append({"role": "developer", "content": f"User facts:\n{facts_text}"})
            summary_rows = _fetch_rows(
                f"SELECT text FROM {user_memory_db}.{user_memory_table} FINAL "
                f"WHERE user_id = {int(user_id)} AND kind = 'dialog_summary' "
                "ORDER BY updated_at DESC LIMIT 1"
            )
            if summary_rows:
                summary_text = str(summary_rows[0].get("text") or "")
                if summary_text:
                    messages.append(
                        {"role": "system", "content": f"Dialog summary (last 3 days):\n{summary_text}"}
                    )
        messages.extend(
            {"role": r["role"], "content": r["text"]}
            for r in history
            if r["role"] in ("user", "assistant", "system", "developer", "tool")
        )
        messages.append({"role": prompt_role, "content": prompt})
        if session_id:
            new_rows.append(
                {
                    "session_id": session_id,
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "role": prompt_role,
                    "request_id": req_id,
                    "tg_msg_id": tg_msg_id,
                    "text": prompt,
                }
            )
        if session_id:
            _slot_action(server_url, slot_id, "restore", session_id)
        if session_id and new_rows:
            logger.debug(
                "CH INSERT messages session_id=%s db=%s table=%s",
                session_id,
                history_db,
                history_table,
            )
            await asyncio.to_thread(
                ch.insert_data,
                history_table,
                new_rows,
                db_name=history_db,
            )

        timings = None
        stop_reason = None
        tool_loops = 0
        while True:
            q: asyncio.Queue[object] = asyncio.Queue()
            cancel_flag.clear()

            def worker():  # отдельный поток, чтобы не блокировать event loop
                logger.info("Старт потока для чтения llama-server")
                for piece in _stream_llama_server_chat(  # читаем стрим от llama-server
                    server_url,
                    messages,
                    tools,
                    max_tokens,
                    temperature,
                    cache_reuse,
                    slot_id,
                    cancel_flag,
                ):
                    logger.debug("QUEUE PUT: %s", piece)
                    q.put_nowait(piece)  # кладем текст чанка в очередь
                logger.debug("QUEUE PUT: END")
                q.put_nowait(None)  # кладем маркер завершения потока

            threading.Thread(target=worker, daemon=True).start()  # запускаем поток сразу

            tool_calls = None
            while True:  # обрабатываем очередь пока не получим маркер конца
                piece = await q.get()  # ждем следующий элемент из очереди
                if piece is None:  # None означает, что стрим завершен
                    break  # выходим из цикла чтения
                if isinstance(piece, dict):
                    if "tool_calls" in piece:
                        tool_calls = piece["tool_calls"]
                        break
                    timings = piece.get("timings") or timings
                    text = piece.get("text")
                    if text is None:
                        continue
                    channel = piece.get("channel", "final")
                    if channel == "final":
                        full_text.append(text)
                    elif channel == "commentary":
                        comment_text.append(text)
                    await safe_send(
                        {"type": "delta", "id": req_id, "text": text, "channel": channel}
                    )
                    continue
                full_text.append(piece)  # копим полный ответ
                await safe_send(
                    {"type": "delta", "id": req_id, "text": piece, "channel": "final"}
                )

            if tool_calls:
                tool_loops += 1
                if session_id and not cancel_flag.is_set():
                    await asyncio.to_thread(
                        ch.insert_data,
                        history_table,
                        [
                            {
                                "session_id": session_id,
                                "chat_id": chat_id,
                                "user_id": 0,
                                "role": "tool_call",
                                "request_id": req_id,
                                "tg_msg_id": 0,
                                "text": json.dumps(tool_calls),
                            }
                        ],
                        db_name=history_db,
                    )
                await safe_send(
                    {"type": "tool_call", "id": req_id, "tool_calls": tool_calls}
                )
                tool_results = []
                error_text = None
                for call in tool_calls:
                    function = call.get("function") or {}
                    name = function.get("name") or ""
                    if not name:
                        continue
                    args_raw = function.get("arguments") or ""
                    args = json.loads(args_raw) if args_raw else {}
                    server_key = mcp_tool_map[name]
                    server = mcp_servers[server_key]
                    mcp_url = f"http://{server['host']}:{server['port']}/mcp"
                    result, is_error = await _call_mcp_tool(mcp_url, name, args)
                    tool_results.append(
                        {"id": call.get("id") or "", "name": name, "content": result}
                    )
                    if is_error and error_text is None:
                        error_text = result
                if session_id and not cancel_flag.is_set():
                    await asyncio.to_thread(
                        ch.insert_data,
                        history_table,
                        [
                            {
                                "session_id": session_id,
                                "chat_id": chat_id,
                                "user_id": 0,
                                "role": "tool_result",
                                "request_id": req_id,
                                "tg_msg_id": 0,
                                "text": json.dumps(tool_results),
                            }
                        ],
                        db_name=history_db,
                    )
                await safe_send(
                    {"type": "tool_result", "id": req_id, "results": tool_results}
                )
                if error_text:
                    full_text.append(error_text)
                    await safe_send(
                        {"type": "delta", "id": req_id, "text": error_text, "channel": "final"}
                    )
                    stop_reason = "error"
                    break
                if session_id.startswith("self:") and tool_loops >= 50:
                    final_text = json.dumps(tool_results, ensure_ascii=False)
                    full_text.append(final_text)
                    await safe_send(
                        {"type": "delta", "id": req_id, "text": final_text, "channel": "final"}
                    )
                    stop_reason = "tool_loop"
                    break
                messages.append({"role": "assistant", "tool_calls": tool_calls})
                for r in tool_results:
                    tool_msg = {"role": "tool", "content": r["content"]}
                    if r["id"]:
                        tool_msg["tool_call_id"] = r["id"]
                    messages.append(tool_msg)
                continue
            break

        reason = stop_reason or ("cancel" if cancel_flag.is_set() else "stop")  # почему завершили генерацию
        assistant_text = "".join(full_text)
        logger.info(
            "LLAMA OUTPUT JSON: %s",
            json.dumps(
                {"assistant": assistant_text, "comment": "".join(comment_text)},
                ensure_ascii=False,
            ),
        )
        if session_id and not cancel_flag.is_set():
            _slot_action(server_url, slot_id, "save", session_id)
        if comment_text and session_id and not cancel_flag.is_set():
            await asyncio.to_thread(
                ch.insert_data,
                history_table,
                [
                    {
                        "session_id": session_id,
                        "chat_id": chat_id,
                        "user_id": 0,
                        "role": "comment",
                        "request_id": req_id,
                        "tg_msg_id": 0,
                        "text": "".join(comment_text),
                    }
                ],
                db_name=history_db,
            )
        if assistant_text and session_id and not cancel_flag.is_set():
            logger.debug(
                "CH INSERT assistant session_id=%s db=%s table=%s",
                session_id,
                history_db,
                history_table,
            )
            await asyncio.to_thread(
                ch.insert_data,
                history_table,
                [
                    {
                        "session_id": session_id,
                        "chat_id": chat_id,
                        "user_id": 0,
                        "role": "assistant",
                        "request_id": req_id,
                        "tg_msg_id": 0,
                        "text": assistant_text,
                    }
                ],
                db_name=history_db,
            )
        msg = {"type": "done", "id": req_id, "reason": reason}
        if timings:
            msg["pp"] = timings.get("prompt_per_second")
            msg["tg"] = timings.get("predicted_per_second")
        await safe_send(msg)  # финальный ответ
        current_id = None  # очищаем активный id после завершения

    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")

            if mtype == "generate":
                logger.info("Старт генерации")
                if gen_task and not gen_task.done():
                    cancel_flag.set()
                    await gen_task

                req_id = str(msg.get("id", "1"))
                prompt = str(msg.get("prompt", ""))
                prompt_role = str(msg.get("role") or "user")
                session_id = str(msg.get("session_id") or "")
                chat_id = int(msg.get("chat_id") or 0)
                user_id = int(msg.get("user_id") or 0)
                tg_msg_id = int(msg.get("tg_msg_id") or 0)
                max_tokens = int(os.environ["max_tokens"])
                temperature = float(os.environ["temperature"])
                gen_task = asyncio.create_task(
                    run_generate(
                        req_id,
                        prompt,
                        prompt_role,
                        max_tokens,
                        temperature,
                        session_id,
                        chat_id,
                        user_id,
                        tg_msg_id,
                    )
                )

            elif mtype == "cancel":
                logger.info("Отмена генерации")
                req_id = str(msg.get("id")) 
                if current_id and req_id == current_id:
                    cancel_flag.set()
    except WebSocketDisconnect:
        cancel_flag.set()
        if gen_task and not gen_task.done():
            await gen_task

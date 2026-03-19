from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone

import websockets
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import Message
import telegramify_markdown
import telegramify_markdown.customize as customize
from services.eiris.common import get_logger
customize.strict_markdown = False

logger = get_logger(__name__)

_HARMONY_TOKENS_RE = re.compile(r"<\|[^|]*\|>")
_XML_TAG_RE = re.compile(r"</?(analysis|final|commentary|message)>", re.IGNORECASE)


def clean_delta(s: str) -> str:
    s = _HARMONY_TOKENS_RE.sub("", s)
    s = _XML_TAG_RE.sub("", s)
    return s


def clip_tail(text: str, limit: int) -> str:
    return text[-limit:] if len(text) > limit else text


def _escape_dots(s: str) -> str:
    return re.sub(r"(?<!\\)\.", r"\\.", s)


def format_for_tg(
    *,
    think: str,
    final: str,
    tg_think_limit: int,
    tg_final_limit: int,
    tg_placeholder: str,
    max_tg_len: int,
) -> str:
    think_s = clip_tail(think, tg_think_limit).strip()
    final_s = clip_tail(final, tg_final_limit).strip()
    if think_s:
        raw = f"```\n{think_s}\n```\n{final_s or tg_placeholder}"
    else:
        raw = final_s or tg_placeholder
    rendered = telegramify_markdown.markdownify(raw) or tg_placeholder
    rendered = _escape_dots(rendered)
    if len(rendered) > max_tg_len:
        rendered = rendered[-max_tg_len:]
        if rendered.endswith("\\"):
            rendered = rendered[:-1]
    return rendered


async def _stream(
    ws_url: str,
    message: Message,
    prompt: str,
    *,
    prompt_role: str,
    edit_throttle_sec: float,
    stream_rotate_sec: float,
    tg_placeholder: str,
    tg_think_limit: int,
    tg_final_limit: int,
    max_tg_len: int,
):
    out = await message.answer(tg_placeholder)
    acc_think = ""
    acc_final = ""
    last_sent = ""
    last_edit = 0.0
    last_rotate = 0.0
    final_started = False
    req_id = uuid.uuid4().hex

    async def try_edit(text: str):
        try:
            await out.edit_text(text)
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                return
            try:
                await out.edit_text(text, parse_mode=None)
            except TelegramBadRequest as e2:
                if "not modified" in str(e2).lower():
                    return
                raise

    async def push_rendered(rendered: str):
        nonlocal out, last_sent, last_edit, last_rotate
        if rendered == last_sent:
            return
        now = asyncio.get_running_loop().time()
        rotate = stream_rotate_sec > 0 and (now - last_rotate >= stream_rotate_sec)
        if not rotate:
            try:
                await try_edit(rendered)
                last_sent, last_edit = rendered, now
                return
            except TelegramRetryAfter:
                rotate = True
        old = out
        out = await message.answer(rendered)
        last_rotate = now
        last_sent, last_edit = rendered, now
        try:
            await old.delete()
        except TelegramBadRequest:
            pass

    try:
        async with websockets.connect(ws_url) as ws:
            user_id = message.from_user.id if message.from_user else 0
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            if prompt_role == "developer":
                prompt = f"Разработчик Telegram id={user_id} написал в {ts}:\n{prompt}"
            else:
                prompt = f"Пользователь Telegram id={user_id} написал в {ts}:\n{prompt}"
            await ws.send(
                json.dumps(
                    {
                        "type": "generate",
                        "id": req_id,
                        "prompt": prompt,
                        "role": prompt_role,
                        "session_id": f"tg:{message.chat.id}",
                        "chat_id": message.chat.id,
                        "user_id": user_id,
                        "tg_msg_id": message.message_id,
                    }
                )
            )
            last_rotate = asyncio.get_running_loop().time()
            while True:
                payload = json.loads(await ws.recv())
                if payload.get("type") == "delta":
                    ch = payload.get("channel", "final")
                    text = clean_delta(payload.get("text") or "")
                    if not text:
                        continue
                    if ch == "commentary" and not final_started:
                        acc_think += text
                    else:
                        acc_final += text
                        final_started = True
                    now = asyncio.get_running_loop().time()
                    if now - last_edit >= edit_throttle_sec:
                        rendered = format_for_tg(
                            think="" if final_started else acc_think,
                            final=acc_final,
                            tg_think_limit=tg_think_limit,
                            tg_final_limit=tg_final_limit,
                            tg_placeholder=tg_placeholder,
                            max_tg_len=max_tg_len,
                        )
                        await push_rendered(rendered)
                elif payload.get("type") == "done":
                    break
            rendered = format_for_tg(
                think="",
                final=acc_final,
                tg_think_limit=tg_think_limit,
                tg_final_limit=tg_final_limit,
                tg_placeholder=tg_placeholder,
                max_tg_len=max_tg_len,
            )
            await push_rendered(rendered)
    except Exception:
        logger.exception("TG stream error")
        try:
            await out.edit_text("⚙️ что-то техническое произошло, напиши ещё раз", parse_mode=None)
        except Exception:
            await message.answer("⚙️ что-то техническое произошло, напиши ещё раз", parse_mode=None)


def build_router(
    ws_url: str,
    allowed_usernames: list[str] | None,
    developer_usernames: list[str] | None,
    *,
    edit_throttle_sec: float,
    stream_rotate_sec: float,
    tg_placeholder: str,
    tg_think_limit: int,
    tg_final_limit: int,
    max_tg_len: int,
) -> Router:
    allowed = (
        None
        if allowed_usernames is None
        else {str(u).lstrip("@").lower() for u in allowed_usernames if u}
    )
    dev = (
        None
        if not developer_usernames
        else {str(u).lstrip("@").lower() for u in developer_usernames if u}
    )
    router = Router()

    @router.message(F.text)
    async def on_text(message: Message):
        if allowed is not None:
            user = message.from_user
            username = "" if not user or not user.username else user.username
            if not username or username.lstrip("@").lower() not in allowed:
                return
        uname = (
            ""
            if not message.from_user or not message.from_user.username
            else message.from_user.username.lstrip("@").lower()
        )
        prompt_role = "developer" if dev and uname in dev else "user"
        await _stream(
            ws_url,
            message,
            message.text,
            prompt_role=prompt_role,
            edit_throttle_sec=edit_throttle_sec,
            stream_rotate_sec=stream_rotate_sec,
            tg_placeholder=tg_placeholder,
            tg_think_limit=tg_think_limit,
            tg_final_limit=tg_final_limit,
            max_tg_len=max_tg_len,
        )

    return router

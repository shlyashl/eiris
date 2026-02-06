import asyncio
import os
import yaml
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .tg_llm_stream import build_router

async def main():
    config_path = os.getenv("CONFIG_PATH", "/config/services.yaml")
    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)["bot"]

    bot = Bot(
        token=os.environ["TG_TOKEN"],
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2),
    )

    dp = Dispatcher()
    dp.include_router(
        build_router(
            str(cfg["ws_url"]),
            cfg.get("tg_allowed_usernames"),
            edit_throttle_sec=float(cfg["tg_edit_throttle_sec"]),
            tg_placeholder=str(cfg["tg_placeholder"]),
            tg_think_limit=int(cfg["tg_think_limit"]),
            tg_final_limit=int(cfg["tg_final_limit"]),
            max_tg_len=int(cfg["tg_max_len"]),
        )
    )

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

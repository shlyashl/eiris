import os
import uvicorn
from fastapi import FastAPI
import yaml

from .llama_ws import router
from services.eiris.common import get_logger


app = FastAPI()
app.include_router(router)
logger = get_logger(__name__)


def main() -> None:
    logger.info("Читаем конфиг")
    config_path = os.getenv("CONFIG_PATH", "/config/services.yaml")
    with open(config_path, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
        settings = config.get("ws_api")
        ch = config.get("clickhouse")
    logger.debug("CONFIG ws_api: %s", settings)

    logger.info("Кладем настройки в env")
    for key, value in settings.items():
        os.environ[key] = str(value)
    os.environ["user_memory_db"] = str(ch["user_memory_db"])
    os.environ["user_memory_table"] = str(ch["user_memory_table"])
    logger.debug("ENV ws_api: %s", settings)

    logger.info("Запускаем WS API")
    logger.debug("UVICORN: host=%s port=%s", settings["host"], settings["port"])
    uvicorn.run(app, host=settings["host"], port=settings["port"])


if __name__ == "__main__":
    main()

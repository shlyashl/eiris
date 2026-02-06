import logging
import os


def get_logger(name: str, level: str | None = None) -> logging.Logger:
    level_name = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    log_level = getattr(logging, level_name, logging.DEBUG)
    log_format = os.getenv(
        "LOG_FORMAT",
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    date_format = os.getenv("LOG_DATEFMT", "%Y-%m-%d %H:%M:%S")
    logging.basicConfig(level=log_level, format=log_format, datefmt=date_format)
    return logging.getLogger(name)

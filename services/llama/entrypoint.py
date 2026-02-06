#!/usr/bin/env python3
import os
import yaml


def main() -> None:
    config_path = os.getenv("CONFIG_PATH", "/config/services.yaml")
    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)["llama_server"]

    model_path = str(cfg["model_path"])
    model_file = str(cfg["model_file"])
    binary_path = str(cfg["binary_path"])
    chat_template_path = str(cfg["chat_template_path"])
    host = str(cfg["host"])
    port = str(cfg["port"])
    args = str(cfg.get("args", "")).split()

    cmd = [
        binary_path,
        "-m",
        f"{model_path.rstrip('/')}/{model_file.lstrip('/')}",
        "--host",
        host,
        "--port",
        port,
        "--chat-template-file",
        chat_template_path,
        *args,
    ]

    os.execv(cmd[0], cmd)


if __name__ == "__main__":
    main()

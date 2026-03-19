import os
import subprocess

import yaml
from fastmcp import FastMCP

config_path = os.getenv("CONFIG_PATH", "/config/services.yaml")
with open(config_path, "r", encoding="utf-8") as fh:
    cc = yaml.safe_load(fh)["compose_control"]

PROJECT = str(cc["project_dir"])
COMPOSE_FILE = str(cc["compose_file"])
PROJECT_NAME = str(cc["project_name"])
ALLOWED = frozenset(cc["allowed_services"])

mcp = FastMCP("compose-restart")


def _compose_args(*tail: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        PROJECT_NAME,
        "-f",
        os.path.join(PROJECT, COMPOSE_FILE),
        *tail,
    ]


def _run(argv: list[str]) -> dict:
    r = subprocess.run(
        argv,
        cwd=PROJECT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return {
        "exit_code": r.returncode,
        "stdout": r.stdout[-12000:],
        "stderr": r.stderr[-12000:],
    }


@mcp.tool
def compose_restart(services: list[str] | None = None) -> dict:
    """docker compose restart. Пустой список — все сервисы из allowed_services в конфиге."""
    names = [] if services is None else list(services)
    if not names:
        targets = sorted(ALLOWED)
    else:
        bad = [s for s in names if s not in ALLOWED]
        if bad:
            raise ValueError(f"not allowed: {bad}")
        targets = names
    if not targets:
        raise ValueError("allowed_services пуст")
    argv = _compose_args("restart", *targets)
    out = _run(argv)
    out["argv"] = argv
    return out


if __name__ == "__main__":
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "9019"))
    mcp.run(transport="streamable-http", host=host, port=port)

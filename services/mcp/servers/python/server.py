from fastmcp import FastMCP
import os
import sys
import time
import subprocess
import yaml

config_path = os.getenv("CONFIG_PATH", "/config/services.yaml")
with open(config_path, "r", encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh)
BASE_DIR = str(cfg["python"]["base_dir"])
MAX_INPUT_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_OUTPUT = 200_000

VENV_DIR = os.path.join(BASE_DIR, ".venv")
PY_BIN = os.path.join(VENV_DIR, "bin", "python")
PIP_BIN = os.path.join(VENV_DIR, "bin", "pip")

mcp = FastMCP("python")


def _safe_path(path: str) -> str:
    if path in ("", "."):
        rel = ""
    else:
        rel = path
    if ".." in rel.split(os.sep):
        raise ValueError("bad path")
    if rel.startswith("/"):
        full = os.path.realpath(rel)
    else:
        full = os.path.realpath(os.path.join(BASE_DIR, rel))
    if not (full == BASE_DIR or full.startswith(BASE_DIR + os.sep)):
        raise ValueError("bad path")
    return full


def _ensure_venv() -> None:
    if not os.path.isdir(VENV_DIR):
        subprocess.run([sys.executable, "-m", "venv", VENV_DIR], check=True)


def _trim(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars]


@mcp.tool
def write_file(path: str, content: str) -> str:
    data = content.encode("utf-8")
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("content too large")
    full = _safe_path(path)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return "ok"


@mcp.tool
def read_file(path: str) -> str:
    full = _safe_path(path)
    if os.path.getsize(full) > MAX_INPUT_BYTES:
        raise ValueError("file too large")
    with open(full, "r", encoding="utf-8", errors="ignore") as f:
        return _trim(f.read(DEFAULT_MAX_OUTPUT), DEFAULT_MAX_OUTPUT)


@mcp.tool
def list_files(path: str) -> list[str]:
    full = _safe_path(path)
    names = os.listdir(full)
    return sorted(names)


@mcp.tool
def run_python(
    path: str,
    args: list[str] | None = None,
    timeout_sec: int = DEFAULT_TIMEOUT,
    max_output_chars: int = DEFAULT_MAX_OUTPUT,
) -> dict:
    _ensure_venv()
    full = _safe_path(path)
    argv = [PY_BIN, full] + (args or [])
    t0 = time.time()
    res = subprocess.run(
        argv,
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    return {
        "stdout": _trim(res.stdout, max_output_chars),
        "stderr": _trim(res.stderr, max_output_chars),
        "exit_code": res.returncode,
        "duration_ms": int((time.time() - t0) * 1000),
    }


@mcp.tool
def pip_install(
    packages: list[str],
    timeout_sec: int = DEFAULT_TIMEOUT,
    max_output_chars: int = DEFAULT_MAX_OUTPUT,
) -> dict:
    _ensure_venv()
    if not packages:
        raise ValueError("empty packages")
    t0 = time.time()
    res = subprocess.run(
        [PIP_BIN, "install", *packages],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    return {
        "stdout": _trim(res.stdout, max_output_chars),
        "stderr": _trim(res.stderr, max_output_chars),
        "exit_code": res.returncode,
        "duration_ms": int((time.time() - t0) * 1000),
    }


@mcp.tool
def delete_file(path: str) -> str:
    full = _safe_path(path)
    if os.path.isdir(full):
        raise ValueError("is a directory")
    os.remove(full)
    return "ok"


@mcp.tool
def make_dir(path: str) -> str:
    full = _safe_path(path)
    os.makedirs(full, exist_ok=True)
    return "ok"


if __name__ == "__main__":
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "9003"))
    mcp.run(transport="streamable-http", host=host, port=port)

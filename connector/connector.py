#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27", "websockets>=14"]
# ///
"""SketchUp connector: links SketchUp on this computer to the SketchUp Claude website.

Easiest: on the website click "Add computer" and run the one-line installer it shows.
Manual use (uv installs the dependencies automatically):
  1. uv run connector.py pair --server https://your-site --code ABCD-EFGH
  2. uv run connector.py run        (keep it running while you work)

It starts the SketchUp MCP server on this computer (localhost only) and connects OUT to
the website. No port is opened on this computer and no VPN is needed.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

HERE = Path(__file__).resolve().parent
LEGACY_CONFIG = Path.home() / ".sketchup-connector.json"
CONFIG = Path(os.environ.get("SKC_CONFIG", HERE / "connector.json"))
if not CONFIG.exists() and "SKC_CONFIG" not in os.environ and LEGACY_CONFIG.exists():
    CONFIG = LEGACY_CONFIG
LOG = HERE / "connector.log"
KUAS_DIR = HERE / "kuas"      # KUAS Ruby tools, synced from the website
WORK_DIR = HERE / "work"      # specs/mappings written by Claude through the MCP server
DEFAULT_MCP_DIR = next(
    (p for p in (HERE / "sketchup-mcp-remote", Path.home() / "Documents" / "sketchup-mcp-remote") if p.exists()),
    HERE / "sketchup-mcp-remote",
)
FORWARD_HEADERS = {"content-type", "accept", "mcp-session-id", "mcp-protocol-version", "last-event-id"}
RETURN_HEADERS = {"content-type", "mcp-session-id", "mcp-protocol-version"}


def load_config() -> dict:
    if not CONFIG.exists():
        sys.exit("Not paired yet. On the website click 'Add computer' and run the command it shows.")
    return json.loads(CONFIG.read_text())


def save_config(cfg: dict) -> None:
    CONFIG.write_text(json.dumps(cfg, indent=2))
    if os.name == "posix":
        CONFIG.chmod(0o600)


def cmd_pair(args: argparse.Namespace) -> None:
    server = args.server.rstrip("/")
    try:
        r = httpx.post(
            f"{server}/api/pair",
            json={"code": args.code.strip().upper(), "name": args.name or platform.node()},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        sys.exit(f"Could not reach {server}: {exc}")
    if r.status_code != 200:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        sys.exit(f"Pairing failed: {detail}")
    data = r.json()
    save_config({"server": server, "token": data["token"], "device_id": data["device_id"]})
    print(f"Paired as '{data['name']}'.")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_uv() -> list[str]:
    """uv as a command, or as a module of this or the system Python."""
    candidates = []
    if os.environ.get("UV"):  # set by `uv run`
        candidates.append([os.environ["UV"]])
    if shutil.which("uv"):
        candidates.append([shutil.which("uv")])
    candidates.append([sys.executable, "-m", "uv"])
    for py in ("python", "python3", "py"):
        if shutil.which(py):
            candidates.append([shutil.which(py), "-m", "uv"])
    for cand in candidates:
        try:
            if subprocess.run(cand + ["--version"], capture_output=True, timeout=20).returncode == 0:
                return cand
        except (OSError, subprocess.TimeoutExpired):
            continue
    sys.exit("uv is not installed. Install it first (Windows: winget install astral-sh.uv, Mac: brew install uv).")


def sync_kuas(server: str) -> None:
    """Download the KUAS Ruby tools from the website; only changed files, checksums verified."""
    import hashlib
    try:
        manifest = httpx.get(f"{server}/kuas/manifest.json", timeout=20).raise_for_status().json()
    except (httpx.HTTPError, ValueError) as exc:
        state = "the existing copy is used" if (KUAS_DIR / "kuas_tools.rb").exists() else "KUAS phases will not work"
        print(f"Could not check KUAS tools ({exc}); {state}.")
        return
    updated = 0
    for f in manifest["files"]:
        rel = Path(f["path"])
        if rel.is_absolute() or ".." in rel.parts:
            continue
        target = KUAS_DIR / rel
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == f["sha256"]:
            continue
        data = httpx.get(f"{server}/kuas/ruby/{f['path']}", timeout=60).raise_for_status().content
        if hashlib.sha256(data).hexdigest() != f["sha256"]:
            sys.exit(f"KUAS file {f['path']} failed its checksum; not installed.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        updated += 1
    print(f"KUAS tools {manifest['version']}: {'updated ' + str(updated) + ' file(s)' if updated else 'up to date'}.")


def start_local_mcp(mcp_dir: Path) -> tuple[subprocess.Popen, str]:
    if not (mcp_dir / "pyproject.toml").exists():
        sys.exit(f"SketchUp MCP project not found in {mcp_dir}. Clone it or pass --mcp-dir PATH.")
    port = free_port()
    env = {
        **os.environ,
        "SKETCHUP_MCP_TRANSPORT": "streamable-http",
        "SKETCHUP_MCP_HOST": "127.0.0.1",
        "SKETCHUP_MCP_PORT": str(port),
        "SKETCHUP_KUAS_DIR": str(KUAS_DIR),
        "SKETCHUP_MCP_WORKDIR": str(WORK_DIR),
    }
    cmd = find_uv() + ["run", "--directory", str(mcp_dir), "sketchup-mcp"]
    log = open(LOG, "ab")
    proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=log, start_new_session=(os.name == "posix"))
    print(f"Starting SketchUp MCP server on 127.0.0.1:{port} (first start can take a minute)...")
    deadline = time.time() + 180
    while time.time() < deadline:
        if proc.poll() is not None:
            sys.exit(f"The SketchUp MCP server stopped. See {LOG}")
        if port_open(port):
            return proc, f"http://127.0.0.1:{port}/mcp"
        time.sleep(1)
    stop_tree(proc)
    sys.exit(f"The SketchUp MCP server did not start in time. See {LOG}")


def stop_tree(proc: subprocess.Popen) -> None:
    """Stop the MCP server and its child processes (uv starts Python as a child)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


async def handle(ws, client: httpx.AsyncClient, mcp_url: str, msg: dict) -> None:
    try:
        r = await client.request(
            msg.get("method", "POST"), mcp_url,
            headers={k: v for k, v in (msg.get("headers") or {}).items() if k.lower() in FORWARD_HEADERS},
            content=base64.b64decode(msg.get("body") or ""),
        )
        out = {
            "status": r.status_code,
            "headers": {k: v for k, v in r.headers.items() if k.lower() in RETURN_HEADERS},
            "body": base64.b64encode(r.content).decode(),
        }
    except httpx.HTTPError as exc:
        body = json.dumps({"error": f"SketchUp MCP server on this computer failed: {exc}"}).encode()
        out = {"status": 502, "headers": {"content-type": "application/json"}, "body": base64.b64encode(body).decode()}
    await ws.send(json.dumps({"type": "http_response", "id": msg.get("id"), **out}))


async def serve(cfg: dict, mcp_url: str) -> None:
    ws_url = cfg["server"].replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/connector/ws"
    tasks: set[asyncio.Task] = set()
    backoff = 1
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as client:
        while True:
            try:
                async with connect(
                    ws_url,
                    additional_headers={"Authorization": f"Bearer {cfg['token']}"},
                    ping_interval=20, ping_timeout=20, max_size=32 * 1024 * 1024,
                ) as ws:
                    print(f"Connected to {cfg['server']}. Leave this window open while you use SketchUp.")
                    backoff = 1
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "http":
                            task = asyncio.create_task(handle(ws, client, mcp_url, msg))
                            tasks.add(task)
                            task.add_done_callback(tasks.discard)
            except InvalidStatus as exc:
                if exc.response.status_code in (401, 403):
                    sys.exit("The website rejected this computer (it was removed). Pair it again.")
                print(f"Server answered {exc.response.status_code}; retrying...")
            except ConnectionClosed as exc:
                code = exc.rcvd.code if exc.rcvd else None
                if code == 4000:
                    sys.exit("Another connector for this computer took over. Closing this one.")
                if code == 4401:
                    sys.exit("This computer was removed on the website. Pair it again.")
                print("Connection lost; reconnecting...")
            except OSError as exc:
                print(f"Cannot reach the website ({exc}); retrying...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


def cmd_run(args: argparse.Namespace) -> None:
    cfg = load_config()
    if not port_open(9876):
        print("Warning: SketchUp is not listening on port 9876.")
        print("         Open SketchUp and click Extensions > MCP Server > Start Server.")
    sync_kuas(cfg["server"])
    proc = None
    if args.mcp_url:
        mcp_url = args.mcp_url
    else:
        proc, mcp_url = start_local_mcp(Path(args.mcp_dir).expanduser())
    try:
        asyncio.run(serve(cfg, mcp_url))
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        if proc:
            stop_tree(proc)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair", help="link this computer to your account")
    p.add_argument("--server", required=True, help="website address, e.g. https://example.com")
    p.add_argument("--code", required=True, help="the code shown after clicking 'Add computer'")
    p.add_argument("--name", help="name shown on the website (default: computer name)")
    p.set_defaults(func=cmd_pair)
    r = sub.add_parser("run", help="connect and keep running")
    r.add_argument("--mcp-dir", default=str(DEFAULT_MCP_DIR), help="folder of the sketchup-mcp-remote project")
    r.add_argument("--mcp-url", help="use an already running SketchUp MCP server instead")
    r.set_defaults(func=cmd_run)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

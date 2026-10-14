"""HTTP + WebSocket entry points: the device's config (OTA) check and voice sessions."""

import logging
import os
import socket
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import uvicorn
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket

from .engines import Personas
from .session import Session

log = logging.getLogger("rick_gateway")

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
CFG = yaml.safe_load((ROOT / "config.yaml").read_text())
TOKEN = os.environ.get("RICK_GATEWAY_TOKEN", "local-dev")


def lan_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # no packet is sent; just picks the outbound interface
        return s.getsockname()[0]


PUBLIC_HOST = os.environ.get("GATEWAY_PUBLIC_HOST") or CFG["server"]["public_host"] or lan_ip()
WS_URL = f"ws://{PUBLIC_HOST}:{CFG['server']['port']}/xiaozhi/v1/"


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.chdir(ROOT)
    app.state.personas = Personas(CFG, ROOT)
    app.state.personas.get(app.state.personas.default)  # load the default persona's models up front
    log.info("Ready. Device config URL: http://%s:%s/xiaozhi/ota/", PUBLIC_HOST, CFG["server"]["port"])
    yield


app = FastAPI(lifespan=lifespan)


@app.api_route("/xiaozhi/ota/", methods=["GET", "POST"])
async def ota(request: Request) -> dict:
    """The device calls this on every boot to learn where to connect."""
    body = {}
    if request.method == "POST":
        try:
            body = await request.json()
        except ValueError:
            pass
    version = body.get("application", {}).get("version", "0.0.0")
    log.info("config check from %s (firmware %s)", request.headers.get("Device-Id"), version)
    return {
        "server_time": {"timestamp": int(time.time() * 1000), "timezone_offset": int(datetime.now().astimezone().utcoffset().total_seconds() // 60)},
        # Echo the running version so the device never tries to self-update (yet).
        "firmware": {"version": version, "url": ""},
        "websocket": {"url": WS_URL, "token": TOKEN},
    }


@app.websocket("/xiaozhi/v1/")
async def voice(ws: WebSocket) -> None:
    auth = ws.headers.get("authorization", "")
    if auth.removeprefix("Bearer ").strip() != TOKEN:
        log.warning("rejected connection with bad token from %s", ws.client)
        await ws.close(code=4401)
        return
    device_id = ws.headers.get("device-id", "unknown")
    await ws.accept()
    log.info("[%s] session opened", device_id)
    await Session(ws, app.state.personas, CFG, device_id).run()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(app, host=CFG["server"]["host"], port=CFG["server"]["port"], ws_ping_interval=None)


if __name__ == "__main__":
    main()

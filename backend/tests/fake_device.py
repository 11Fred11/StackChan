"""Plays a WAV into the gateway as if it were the robot, and prints what comes back.

Usage: uv run python tests/fake_device.py [question.wav]
"""

import asyncio
import json
import sys
import time

import httpx
import numpy as np
import soundfile as sf
import websockets

from rick_gateway.audio import UPLINK_RATE, OpusIn, resample

import opuslib

BASE = "http://127.0.0.1:8000"


async def main(wav: str) -> None:
    cfg = httpx.post(f"{BASE}/xiaozhi/ota/", json={"application": {"version": "1.5.3"}}).json()
    print("config:", json.dumps(cfg))
    url, token = cfg["websocket"]["url"], cfg["websocket"]["token"]
    audio, rate = sf.read(wav, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = resample(audio, rate, UPLINK_RATE)
    audio = np.concatenate([np.zeros(UPLINK_RATE // 2), audio, np.zeros(UPLINK_RATE * 2)])
    pcm = (audio * 32767).astype(np.int16)
    enc = opuslib.Encoder(UPLINK_RATE, 1, opuslib.APPLICATION_VOIP)
    n = UPLINK_RATE * 60 // 1000
    frames = [enc.encode(pcm[i : i + n].tobytes(), n) for i in range(0, len(pcm) - n, n)]

    headers = {"Authorization": f"Bearer {token}", "Device-Id": "fa:ke:00:00:00:01", "Protocol-Version": "1"}
    async with websockets.connect(url.replace("ws://" + url.split("/")[2], "ws://127.0.0.1:8000"), additional_headers=headers) as ws:
        await ws.send(json.dumps({"type": "hello", "version": 1, "features": {"mcp": True}, "transport": "websocket",
                                  "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60}}))
        t0 = time.monotonic()
        audio_frames = 0
        done = asyncio.Event()

        async def receive():
            nonlocal audio_frames
            async for msg in ws:
                if isinstance(msg, bytes):
                    audio_frames += 1
                    continue
                m = json.loads(msg)
                print(f"{time.monotonic() - t0:6.2f}s  <- {msg[:200]}")
                if m["type"] == "mcp" and "method" in m["payload"]:
                    p = m["payload"]
                    result = {}
                    if p["method"] == "tools/list":
                        result = {"tools": [{"name": "self.robot.set_head_angles", "description": "Turn the robot head. yaw -90..90, pitch 0..45",
                                             "inputSchema": {"type": "object", "properties": {"yaw": {"type": "integer"}, "pitch": {"type": "integer"}}}}]}
                    elif p["method"] == "tools/call":
                        result = {"content": [{"type": "text", "text": "true"}], "isError": False}
                    await ws.send(json.dumps({"type": "mcp", "payload": {"jsonrpc": "2.0", "id": p["id"], "result": result}}))
                if m["type"] == "tts" and m["state"] == "stop":
                    done.set()

        rx = asyncio.create_task(receive())
        await asyncio.sleep(0.5)
        await ws.send(json.dumps({"type": "listen", "state": "start", "mode": "auto"}))
        for f in frames:
            if done.is_set():
                break
            await ws.send(f)
            await asyncio.sleep(0.06)
        await asyncio.wait_for(done.wait(), 60)
        print(f"received {audio_frames} audio frames (~{audio_frames * 0.06:.1f}s of speech)")
        rx.cancel()


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "tests/question.wav"))

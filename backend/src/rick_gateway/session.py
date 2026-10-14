"""One WebSocket conversation with a device, speaking the Xiaozhi protocol.

Protocol reference: firmware/xiaozhi-esp32/docs/websocket.md and mcp-protocol.md.
"""

import asyncio
import json
import logging
import re
import time
import uuid

import httpx
from fastapi import WebSocket, WebSocketDisconnect

from pathlib import Path

from datetime import datetime

from . import actions
from .audio import DOWNLINK_RATE, FRAME_MS, Endpointer, OpusIn, OpusOut
from .engines import Persona, Personas

log = logging.getLogger(__name__)

EMOTIONS = {"😐": "neutral", "😊": "happy", "😂": "laughing", "😠": "angry", "😢": "sad", "😭": "crying", "😴": "sleepy", "🤔": "doubtful"}
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+")
BURP = re.compile(r"\*?\b(?:bu+r+p+|u+r+p+)\b\*?[,.!]?", re.IGNORECASE)
STAGE_DIRECTION = re.compile(r"\*[^*]*\*|\{[^}]*\}|\[[^\]]*\]")
# Small local models sometimes write a tool name instead of calling it.
TOOL_AS_TEXT = re.compile(r"\bend[_ ]conversation\b\W*", re.IGNORECASE)
NON_SPEECH = re.compile(r"[^\w\s.,!?'’\-…:;\"()]")
SFX_BURP = "\x00burp"
MIN_SPOKEN_CHARS = 12
# The brain sometimes says goodbye without calling end_conversation; a clear goodbye ends it anyway.
GOODBYE = re.compile(
    r"^\W*(?:ok(?:ay)?\W+|alright\W+|thanks?\W+(?:you\W+)?)?(?:rick\W+)?"
    r"(?:bye|goodbye|good ?night|night|see (?:you|ya)|later|go to sleep|sleep|that's all|stop|shut up)\b",
    re.IGNORECASE,
)
MAX_TOOL_ROUNDS = 3
END_TOOL = {
    "type": "function",
    "function": {
        "name": "end_conversation",
        "description": "Stop listening and go idle. Call when the user says goodbye, asks you to sleep, or is done talking. Say a short goodbye too.",
        "parameters": {"type": "object", "properties": {}},
    },
}
SEND_LEAD_FRAMES = 5  # frames sent ahead of real time to keep the device buffer fed
FACE_TOOL = "self.avatar.set_face"  # firmware tool that swaps the on-screen avatar skin

# Conversation history per (device, persona), kept across sessions while the gateway runs.
_histories: dict[tuple[str, str], list[dict]] = {}


class DeviceState:
    """Remembers each robot's current persona across restarts (state/devices.json)."""

    PATH = Path("state/devices.json")

    @classmethod
    def persona(cls, device_id: str) -> str | None:
        try:
            return json.loads(cls.PATH.read_text()).get(device_id, {}).get("persona")
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    @classmethod
    def set_persona(cls, device_id: str, key: str) -> None:
        try:
            data = json.loads(cls.PATH.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        data.setdefault(device_id, {})["persona"] = key
        cls.PATH.parent.mkdir(exist_ok=True)
        cls.PATH.write_text(json.dumps(data, indent=2))


def switch_tool(available: dict[str, str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "switch_persona",
            "description": "Hand the robot over to another persona when the user asks for them. Personas: "
            + "; ".join(f"{k} = {d}" for k, d in available.items()),
            "parameters": {
                "type": "object",
                "properties": {"persona": {"type": "string", "enum": list(available)}},
                "required": ["persona"],
            },
        },
    }


class Session:
    def __init__(self, ws: WebSocket, personas: Personas, cfg: dict, device_id: str, vision: dict | None = None) -> None:
        self.ws = ws
        self.vision = vision
        self.personas = personas
        self.persona: Persona = personas.get(DeviceState.persona(device_id) or personas.default)
        self.switch_to: str | None = None
        self.cfg = cfg
        self.device_id = device_id
        self.session_id = uuid.uuid4().hex[:12]
        self.opus_in = OpusIn()
        self.endpointer = Endpointer(cfg["vad"]["end_silence_ms"])
        self.listening = False
        self.listened_before = False
        self.listen_started_at = 0.0
        self.turn: asyncio.Task | None = None
        self.deny = set(cfg["tools"]["deny"])
        self.tools: list[dict] = [END_TOOL, *actions.LOCAL_TOOLS]
        if len(personas.available) > 1:
            self.tools.append(switch_tool(personas.available))
        self.ending = False
        self.tool_names: dict[str, str] = {}  # llm-safe name -> device tool name
        self._mcp_id = 0
        self._mcp_waiting: dict[int, asyncio.Future] = {}
        self._send_lock = asyncio.Lock()

    # ---- transport -----------------------------------------------------

    async def send_json(self, msg: dict) -> None:
        msg.setdefault("session_id", self.session_id)
        async with self._send_lock:
            await self.ws.send_text(json.dumps(msg, ensure_ascii=False))

    async def send_audio(self, frame: bytes) -> None:
        async with self._send_lock:
            await self.ws.send_bytes(frame)

    async def run(self) -> None:
        watchdog = asyncio.create_task(self._no_speech_watchdog())
        try:
            while True:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    self._on_audio(msg["bytes"])
                elif msg.get("text") is not None:
                    await self._on_json(json.loads(msg["text"]))
        except WebSocketDisconnect:
            pass
        finally:
            watchdog.cancel()
            if self.turn:
                self.turn.cancel()
            log.info("[%s] session closed", self.device_id)

    # ---- incoming --------------------------------------------------------

    async def _on_json(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "hello":
            await self.send_json(
                {
                    "type": "hello",
                    "transport": "websocket",
                    "audio_params": {
                        "format": "opus",
                        "sample_rate": DOWNLINK_RATE,
                        "channels": 1,
                        "frame_duration": FRAME_MS,
                    },
                }
            )
            if msg.get("features", {}).get("mcp"):
                asyncio.create_task(self._discover_tools())
        elif kind == "listen":
            state = msg.get("state")
            if state == "start":
                self._start_listening()
            elif state == "stop":
                self.listening = False
                if self.endpointer.heard_speech:
                    self._start_turn()
            elif state == "detect":
                log.info("[%s] wake word: %s", self.device_id, msg.get("text"))
        elif kind == "abort":
            log.info("[%s] abort: %s", self.device_id, msg.get("reason"))
            if self.turn and not self.turn.done():
                self.turn.cancel()
                await self.send_json({"type": "tts", "state": "stop"})
        elif kind == "mcp":
            self._on_mcp(msg.get("payload", {}))

    def _start_listening(self) -> None:
        # After the wake word the robot plays a "pop" chime that the mic hears (Whisper turned
        # it into "Whoop!"); after a reply, the speaker's tail can echo. Skip both.
        self.endpointer.reset(skip_ms=600 if not self.listened_before else 250)
        self.listened_before = True
        self.listening = True
        self.listen_started_at = time.monotonic()

    def _on_audio(self, packet: bytes) -> None:
        if not self.listening:
            return
        if self.endpointer.feed(self.opus_in.decode(packet)):
            self.listening = False
            self._start_turn()

    def _start_turn(self) -> None:
        if self.turn and not self.turn.done():
            return
        audio = self.endpointer.utterance()
        self.turn = asyncio.create_task(self._turn(audio))

    async def _no_speech_watchdog(self) -> None:
        timeout = self.cfg["vad"]["no_speech_timeout_s"]
        while True:
            await asyncio.sleep(1)
            idle = time.monotonic() - self.listen_started_at
            if self.listening and not self.endpointer.heard_speech and idle > timeout:
                log.info("[%s] nobody talking for %ds, closing", self.device_id, timeout)
                await self.ws.close()
                return

    # ---- one conversational turn -------------------------------------

    async def _turn(self, audio) -> None:
        try:
            t0 = time.monotonic()
            text = await asyncio.to_thread(self.persona.stt.transcribe, audio)
            if not text or len(text) < 2:
                self._start_listening()
                return
            log.info("[%s] >> %s  (stt %.2fs)", self.device_id, text, time.monotonic() - t0)
            if GOODBYE.search(text):
                self.ending = True
            await self.send_json({"type": "stt", "text": text})

            speaker = Speaker(self)
            persona_before = self.persona
            reply = await self._think(text, speaker)
            switch_to, self.switch_to = self.switch_to, None
            if switch_to:
                # Save the switch now: the handover line still plays in the old voice
                # (the speaker holds its own persona), then the new persona greets.
                self.persona = self.personas.get(switch_to)
                DeviceState.set_persona(self.device_id, switch_to)
                log.info("[%s] switched to persona %s", self.device_id, switch_to)
            await speaker.finish()
            log.info("[%s] << %s  (turn %.2fs)", self.device_id, reply, time.monotonic() - t0)
            history = _histories.setdefault((self.device_id, persona_before.key), [])
            history += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
            del history[: -2 * persona_before.history_turns]
            if switch_to:
                await self._greet()
            if self.ending:
                await asyncio.sleep(1.0)  # let the device finish playing
                await self.ws.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("[%s] turn failed", self.device_id)
            await self.send_json({"type": "tts", "state": "stop"})

    @property
    def history(self) -> list[dict]:
        return _histories.setdefault((self.device_id, self.persona.key), [])

    async def _greet(self) -> None:
        if self.persona.face and FACE_TOOL.replace(".", "_") in self.tool_names:
            await self.call_tool(FACE_TOOL.replace(".", "_"), {"face": self.persona.face})
        if self.persona.greeting:
            speaker = Speaker(self)
            await speaker.feed("😊 " + self.persona.greeting)
            await speaker.finish()

    def _system_prompt(self) -> str:
        home = self.cfg.get("home", {})
        now = datetime.now().astimezone()
        where = f" The user lives in {home['city']}." if home.get("city") else ""
        return f"{self.persona.prompt}\n\nIt is {now:%A %d %B %Y, %H:%M} ({now:%Z}).{where}"

    async def _think(self, text: str, speaker: "Speaker") -> str:
        messages = [{"role": "system", "content": self._system_prompt()}, *self.history, {"role": "user", "content": text}]
        names = {t["function"]["name"] for t in self.tools}
        force = actions.required_tools(text, names)
        if force:
            log.info("[%s] action request -> must use one of %s", self.device_id, force)
        spoken = ""
        for round_ in range(MAX_TOOL_ROUNDS):
            content, calls = "", []
            # Offer tools only on the first round; small models otherwise repeat the call.
            tools = self.tools if round_ == 0 else []
            async for event in self.persona.llm.stream(messages, tools, force if round_ == 0 else None):
                content += event.text
                calls += event.tool_calls
                await speaker.feed(event.text)
            spoken += content
            if not calls and TOOL_AS_TEXT.search(content):
                await self.call_tool("end_conversation", {})
            if not calls:
                break
            messages.append({"role": "assistant", "content": content, "tool_calls": calls})
            for call in calls:
                fn = call.get("function", {})
                result = await self.call_tool(fn.get("name", ""), fn.get("arguments") or {})
                messages.append({"role": "tool", "tool_name": fn.get("name", ""), "content": result})
        return Speaker.clean(spoken)

    # ---- MCP (device tools) -------------------------------------------

    async def mcp_request(self, method: str, params: dict, timeout: float = 20) -> dict:
        self._mcp_id += 1
        req_id = self._mcp_id
        fut = asyncio.get_running_loop().create_future()
        self._mcp_waiting[req_id] = fut
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": req_id}
        await self.send_json({"type": "mcp", "payload": payload})
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._mcp_waiting.pop(req_id, None)

    def _on_mcp(self, payload: dict) -> None:
        fut = self._mcp_waiting.get(payload.get("id"))
        if fut and not fut.done():
            fut.set_result(payload)

    async def _discover_tools(self) -> None:
        try:
            capabilities = {"vision": self.vision} if self.vision else {}
            await self.mcp_request("initialize", {"protocolVersion": "2024-11-05", "capabilities": capabilities})
            cursor, device_tools = None, []
            while True:
                params = {"cursor": cursor} if cursor else {}
                result = (await self.mcp_request("tools/list", params)).get("result", {})
                device_tools += result.get("tools", [])
                cursor = result.get("nextCursor")
                if not cursor:
                    break
        except (TimeoutError, asyncio.TimeoutError):
            log.warning("[%s] device did not answer MCP discovery", self.device_id)
            return
        for tool in device_tools:
            if tool["name"] in self.deny:
                continue
            safe = tool["name"].replace(".", "_")
            self.tool_names[safe] = tool["name"]
            self.tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": safe,
                        "description": tool.get("description", ""),
                        "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                    },
                }
            )
        log.info("[%s] tools: %s", self.device_id, ", ".join(self.tool_names.values()) or "none")

    async def call_tool(self, safe_name: str, arguments: dict) -> str:
        if safe_name == "end_conversation":
            log.info("[%s] ending conversation", self.device_id)
            self.ending = True
            return "ok, say goodbye now"
        if safe_name == "get_weather":
            log.info("[%s] weather %s", self.device_id, arguments)
            try:
                return await actions.get_weather(arguments.get("city", ""), self.cfg.get("home", {}))
            except httpx.HTTPError as e:
                return f"Weather service unreachable: {e}"
        if safe_name == "gesture":
            if "self_robot_set_head_angles" not in self.tool_names:
                return "This robot can't move its head."
            log.info("[%s] gesture %s", self.device_id, arguments)
            return await actions.gesture(
                arguments.get("kind", "nod"),
                lambda yaw, pitch: self.call_tool("self_robot_set_head_angles", {"yaw": yaw, "pitch": pitch, "speed": 400}),
            )
        if safe_name == "switch_persona":
            key = arguments.get("persona", "")
            if key not in self.personas.available:
                return f"No persona called {key}. Available: {', '.join(self.personas.available)}"
            if key == self.persona.key:
                return "You are already that persona."
            self.switch_to = key
            return f"ok, {key} takes over after you say one short handover line"
        name = self.tool_names.get(safe_name)
        if not name:
            return f"Unknown tool {safe_name}"
        log.info("[%s] tool %s %s", self.device_id, name, arguments)
        try:
            resp = await self.mcp_request("tools/call", {"name": name, "arguments": arguments})
        except (TimeoutError, asyncio.TimeoutError):
            return "Tool timed out"
        if "error" in resp:
            return f"Error: {resp['error'].get('message', resp['error'])}"
        parts = resp.get("result", {}).get("content", [])
        return " ".join(p.get("text", "") for p in parts) or "ok"


class Speaker:
    """Turns streamed LLM text into emotion + sentence-by-sentence speech."""

    def __init__(self, session: Session) -> None:
        self.s = session
        self.persona = session.persona  # fixed for this reply, even if a switch is pending
        self.opus_out = OpusOut()
        self.buffer = ""
        self.emotion_done = False
        self.emotion = "neutral"
        self.queued_any = False
        self.carry = ""
        self.started = False
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.player = asyncio.create_task(self._play())
        self.frames_sent = 0
        self.t0 = 0.0

    @staticmethod
    def clean(text: str) -> str:
        text = TOOL_AS_TEXT.sub("", STAGE_DIRECTION.sub("", text))
        return re.sub(r"\s+", " ", NON_SPEECH.sub("", text)).strip().lstrip(",;: ")

    async def feed(self, text: str) -> None:
        self.buffer += text
        if not self.emotion_done:
            stripped = self.buffer.lstrip()
            if not stripped:
                return  # mood emoji still arriving
            emotion = EMOTIONS.get(stripped[0], "neutral")
            self.emotion = emotion
            self.buffer = stripped[1:] if stripped[0] in EMOTIONS else stripped
            self.emotion_done = True
            await self.s.send_json({"type": "llm", "emotion": emotion, "text": ""})
        parts = SENTENCE_END.split(self.buffer)
        for sentence in parts[:-1]:
            await self._queue(sentence)
        self.buffer = parts[-1]
        # Get the first words out fast: speak up to the first comma instead of a whole long sentence.
        if not self.queued_any:
            cut = self.buffer.find(", ", 15)
            if cut != -1:
                await self._queue(self.buffer[: cut + 1])
                self.buffer = self.buffer[cut + 2 :]

    async def _queue(self, sentence: str, final: bool = False) -> None:
        # Real burp sound effects wherever the text says "burp".
        pieces = BURP.split(sentence)
        for i, piece in enumerate(pieces):
            if i > 0 and "burp" in self.persona.sfx:
                await self._flush_carry()
                await self.queue.put(SFX_BURP)
            piece = self.clean(piece)
            if not any(ch.isalnum() for ch in piece):
                continue  # punctuation left over after removing a burp or emoji
            # Tiny fragments ("Uh...", "Oh!") make the voice clone produce odd noises:
            # carry them into the next piece instead of voicing them alone.
            piece = (self.carry + " " + piece).strip() if self.carry else piece
            self.carry = ""
            if len(piece) < MIN_SPOKEN_CHARS and not (final and i == len(pieces) - 1):
                self.carry = piece
                continue
            self.queued_any = True
            await self.queue.put(piece)
        if final:
            await self._flush_carry()

    async def _flush_carry(self) -> None:
        if self.carry:
            carry, self.carry = self.carry, ""
            self.queued_any = True
            await self.queue.put(carry)

    async def finish(self) -> None:
        await self._queue(self.buffer, final=True)
        self.buffer = ""
        await self.queue.put(None)
        await self.player
        if self.started:
            await self.s.send_json({"type": "tts", "state": "stop"})

    async def _play(self) -> None:
        while (sentence := await self.queue.get()) is not None:
            if sentence == SFX_BURP:
                audio, rate = self.persona.sfx["burp"]
                sentence = "*burp*"
            else:
                audio, rate = await asyncio.to_thread(self.persona.tts.synthesize, sentence, self.emotion)
            frames = self.opus_out.encode(audio, rate)
            if not self.started:
                self.started = True
                await self.s.send_json({"type": "tts", "state": "start"})
                self.t0 = time.monotonic()
            await self.s.send_json({"type": "tts", "state": "sentence_start", "text": sentence})
            for frame in frames:
                due = self.t0 + (self.frames_sent - SEND_LEAD_FRAMES) * FRAME_MS / 1000
                delay = due - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                elif delay < -0.5:
                    # TTS fell behind real time; restart the clock so we don't burst.
                    self.t0 = time.monotonic() - (self.frames_sent - SEND_LEAD_FRAMES) * FRAME_MS / 1000
                await self.s.send_audio(frame)
                self.frames_sent += 1

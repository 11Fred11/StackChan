"""Speech-to-text, LLM and text-to-speech engines. Each is local by default."""

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
import numpy as np

log = logging.getLogger(__name__)


class WhisperSTT:
    def __init__(self, cfg: dict) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(cfg["model"], device=cfg["device"], compute_type=cfg["compute_type"])

    def transcribe(self, pcm16k: np.ndarray) -> str:
        segments, _ = self._model.transcribe(pcm16k, language="en", beam_size=1, vad_filter=False)
        return " ".join(s.text.strip() for s in segments).strip()


class KokoroTTS:
    def __init__(self, cfg: dict) -> None:
        from kokoro_onnx import Kokoro

        self._kokoro = Kokoro(cfg["model_path"], cfg["voices_path"])
        self._voice = cfg["voice"]
        self._speed = cfg["speed"]

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        audio, sample_rate = self._kokoro.create(text, voice=self._voice, speed=self._speed, lang="en-us")
        return audio.astype(np.float32), sample_rate


@dataclass
class LLMEvent:
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)


class OllamaLLM:
    def __init__(self, cfg: dict) -> None:
        self._url = cfg["url"].rstrip("/") + "/api/chat"
        self._model = cfg["model"]
        self._client = httpx.AsyncClient(timeout=120)

    async def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[LLMEvent]:
        body = {
            "model": self._model,
            "messages": messages,
            "think": False,
            "keep_alive": "60m",
            "stream": True,
        }
        if tools:
            body["tools"] = tools
        async with self._client.stream("POST", self._url, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                msg = json.loads(line).get("message", {})
                event = LLMEvent(text=msg.get("content", ""), tool_calls=msg.get("tool_calls") or [])
                if event.text or event.tool_calls:
                    yield event

    async def warm_up(self) -> None:
        try:
            await self._client.post(
                self._url,
                json={"model": self._model, "messages": [], "keep_alive": "60m", "stream": False},
            )
        except httpx.HTTPError as e:
            log.warning("LLM warm-up failed: %s", e)


@dataclass
class Engines:
    stt: WhisperSTT
    llm: OllamaLLM
    tts: KokoroTTS
    persona: str


def load_engines(cfg: dict, persona: str) -> Engines:
    for stage, expected in (("stt", "whisper"), ("llm", "ollama"), ("tts", "kokoro")):
        if cfg[stage]["provider"] != expected:
            raise ValueError(f"{stage}.provider '{cfg[stage]['provider']}' is not supported yet")
    log.info("Loading speech-to-text (%s)", cfg["stt"]["model"])
    stt = WhisperSTT(cfg["stt"])
    log.info("Loading text-to-speech (kokoro, voice %s)", cfg["tts"]["voice"])
    tts = KokoroTTS(cfg["tts"])
    return Engines(stt=stt, llm=OllamaLLM(cfg["llm"]), tts=tts, persona=persona)

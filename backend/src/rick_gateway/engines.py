"""Speech-to-text, LLM and text-to-speech engines. Each is local by default."""

import json
import logging
import os
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


class ChatterboxTTS:
    """Calls the local Chatterbox voice-clone service (services/chatterbox)."""

    def __init__(self, cfg: dict) -> None:
        self._url = cfg["url"].rstrip("/") + "/tts"
        preset = cfg["presets"][cfg["preset"]]
        self._voice = preset["voice"]
        self._params = {k: preset[k] for k in ("reference", "exaggeration", "cfg_weight", "temperature") if k in preset}
        self._client = httpx.Client(timeout=60)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        resp = self._client.post(self._url, json={"text": text, "voice": self._voice, **self._params})
        resp.raise_for_status()
        rate = int(resp.headers["X-Sample-Rate"])
        return np.frombuffer(resp.content, dtype=np.int16).astype(np.float32) / 32768.0, rate


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


class GeminiLLM:
    """Gemini via the google-genai SDK. Messages and tools use the same shapes as OllamaLLM."""

    def __init__(self, cfg: dict) -> None:
        from google import genai

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("llm.provider is gemini but GEMINI_API_KEY is not set (put it in backend/.env)")
        self._client = genai.Client(api_key=api_key)
        self._model = cfg["model"]
        self._thinking = cfg.get("thinking_config")

    def _convert(self, messages: list[dict]):
        from google.genai import types

        system, contents = None, []
        for m in messages:
            role = m["role"]
            if role == "system":
                system = m["content"]
            elif role == "user":
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text=m["content"])]))
            elif role == "assistant":
                parts = [types.Part.from_text(text=m["content"])] if m.get("content") else []
                for call in m.get("tool_calls", []):
                    fn = call["function"]
                    parts.append(call.get("_part") or types.Part.from_function_call(name=fn["name"], args=fn["arguments"]))
                contents.append(types.Content(role="model", parts=parts))
            elif role == "tool":
                part = types.Part.from_function_response(name=m["tool_name"], response={"result": m["content"]})
                if contents and contents[-1].role == "user" and contents[-1].parts[0].function_response:
                    contents[-1].parts.append(part)
                else:
                    contents.append(types.Content(role="user", parts=[part]))
        return system, contents

    async def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[LLMEvent]:
        from google.genai import types

        system, contents = self._convert(messages)
        config = types.GenerateContentConfig(system_instruction=system)
        if tools:
            config.tools = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=t["function"]["name"],
                            description=t["function"]["description"],
                            parameters_json_schema=t["function"]["parameters"],
                        )
                        for t in tools
                    ]
                )
            ]
            config.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
        if self._thinking:
            config.thinking_config = types.ThinkingConfig(**self._thinking)
        stream = await self._client.aio.models.generate_content_stream(model=self._model, contents=contents, config=config)
        async for chunk in stream:
            for cand in chunk.candidates or []:
                for part in (cand.content.parts if cand.content else None) or []:
                    if part.function_call:
                        call = {"function": {"name": part.function_call.name, "arguments": dict(part.function_call.args or {})}}
                        call["_part"] = part  # keeps Gemini's thought signature for the follow-up request
                        yield LLMEvent(tool_calls=[call])
                    elif part.text and not part.thought:
                        yield LLMEvent(text=part.text)

    async def warm_up(self) -> None:
        pass


@dataclass
class Engines:
    stt: WhisperSTT
    llm: "OllamaLLM | GeminiLLM"
    tts: "KokoroTTS | ChatterboxTTS"
    persona: str


LLMS = {"ollama": OllamaLLM, "gemini": GeminiLLM}
TTSS = {"kokoro": KokoroTTS, "chatterbox": ChatterboxTTS}


def load_engines(cfg: dict, persona: str) -> Engines:
    if cfg["stt"]["provider"] != "whisper":
        raise ValueError(f"stt.provider '{cfg['stt']['provider']}' is not supported yet")
    log.info("Loading speech-to-text (%s)", cfg["stt"]["model"])
    stt = WhisperSTT(cfg["stt"])
    llm_cfg = cfg["llm"][cfg["llm"]["provider"]]
    log.info("Brain: %s (%s)", cfg["llm"]["provider"], llm_cfg["model"])
    llm = LLMS[cfg["llm"]["provider"]](llm_cfg)
    tts_cfg = cfg["tts"][cfg["tts"]["provider"]]
    log.info("Voice: %s (%s)", cfg["tts"]["provider"], tts_cfg.get("preset") or tts_cfg.get("voice"))
    tts = TTSS[cfg["tts"]["provider"]](tts_cfg)
    return Engines(stt=stt, llm=llm, tts=tts, persona=persona)

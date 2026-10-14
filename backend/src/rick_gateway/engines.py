"""Speech-to-text, LLM and text-to-speech providers, and the personas built from them.

A persona (personas/<key>/persona.yaml) picks one provider per stage. Provider
connection settings live in config.yaml under `providers`; a persona's stage settings
are merged on top of them. Heavy models are loaded once and shared between personas.
"""

import io
import json
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import httpx
import numpy as np
import yaml

log = logging.getLogger(__name__)


# ---- speech-to-text ------------------------------------------------------------------


@cache
def _whisper_model(model: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    log.info("Loading speech-to-text (whisper %s on %s)", model, device)
    return WhisperModel(model, device=device, compute_type=compute_type)


# What Whisper "hears" in silence and background noise (it was trained on subtitled video).
WHISPER_HALLUCINATIONS = (
    "thanks for watching", "thank you for watching", "please subscribe", "like and subscribe",
    "see you in the next video", "subtitles by", "amara.org", "you", "oh.", "uh.",
)


class WhisperSTT:
    def __init__(self, cfg: dict) -> None:
        self._model = _whisper_model(cfg["model"], cfg["device"], cfg["compute_type"])
        self._language = cfg.get("language", "en")

    def transcribe(self, pcm16k: np.ndarray) -> str:
        segments, _ = self._model.transcribe(pcm16k, language=self._language, beam_size=1, vad_filter=False)
        # Drop segments Whisper itself doubts: likely noise, not speech.
        kept = [s.text.strip() for s in segments if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0]
        text = " ".join(kept).strip()
        audio_seconds = len(pcm16k) / 16000
        if text.lower() in WHISPER_HALLUCINATIONS or (
            audio_seconds < 1.2 and any(h in text.lower() for h in WHISPER_HALLUCINATIONS[:6])
        ):
            log.info("ignoring likely noise transcript: %r", text)
            return ""
        return text


class RemoteSTT:
    """The Darja project's GPU speech server: POST {url}/asr?model=... with a WAV body."""

    def __init__(self, cfg: dict) -> None:
        self._url = _env_url(cfg) + "/asr"
        self._model = cfg["model"]
        self._client = httpx.Client(timeout=60, headers=_bearer(cfg))

    def transcribe(self, pcm16k: np.ndarray) -> str:
        import soundfile as sf

        buf = io.BytesIO()
        sf.write(buf, pcm16k, 16000, format="WAV", subtype="PCM_16")
        resp = self._client.post(self._url, params={"model": self._model}, content=buf.getvalue())
        resp.raise_for_status()
        return resp.json().get("text", "").strip()


# ---- text-to-speech ------------------------------------------------------------------


@cache
def _kokoro_model(model_path: str, voices_path: str):
    from kokoro_onnx import Kokoro

    return Kokoro(model_path, voices_path)


class KokoroTTS:
    def __init__(self, cfg: dict) -> None:
        self._kokoro = _kokoro_model(cfg["model_path"], cfg["voices_path"])
        self._voice = cfg["voice"]
        self._speed = cfg.get("speed", 1.0)
        self._lang = cfg.get("lang", "en-us")

    def synthesize(self, text: str, emotion: str = "neutral") -> tuple[np.ndarray, int]:
        audio, sample_rate = self._kokoro.create(text, voice=self._voice, speed=self._speed, lang=self._lang)
        return audio.astype(np.float32), sample_rate


class ChatterboxTTS:
    """Calls the local Chatterbox voice-clone service (services/chatterbox)."""

    def __init__(self, cfg: dict) -> None:
        self._url = cfg["url"].rstrip("/") + "/tts"
        preset = cfg["presets"][cfg["preset"]]
        self._voice = preset["voice"]
        self._params = {k: preset[k] for k in ("reference", "exaggeration", "cfg_weight", "temperature") if k in preset}
        self._by_emotion = preset.get("emotion_exaggeration", {})
        self._client = httpx.Client(timeout=60)

    def synthesize(self, text: str, emotion: str = "neutral") -> tuple[np.ndarray, int]:
        params = dict(self._params)
        if emotion in self._by_emotion:
            params["exaggeration"] = self._by_emotion[emotion]
        resp = self._client.post(self._url, json={"text": text, "voice": self._voice, **params})
        resp.raise_for_status()
        return _pcm16(resp)


class RemoteTTS:
    """The Darja project's GPU speech server: POST {url}/tts {text, model, voice}."""

    def __init__(self, cfg: dict) -> None:
        self._url = _env_url(cfg) + "/tts"
        self._body = {"model": cfg["model"], "voice": cfg.get("voice", "default")}
        self._client = httpx.Client(timeout=60, headers=_bearer(cfg))

    def synthesize(self, text: str, emotion: str = "neutral") -> tuple[np.ndarray, int]:
        resp = self._client.post(self._url, json={"text": text, **self._body})
        resp.raise_for_status()
        return _pcm16(resp)


class Converted:
    """Passes another voice through an RVC model (services/rvc) for a closer timbre.

    If the converter is down, the unconverted voice is used rather than failing.
    """

    def __init__(self, voice, cfg: dict) -> None:
        self._voice = voice
        self._url = cfg["url"].rstrip("/") + "/convert"
        self._params = {k: cfg[k] for k in ("model", "pitch", "index_rate", "protect") if k in cfg}
        self._client = httpx.Client(timeout=60)

    def synthesize(self, text: str, emotion: str = "neutral") -> tuple[np.ndarray, int]:
        audio, rate = self._voice.synthesize(text, emotion)
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
        try:
            resp = self._client.post(self._url, params=self._params, content=pcm, headers={"X-Sample-Rate": str(rate)})
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("voice conversion failed (%s); using the unconverted voice", e)
            return audio, rate
        return _pcm16(resp)


class WithFallback:
    """Uses a second voice when the first fails, so the robot never goes mute."""

    def __init__(self, primary, fallback) -> None:
        self._primary, self._fallback = primary, fallback

    def synthesize(self, text: str, emotion: str = "neutral") -> tuple[np.ndarray, int]:
        try:
            return self._primary.synthesize(text, emotion)
        except httpx.HTTPError as e:
            log.warning("voice failed (%s); using the fallback voice", e)
            return self._fallback.synthesize(text, emotion)


def _pcm16(resp: httpx.Response) -> tuple[np.ndarray, int]:
    rate = int(resp.headers["X-Sample-Rate"])
    return np.frombuffer(resp.content, dtype=np.int16).astype(np.float32) / 32768.0, rate


def _env_url(cfg: dict) -> str:
    url = os.environ.get(cfg["url_env"], "")
    if not url:
        raise RuntimeError(f"{cfg['url_env']} is not set (put it in backend/.env)")
    return url.rstrip("/")


def _bearer(cfg: dict) -> dict[str, str]:
    key = os.environ.get(cfg.get("key_env", ""), "")
    return {"Authorization": f"Bearer {key}"} if key else {}


# ---- LLMs ------------------------------------------------------------------------------


@dataclass
class LLMEvent:
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)


class OllamaLLM:
    def __init__(self, cfg: dict) -> None:
        self._url = cfg["url"].rstrip("/") + "/api/chat"
        self._model = cfg["model"]
        self._client = httpx.AsyncClient(timeout=120)

    async def stream(self, messages: list[dict], tools: list[dict], force: list[str] | None = None) -> AsyncIterator[LLMEvent]:
        body = {"model": self._model, "messages": messages, "think": False, "keep_alive": "60m", "stream": True}
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


class GeminiLLM:
    """Gemini via the google-genai SDK. Messages and tools use the same shapes as OllamaLLM."""

    def __init__(self, cfg: dict) -> None:
        from google import genai

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("a persona uses gemini but GEMINI_API_KEY is not set (put it in backend/.env)")
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

    async def stream(self, messages: list[dict], tools: list[dict], force: list[str] | None = None) -> AsyncIterator[LLMEvent]:
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
            if force:
                # The user clearly asked for an action: the first step must be one of these tools.
                config.tool_config = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(mode="ANY", allowed_function_names=force)
                )
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


async def describe_image(cfg: dict, jpeg: bytes, question: str) -> str:
    """Answers a question about a photo from the robot's camera (Gemini vision)."""
    from google import genai
    from google.genai import types

    if cfg["provider"] != "gemini":
        raise ValueError(f"vision provider '{cfg['provider']}' is not supported yet")
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = (
        "This photo was just taken by a small desk robot's camera, facing the person talking to it. "
        "Answer the question about it factually and briefly (two or three sentences). "
        f"Question: {question or 'What do you see?'}"
    )
    resp = await client.aio.models.generate_content(
        model=cfg["model"],
        contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), prompt],
    )
    return (resp.text or "").strip()


STT = {"whisper": WhisperSTT, "darja": RemoteSTT}
LLM = {"gemini": GeminiLLM, "ollama": OllamaLLM}
TTS = {"kokoro": KokoroTTS, "chatterbox": ChatterboxTTS, "darja": RemoteTTS}


# ---- personas -----------------------------------------------------------------------


@dataclass
class Persona:
    key: str
    name: str
    description: str
    language: str
    face: str | None
    greeting: str
    prompt: str
    history_turns: int
    stt: object
    llm: object
    tts: object
    sfx: dict[str, tuple[np.ndarray, int]]


class Personas:
    """Loads every persona folder and builds its providers on first use."""

    def __init__(self, cfg: dict, root: Path) -> None:
        self._providers = cfg["providers"]
        self._dir = root / cfg["personas"]["dir"]
        self.default = cfg["personas"]["default"]
        self._common = (self._dir / "_common.md").read_text()
        self._specs: dict[str, dict] = {}
        for folder in sorted(p for p in self._dir.iterdir() if (p / "persona.yaml").is_file()):
            spec = yaml.safe_load((folder / "persona.yaml").read_text())
            if spec.get("enabled", True):
                self._specs[folder.name] = spec
            else:
                log.info("persona %s is disabled: %s", folder.name, spec.get("disabled_reason", ""))
        self._loaded: dict[str, Persona] = {}
        if self.default not in self._specs:
            raise ValueError(f"default persona '{self.default}' not found or disabled in {self._dir}")

    @property
    def available(self) -> dict[str, str]:
        """key -> one-line description, for the persona-switch tool."""
        return {k: f"{s['name']}: {s['description']}" for k, s in self._specs.items()}

    def get(self, key: str) -> Persona:
        if key not in self._specs:
            key = self.default
        if key not in self._loaded:
            self._loaded[key] = self._build(key, self._specs[key])
        return self._loaded[key]

    def _stage(self, kind: dict, spec: dict):
        provider = spec["provider"]
        return kind[provider]({**self._providers.get(provider, {}), **spec})

    def _build(self, key: str, spec: dict) -> Persona:
        folder = self._dir / key
        tts = self._stage(TTS, spec["tts"])
        if "convert" in spec["tts"]:
            convert = spec["tts"]["convert"]
            tts = Converted(tts, {**self._providers.get(convert["provider"], {}), **convert})
        if "fallback" in spec["tts"]:
            tts = WithFallback(tts, self._stage(TTS, spec["tts"]["fallback"]))
        sfx = {}
        for name, path in spec.get("sfx", {}).items():
            import soundfile as sf

            audio, rate = sf.read(folder / path, dtype="float32")
            sfx[name] = (audio.mean(axis=1) if audio.ndim > 1 else audio, rate)
        persona = Persona(
            key=key,
            name=spec["name"],
            description=spec["description"],
            language=spec.get("language", "en"),
            face=spec.get("face"),
            greeting=spec.get("greeting", ""),
            prompt=(folder / spec.get("prompt", "prompt.md")).read_text() + "\n\n" + self._common,
            history_turns=spec["llm"].get("history_turns", 8),
            stt=self._stage(STT, {"language": spec.get("language", "en"), **spec["stt"]}),
            llm=self._stage(LLM, spec["llm"]),
            tts=tts,
            sfx=sfx,
        )
        log.info("persona %s ready (brain %s, voice %s)", key, spec["llm"]["provider"], spec["tts"]["provider"])
        return persona

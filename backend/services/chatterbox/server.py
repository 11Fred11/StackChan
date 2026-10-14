"""Chatterbox voice-clone TTS as a small HTTP service.

Each voice is a folder of reference recordings in backend/voices/<name>/. The longest
clip is used as the reference (Chatterbox works best with 10–20 s of clean speech).

POST /tts {"text": ..., "voice": ...}  ->  raw int16 mono PCM, rate in X-Sample-Rate.
"""

import logging
import os

# Must be set before torch initialises CUDA; reduces fragmentation over long uptimes.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
import threading
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import uvicorn
import torch.nn.functional as F
from chatterbox.tts import ChatterboxTTS, T3Cond, drop_invalid_tokens, punc_norm
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

log = logging.getLogger("chatterbox")
VOICES = Path(__file__).resolve().parents[2] / "voices"
DEVICE = os.environ.get("CHATTERBOX_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")

model = ChatterboxTTS.from_pretrained(device=DEVICE)
lock = threading.Lock()  # one generation at a time; the model is not thread-safe
conds_cache: dict[tuple, object] = {}
app = FastAPI()


class Req(BaseModel):
    text: str
    voice: str
    reference: str | None = None  # a file in voices/<voice>/; default: the longest clip
    exaggeration: float = 0.5
    cfg_weight: float = 0.5
    temperature: float = 0.8
    seed: int | None = None  # fix the randomness to compare settings fairly


def reference_for(voice: str, reference: str | None) -> Path:
    if reference:
        path = VOICES / voice / reference
        if not path.is_file():
            raise HTTPException(404, f"voices/{voice}/{reference} not found")
        return path
    clips = sorted((VOICES / voice).glob("*.wav"), key=lambda p: sf.info(p).duration, reverse=True)
    if not clips:
        raise HTTPException(404, f"no .wav files in voices/{voice}")
    return clips[0]


TOKENS_PER_SECOND = 25  # Chatterbox speech tokens


class Runaway(Exception):
    """Generation hit its length budget: almost always gibberish or a stuck loop."""


def speech_budget(text: str) -> int:
    """Max speech tokens for a sentence: generous for slow, dramatic delivery, but finite."""
    seconds = min(30.0, 2.0 + len(text) / 8.0)
    return int(seconds * TOKENS_PER_SECOND)


def generate_once(text: str, exaggeration: float, cfg_weight: float, temperature: float, repetition_penalty: float) -> torch.Tensor:
    """chatterbox.tts.ChatterboxTTS.generate, but with a per-sentence token budget
    instead of its fixed 1000 tokens (40 s), so a runaway sentence stops early."""
    if exaggeration != model.conds.t3.emotion_adv[0, 0, 0]:
        cond = model.conds.t3
        model.conds.t3 = T3Cond(
            speaker_emb=cond.speaker_emb,
            cond_prompt_speech_tokens=cond.cond_prompt_speech_tokens,
            emotion_adv=exaggeration * torch.ones(1, 1, 1),
        ).to(device=model.device)
    text = punc_norm(text)
    text_tokens = model.tokenizer.text_to_tokens(text).to(model.device)
    if cfg_weight > 0.0:
        text_tokens = torch.cat([text_tokens, text_tokens], dim=0)
    text_tokens = F.pad(text_tokens, (1, 0), value=model.t3.hp.start_text_token)
    text_tokens = F.pad(text_tokens, (0, 1), value=model.t3.hp.stop_text_token)
    budget = speech_budget(text)
    with torch.inference_mode():
        speech_tokens = model.t3.inference(
            t3_cond=model.conds.t3, text_tokens=text_tokens, max_new_tokens=budget,
            temperature=temperature, cfg_weight=cfg_weight,
            repetition_penalty=repetition_penalty, min_p=0.05, top_p=1.0,
        )[0]
        if len(speech_tokens) >= budget - 1:
            raise Runaway(f"{len(speech_tokens)} tokens for {len(text)} chars")
        speech_tokens = drop_invalid_tokens(speech_tokens)
        speech_tokens = speech_tokens[speech_tokens < 6561].to(model.device)
        wav, _ = model.s3gen.inference(speech_tokens=speech_tokens, ref_dict=model.conds.gen)
        wav = wav.squeeze(0).detach().cpu().numpy()
        wav = model.watermarker.apply_watermark(wav, sample_rate=model.sr)
    return torch.from_numpy(wav).unsqueeze(0)


def generate(req: Req) -> torch.Tensor:
    try:
        return generate_once(req.text, req.exaggeration, req.cfg_weight, req.temperature, 1.2)
    except Runaway as e:
        log.warning("runaway generation (%s) for %r; retrying with safer settings", e, req.text)
    # Calmer, more literal settings rarely run away; if they do, the gateway falls back.
    try:
        return generate_once(req.text, min(req.exaggeration, 0.6), 0.5, 0.6, 1.5)
    except Runaway as e:
        log.warning("still running away (%s); giving up on %r", e, req.text)
        raise HTTPException(503, "voice generation ran away")


@app.post("/tts")
def tts(req: Req) -> Response:
    with lock:
        # One cache entry per reference clip; generate() applies the exaggeration itself.
        key = (req.voice, req.reference)
        if key not in conds_cache:
            ref = reference_for(req.voice, req.reference)
            log.info("preparing voice %s from %s", req.voice, ref.name)
            model.prepare_conditionals(str(ref), exaggeration=req.exaggeration)
            conds_cache[key] = model.conds
        model.conds = conds_cache[key]
        if req.seed is not None:
            torch.manual_seed(req.seed)
        try:
            wav = generate(req)
        except torch.OutOfMemoryError:
            log.warning("CUDA out of memory; freeing cache and retrying once")
            torch.cuda.empty_cache()
            wav = generate(req)
        finally:
            if DEVICE == "cuda":
                torch.cuda.empty_cache()  # the allocator otherwise keeps growing until the card is full
    pcm = (np.clip(wav.squeeze(0).cpu().numpy(), -1, 1) * 32767).astype(np.int16)
    return Response(pcm.tobytes(), media_type="application/octet-stream", headers={"X-Sample-Rate": str(model.sr)})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8001")))

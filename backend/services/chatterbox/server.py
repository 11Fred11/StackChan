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
from chatterbox.tts import ChatterboxTTS
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


def generate(req: Req) -> torch.Tensor:
    with torch.inference_mode():
        return model.generate(
            req.text, exaggeration=req.exaggeration, cfg_weight=req.cfg_weight, temperature=req.temperature
        )


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

"""RVC voice conversion as a small HTTP service, using an Applio checkout.

Converts speech (e.g. from the Chatterbox clone) into the timbre of a trained RVC model.
Models are Applio training folders: $APPLIO_DIR/logs/<model>/ (latest .pth + added_*.index).

Run with Applio's own environment:
    APPLIO_DIR=~/Projects/Applio $APPLIO_DIR/.venv/bin/python server.py

POST /convert?model=...&pitch=0&index_rate=0.5&protect=0.33
    body: raw int16 mono PCM, header X-Sample-Rate  ->  raw int16 PCM, rate in X-Sample-Rate
"""

import glob
import logging
import os
import sys
import threading

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
APPLIO_DIR = os.path.expanduser(os.environ.get("APPLIO_DIR", "~/Projects/Applio"))
sys.path.insert(0, APPLIO_DIR)
os.chdir(APPLIO_DIR)  # Applio resolves its pretrained models relative to its checkout

import librosa  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from rvc.infer.infer import VoiceConverter  # noqa: E402

log = logging.getLogger("rvc")
app = FastAPI()
vc = VoiceConverter()
lock = threading.Lock()


def model_files(model: str) -> tuple[str, str]:
    folder = os.path.join(APPLIO_DIR, "logs", model)
    weights = sorted(glob.glob(os.path.join(folder, f"{model}_*e_*s.pth")), key=os.path.getmtime)
    if not weights:
        raise HTTPException(404, f"no trained weights in {folder}")
    index = sorted(glob.glob(os.path.join(folder, "*.index")))
    return weights[-1], (index[-1] if index else "")


@app.post("/convert")
async def convert(request: Request, model: str, pitch: int = 0, index_rate: float = 0.5, protect: float = 0.33) -> Response:
    rate = int(request.headers.get("X-Sample-Rate", "24000"))
    audio = np.frombuffer(await request.body(), dtype=np.int16).astype(np.float32) / 32768.0
    audio16k = librosa.resample(audio, orig_sr=rate, target_sr=16000)
    weights, index = model_files(model)
    with lock:
        vc.get_vc(weights, 0)
        if not vc.hubert_model or vc.last_embedder_model != "contentvec":
            vc.load_hubert("contentvec")
            vc.last_embedder_model = "contentvec"
        try:
            out = vc.vc.pipeline(
                model=vc.hubert_model, net_g=vc.net_g, sid=0, audio=audio16k, pitch=pitch,
                f0_method="rmvpe", file_index=index, index_rate=index_rate, pitch_guidance=vc.use_f0,
                volume_envelope=1.0, version=vc.version, protect=protect,
                f0_autotune=False, f0_autotune_strength=1, proposed_pitch=False, proposed_pitch_threshold=155.0,
            )
        finally:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
    out = np.asarray(out, dtype=np.float32)
    if np.abs(out).max() > 1.5:  # some pipelines return int16-scaled floats
        out = out / 32768.0
    pcm = (np.clip(out, -1, 1) * 32767).astype(np.int16)
    return Response(pcm.tobytes(), media_type="application/octet-stream", headers={"X-Sample-Rate": str(vc.tgt_sr)})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8002")))

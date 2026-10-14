# Rick gateway

A self-hosted voice server for Stack-chan. It speaks the Xiaozhi device protocol (see
`firmware/xiaozhi-esp32/docs/websocket.md`), so the robot only needs its config URL pointed here.

Each stage is pluggable in `config.yaml`:

| Stage | Default | Alternative |
|---|---|---|
| Hearing | faster-whisper `small.en` (local CPU) | |
| Brain | Gemini Flash (cloud, `GEMINI_API_KEY` in `.env`) | Qwen3 8B via Ollama (local GPU) |
| Voice | Chatterbox voice clone (local GPU, `services/chatterbox`) | Kokoro stock voices (local CPU) |

Voices for Chatterbox are folders of reference recordings in `voices/<name>/` (gitignored).
Only clone voices you have the speaker's consent for.

Rick's personality is `persona/rick.md`. Settings are in `config.yaml`.

## Run

```bash
# one-time: Ollama with the model, and the Kokoro voice files
ollama pull qwen3:8b
mkdir -p models && cd models
curl -LO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -LO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
cd ..

# voice-clone service (Python 3.11, GPU)
(cd services/chatterbox && uv run python server.py) &

uv run rick-gateway
```

The robot must be able to reach port 8000 on this machine (e.g. `sudo ufw allow from 192.168.1.0/24 to any port 8000 proto tcp`).

## Point the robot at it

Set `CONFIG_OTA_URL` to `http://<this machine>:8000/xiaozhi/ota/` in `firmware/sdkconfig`, then build and flash.

## Test without the robot

```bash
uv run python tests/fake_device.py   # plays tests/question.wav as if spoken to the robot
```

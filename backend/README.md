# Rick gateway

A self-hosted voice server for Stack-chan. It speaks the Xiaozhi device protocol (see
`firmware/xiaozhi-esp32/docs/websocket.md`), so the robot only needs its config URL pointed here.

## Personas

Each persona is a folder in `personas/` with its own personality, hearing, brain, voice, face and language:

```
personas/
  _common.md          rules every persona gets (mood emoji, tools, limits)
  rick/               persona.yaml + prompt.md
  stackchan/          the original cheerful Stack-chan
  darja/              Tunisian Darija (disabled until the Darja GPU speech server is deployed)
```

`persona.yaml` picks a provider per stage:

| Stage | Providers |
|---|---|
| Hearing | `whisper` (local), `darja` (Tunisian, Darja GPU server) |
| Brain | `gemini` (cloud, `GEMINI_API_KEY` in `.env`), `ollama` (local) |
| Voice | `chatterbox` (local voice clone, `services/chatterbox`), `kokoro` (local stock voices), `darja` |

Shared connection settings for the providers are in `config.yaml`. Ask the robot to "switch to Stack-chan"
(or any persona's name) to swap; each robot remembers its persona in `state/devices.json`.

A voice can also pass through a trained RVC model for a closer timbre (`convert:` in `persona.yaml`).
`services/rvc` runs it from an [Applio](https://github.com/IAHispano/Applio) checkout, which is also used to train
the model (`core.py preprocess / extract / train`).

Voices for Chatterbox are folders of reference recordings in `voices/<name>/` (gitignored).
Only clone voices you have the speaker's consent for.

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

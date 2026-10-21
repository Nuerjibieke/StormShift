# Configuration

Start with `.env.example`. The Python entry point reads environment variables; it does not automatically load dotenv files.

~~~bash
set -a
source .env
set +a
.venv/bin/python main_v2.py --host "$STORMSHIFT_HOST" --port "$STORMSHIFT_PORT"
~~~

Run these commands from the project root. Relative data and cache paths are resolved from the working directory. Host and port are command-line options, so changing only their environment variables does not change a separately typed hard-coded launch command.

## Core settings

| Variable | Example | Purpose |
| --- | --- | --- |
| `STORMSHIFT_HOST` | `127.0.0.1` | Used by the documented launch command |
| `STORMSHIFT_PORT` | `8080` | Used by the documented launch command |
| `STORMSHIFT_DATA_DIR` | `./data` | Session and event SQLite database directory |
| `STORMSHIFT_CACHE_DIR` | `./.cache` | Optional model/voice cache directory |
| `STORMSHIFT_ENABLE_LLM` | `off` | Disable optional language-model requests |
| `STORMSHIFT_ENABLE_VOICE` | `off` | Disable optional speech recognition |

The application also creates `tmp/` inside the repository. Git ignores runtime directories, actual `.env` files, caches, databases, and model files.

## Optional local language models

The default exercise runs without language models. Prepare local OpenAI-compatible services separately and set:

~~~bash
STORMSHIFT_ENABLE_LLM=auto
STORMSHIFT_FAST_BASE_URL=http://127.0.0.1:8101/v1
STORMSHIFT_DEEP_BASE_URL=http://127.0.0.1:8102/v1
STORMSHIFT_FAST_MODEL=Qwen/Qwen3-8B
STORMSHIFT_DEEP_MODEL=Qwen/Qwen3-30B-A3B
~~~

Endpoint URLs include `/v1`. Model names must match the identifiers served by those endpoints. Unavailable services fall back to deterministic specialist behavior.

The current client does not provide an API-key setting for authenticated cloud services. Use endpoints appropriate for this client; keep credentials out of URLs and source files. This repository does not install CUDA, vLLM, or model weights. Check model and serving-software licenses separately.

`ops/stormshiftctl` includes optional vLLM helpers for an independently prepared `.venv-llm` environment and explicit GPU assignments. Its `start` command defaults to starting model services too; use `start api` for the API alone.

## Optional speech input

~~~bash
uv pip install --python .venv/bin/python -r requirements-voice.txt
export STORMSHIFT_ENABLE_VOICE=auto
export STORMSHIFT_WHISPER_DEVICE=cpu
export STORMSHIFT_WHISPER_COMPUTE=int8
~~~

Restart the API after changing settings. The first transcription can download a Whisper model to the configured cache. Browser microphone access requires permission and a secure context; localhost is suitable. Voice dependencies are separate from the core release lock file.

## Optional public data

The default exercise uses generated scenario data. Adapters in `stormshift_v2/data_sources.py` can request weather from Aviation Weather Center and aircraft states from OpenSky. OpenSky credentials are optional environment variables:

~~~bash
OPENSKY_CLIENT_ID=
OPENSKY_CLIENT_SECRET=
~~~

Supply your own account details only in the ignored local configuration. Runtime responses and credentials are not part of the source distribution. Review the [Aviation Weather Center API documentation](https://aviationweather.gov/data/api/) and [OpenSky documentation](https://openskynetwork.github.io/opensky-api/) before enabling these services.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Wrong or old frontend | Rebuild with `npm --prefix frontend ci && npm --prefix frontend run build` |
| Health reads `degraded` | Inspect components; model fallback and disabled voice are expected by default |
| Port 8080 is occupied | Stop the other process or choose a different port and update the SSH tunnel |
| Browser cannot connect | Check the server health endpoint, then the SSH tunnel |
| Missing Python package | Use the release virtual environment and `requirements-lock.txt` |
| Changes to `.env` have no effect | Reload it in the launching process and restart the API |

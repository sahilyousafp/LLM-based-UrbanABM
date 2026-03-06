# Backend LLM Providers

This folder contains the provider-agnostic LLM client used by the simulation backend.

The current runtime path is:

- `llm_config.py` - reads provider settings from environment variables
- `llm_client.py` - talks to Ollama, vLLM, OpenAI, DeepSeek, or any other OpenAI-compatible endpoint
- `llm_service.py` - older Ollama-specific helper kept for local summary experiments

`Backend\Agent\model.py` uses `llm_config.py` + `llm_client.py` for agent reasoning, so vLLM support belongs here.

## Supported providers

| Provider | `LLM_PROVIDER` | `LLM_BASE_URL` | `LLM_API_KEY` | Notes |
|---|---|---|---|---|
| Ollama | `ollama` | leave blank | leave blank | Defaults to `http://localhost:11434/v1`; supports HuggingFace GGUF models via `hf.co/...` |
| vLLM in Docker | `vllm` | leave blank (defaults to `http://localhost:8001/v1`) | leave blank (defaults to `vllm`) | Run the Docker container on port `8001` so it does not collide with the FastAPI backend on `8000` |
| Docker Model Runner | `docker` | leave blank (defaults to `http://localhost:12434/engines/llama.cpp/v1`) | not needed | Requires Docker Desktop 4.40+ with Model Runner enabled |
| OpenAI-compatible custom server | `custom` | required | provider-specific | Any OpenAI-compatible endpoint works if `LLM_BASE_URL` is set |

## Quick start

### Option A - Ollama (default model)

```powershell
ollama serve
ollama pull llama3.1

$env:LLM_PROVIDER = "ollama"
$env:LLM_MODEL = "llama3.1"
python Backend\Agent\map_server.py
```

### Option B - vLLM in Docker with Qwen3.5-9B GGUF

Serves the `unsloth/Qwen3.5-9B-GGUF` (Q4_K_M) model via the official vLLM Docker image.
Requires an **NVIDIA GPU** with Docker `--runtime nvidia` support.

1. Start the vLLM server:

```powershell
Backend\LLM\start_vllm_docker.bat
```

This runs `docker run vllm/vllm-openai:latest` with `--model unsloth/Qwen3.5-9B-GGUF` on port `8001`.

2. Point the backend at vLLM:

```powershell
$env:LLM_PROVIDER = "vllm"
$env:LLM_MODEL = "unsloth/Qwen3.5-9B-GGUF"
python Backend\Agent\map_server.py
```

You can also hot-swap at runtime:

```powershell
curl -X POST "http://127.0.0.1:8000/api/config/llm?provider=vllm&model=unsloth/Qwen3.5-9B-GGUF"
```

> **No GPU?** Use Ollama instead — it already has the model downloaded:
> ```powershell
> $env:LLM_PROVIDER = "ollama"
> $env:LLM_MODEL = "hf.co/unsloth/Qwen3.5-9B-GGUF:Q4_K_M"
> ```

## Why vLLM failed without GPU access

If the Docker logs show `No CUDA runtime is found`, the container cannot see an NVIDIA GPU runtime.

Common causes:

- Docker was started without `--gpus all`
- the NVIDIA runtime / toolkit is not available to Docker
- the host machine does not expose a CUDA-capable GPU to the container
- the backend was pointed at the wrong port or `LLM_BASE_URL` was left blank

Docker Model Runner (`docker model run`) does **not** require GPU flags and avoids this issue entirely, but needs Docker Desktop 4.40+. Ollama with a GGUF model is the simpler no-GPU alternative.

## Files in this folder

- `README.md` - overview of supported providers
- `SETUP_GUIDE.md` - step-by-step setup for Ollama and Docker Model Runner
- `start_vllm_docker.bat` - Windows helper to pull and serve a model via Docker Model Runner
- `llm_config.py` - provider, model, timeout, and endpoint configuration
- `llm_client.py` - Async OpenAI-compatible client used by the backend

## Troubleshooting

### `docker model` command not found

Docker Model Runner requires **Docker Desktop 4.40+**. Update Docker Desktop and enable
Model Runner in Settings → Features in Development.

Alternatively, use Ollama — it supports the same GGUF models without any Docker version requirement:

```powershell
ollama pull hf.co/unsloth/Qwen3.5-9B-GGUF:Q4_K_M
$env:LLM_PROVIDER = "ollama"
$env:LLM_MODEL = "hf.co/unsloth/Qwen3.5-9B-GGUF:Q4_K_M"
```

### Backend still points to Ollama

- Using `LLM_PROVIDER=ollama` with a `hf.co/...` model ID is the recommended local path — Ollama resolves it automatically.
- If using `LLM_PROVIDER=custom`, you must provide an explicit `LLM_BASE_URL`.
- If `LLM_BASE_URL` is blank with `custom`, the backend falls back to the Ollama default URL.

### Port conflict

- `Backend\Agent\map_server.py` serves the API on `8000`.
- Ollama listens on `11434` — no conflict with the backend.

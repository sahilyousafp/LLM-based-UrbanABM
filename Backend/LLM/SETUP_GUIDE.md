# Quick Setup Guide for Backend LLM Providers

This guide covers the provider-agnostic backend in `Backend\LLM`.

The active simulation path uses:

- `Backend\LLM\llm_config.py`
- `Backend\LLM\llm_client.py`
- `Backend\Agent\model.py`

Choose one provider path below.

## Prerequisites

- Python dependencies installed from the project root
- One working LLM provider:
  - Ollama (recommended — supports HuggingFace GGUF models directly)

## Option A - Ollama

### 1. Install and start Ollama

```powershell
winget install Ollama.Ollama
ollama serve
```

### 2. Pull a model

```powershell
ollama pull llama3.1
```

### 3. Configure the backend shell

```powershell
$env:LLM_PROVIDER = "ollama"
$env:LLM_MODEL = "llama3.1"
Remove-Item Env:LLM_BASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:LLM_API_KEY -ErrorAction SilentlyContinue
```

### 4. Start the backend

```powershell
python Backend\Agent\map_server.py
```

## Option B - vLLM in Docker with Qwen3.5-9B GGUF

Requires an **NVIDIA GPU** with Docker `--runtime nvidia` support. No HF token needed — `unsloth/Qwen3.5-9B-GGUF` is a public model.

### 1. Start the vLLM server

```powershell
Backend\LLM\start_vllm_docker.bat
```

This runs:
```
docker run --runtime nvidia --gpus all vllm/vllm-openai:latest \
  --model unsloth/Qwen3.5-9B-GGUF \
  --tokenizer Qwen/Qwen2.5-9B-Instruct \
  --max-model-len 8192
```
on port `8001`. First run downloads the model (~5 GB) into the HuggingFace cache.

### 2. Point the backend at vLLM

```powershell
$env:LLM_PROVIDER = "vllm"
$env:LLM_MODEL = "unsloth/Qwen3.5-9B-GGUF"
```

Then start the backend:

```powershell
python Backend\Agent\map_server.py
```

### 3. Verify the endpoint

```powershell
curl http://127.0.0.1:8001/v1/models
```

> **No GPU / prefer CPU?** Use Ollama — the model is already downloaded locally:
> ```powershell
> $env:LLM_PROVIDER = "ollama"
> $env:LLM_MODEL = "hf.co/unsloth/Qwen3.5-9B-GGUF:Q4_K_M"
> ```

## Hot-swap provider at runtime

If the backend is already running, you can reconfigure it without restarting:

```powershell
curl -X POST "http://127.0.0.1:8000/api/config/llm?provider=vllm&model=unsloth/Qwen3.5-9B-GGUF"
```

## Test the backend API

```powershell
curl http://127.0.0.1:8000/api/agent/0/summary
curl http://127.0.0.1:8000/api/llm/stats
```

## Troubleshooting

### `ollama: command not found`

Install Ollama and make sure it is running:

```powershell
winget install Ollama.Ollama
ollama serve
```

### `docker model` command not found (Docker Model Runner)

Docker Model Runner requires **Docker Desktop 4.40 or later**. Use Ollama instead — it supports
the same `hf.co/...` GGUF model paths without any Docker version requirement.

### Backend still talks to Ollama when using a different provider

- For local GGUF inference, keep `LLM_PROVIDER=ollama` — that is the correct setting.
- If you use `LLM_PROVIDER=custom`, you must also set an explicit `LLM_BASE_URL`.
- If `LLM_BASE_URL` is blank and provider is `custom`, the backend falls back to the Ollama default URL.

### Port conflict

- the backend API listens on `8000`
- Ollama listens on `11434` — no conflict with the backend

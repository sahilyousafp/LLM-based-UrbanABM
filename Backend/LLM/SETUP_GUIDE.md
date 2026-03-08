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

## Option B - vLLM in Docker (GPU only)

Requires an **NVIDIA GPU** with CUDA support and the NVIDIA Container Toolkit installed.
AWQ quantization is GPU-only — if you have no NVIDIA GPU, use Option A (Ollama) instead.

### 1. Verify GPU access

```powershell
nvidia-smi                       # must show your NVIDIA GPU
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi  # must work inside Docker
```

If either command fails, see the troubleshooting section below.

### 2. Start the vLLM server

```powershell
Backend\LLM\start_vllm_docker.bat
```

This runs:
```
docker run --gpus all vllm/vllm-openai:latest \
  --model hugging-quants/Meta-Llama-3.1-8B-Instruct-AWQ-INT4 \
  --quantization awq \
  --max-model-len 8192
```
on port `8001`. First run downloads the model (~4.5 GB) into the HuggingFace cache.

### 2. Point the backend at vLLM

```powershell
$env:LLM_PROVIDER = "vllm"
$env:LLM_MODEL = "hugging-quants/Meta-Llama-3.1-8B-Instruct-AWQ-INT4"
```

Then start the backend:

```powershell
python Backend\Agent\map_server.py
```

### 3. Verify the endpoint

```powershell
curl http://127.0.0.1:8001/v1/models
```

> **No NVIDIA GPU?** Use Ollama — it runs GGUF-quantized models on CPU:
> ```powershell
> $env:LLM_PROVIDER = "ollama"
> $env:LLM_MODEL = "hf.co/unsloth/Qwen3.5-9B-GGUF:Q4_K_M"
> ```
> AWQ quantization requires GPU hardware. GGUF quantization works on CPU.

## Hot-swap provider at runtime

If the backend is already running, you can reconfigure it without restarting:

```powershell
curl -X POST "http://127.0.0.1:8000/api/config/llm?provider=vllm&model=hugging-quants/Meta-Llama-3.1-8B-Instruct-AWQ-INT4"
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

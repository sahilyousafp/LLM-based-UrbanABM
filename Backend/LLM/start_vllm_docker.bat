@echo off
setlocal

REM ── Configuration ──────────────────────────────────────────────────
REM Use the HuggingFace model ID (not a Windows path).
REM The HF cache is volume-mounted into the container, so the model
REM resolves automatically via the HuggingFace hub cache layout.
set "VLLM_MODEL=Qwen/Qwen2.5-3B-Instruct-AWQ"
set "VLLM_HOST_PORT=8001"

if not exist "%USERPROFILE%\.cache\huggingface" mkdir "%USERPROFILE%\.cache\huggingface"

REM ── Pre-flight: check for NVIDIA GPU ───────────────────────────────
echo [vLLM] Checking for NVIDIA GPU...
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo.
    echo ========================================
    echo   ERROR: No NVIDIA GPU detected
    echo ========================================
    echo.
    echo   vLLM GPU mode requires an NVIDIA GPU with CUDA support.
    echo   This machine does not have one ^(or the NVIDIA driver is missing^).
    echo.
    echo   Recommended alternatives for CPU inference:
    echo     1. Use Ollama ^(already configured in .env^):
    echo          ollama serve
    echo          set LLM_PROVIDER=ollama
    echo          set LLM_MODEL=hf.co/unsloth/Qwen3.5-9B-GGUF:Q4_K_M
    echo.
    echo     2. Use Docker Model Runner ^(Docker Desktop 4.40+^):
    echo          set LLM_PROVIDER=docker
    echo.
    echo   vLLM CPU mode is NOT recommended for 8B+ models ^(extremely slow^).
    echo ========================================
    echo.
    exit /b 1
)

echo [vLLM] NVIDIA GPU found.
echo.
echo [vLLM] Starting vLLM Docker server (GPU)...
echo [vLLM] Model:    %VLLM_MODEL%
echo [vLLM] Endpoint: http://127.0.0.1:%VLLM_HOST_PORT%/v1
echo.

REM ── Stop any existing container on this port ───────────────────────
for /f "tokens=*" %%c in ('docker ps -q --filter "publish=%VLLM_HOST_PORT%"') do (
    echo [vLLM] Stopping existing container on port %VLLM_HOST_PORT%...
    docker stop %%c >nul
)

REM ── Launch vLLM container with GPU ─────────────────────────────────
REM Notes:
REM   --gpus all      : expose all NVIDIA GPUs to the container
REM   --ipc=host      : shared memory for multi-GPU / large batch inference
REM   VLLM_USE_V1=0   : disable the V1 engine (which spawns a separate subprocess
REM                     that loses CUDA access inside WSL2 / Docker Desktop on Windows)
REM   The AWQ INT4 model bundles its own tokenizer; no --tokenizer needed.
docker run --rm ^
  --gpus all ^
  --ipc=host ^
  -e VLLM_USE_V1=0 ^
  -p %VLLM_HOST_PORT%:8000 ^
  -v "%USERPROFILE%\.cache\huggingface:/root/.cache/huggingface" ^
  vllm/vllm-openai:latest ^
  %VLLM_MODEL% ^
  --host 0.0.0.0 ^
  --port 8000 ^
  --max-model-len 4096 ^
  --quantization awq_marlin ^
  --gpu-memory-utilization 0.85

if errorlevel 1 (
    echo.
    echo [vLLM] GPU container failed.
    echo [vLLM] Check the error above. Common causes:
    echo   - NVIDIA Container Toolkit not installed
    echo   - Docker Desktop WSL2 GPU passthrough not configured
    echo   - Insufficient GPU memory ^(need ~6 GB for AWQ INT4 8B model^)
    echo.
    echo [vLLM] For CPU inference, use Ollama instead:
    echo   ollama serve
    echo   set LLM_PROVIDER=ollama
    echo   set LLM_MODEL=hf.co/unsloth/Qwen3.5-9B-GGUF:Q4_K_M
    echo.
)


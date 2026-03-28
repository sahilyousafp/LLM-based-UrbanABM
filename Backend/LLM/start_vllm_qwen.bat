@echo off
setlocal

REM ── Configuration ──────────────────────────────────────────────────
REM Using Qwen2.5-Coder-1.5B-Instruct (HuggingFace model)
REM Optimized for 6GB available VRAM
REM HF model: https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct
set "VLLM_MODEL=Qwen/Qwen2.5-Coder-1.5B-Instruct"
set "VLLM_HOST_PORT=8001"

REM ── HuggingFace Authentication ─────────────────────────────────────
REM Set your HF_TOKEN in .env file or as a system environment variable
REM Get token from: https://huggingface.co/settings/tokens
if not defined HF_TOKEN set "HF_TOKEN=your-huggingface-token-here"

echo ========================================
echo   vLLM Server - Qwen2.5-Coder-1.5B-Instruct
echo ========================================
echo.
echo   Model: %VLLM_MODEL%
echo   HuggingFace: Qwen/Qwen2.5-Coder-1.5B-Instruct
echo   Endpoint: http://127.0.0.1:%VLLM_HOST_PORT%/v1
echo   Max Context: 4096 tokens
echo   GPU Memory: 55%% utilization (6GB VRAM optimized)
echo   Expected throughput: 50-100+ tokens/sec
echo   HF Token: configured
echo.

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
    echo   vLLM requires an NVIDIA GPU with CUDA support.
    echo.
    echo   Alternative: Use Ollama for this model:
    echo     1. ollama pull Qwen/Qwen2.5-Coder-1.5B-Instruct:latest
    echo     2. ollama serve
    echo     3. Update .env:
    echo          LLM_PROVIDER=ollama
    echo          LLM_MODEL=Qwen/Qwen2.5-Coder-1.5B-Instruct:latest
    echo.
    exit /b 1
)

echo [vLLM] NVIDIA GPU found.
echo.

REM ── Stop any existing container on this port ───────────────────────
for /f "tokens=*" %%c in ('docker ps -q --filter "publish=%VLLM_HOST_PORT%"') do (
    echo [vLLM] Stopping existing container on port %VLLM_HOST_PORT%...
    docker stop %%c >nul
)

REM ── Launch vLLM container with GPU ─────────────────────────────────
REM Notes:
REM   --gpus all              : expose all NVIDIA GPUs to the container
REM   --ipc=host              : shared memory for large batch inference
REM   VLLM_USE_V1=0           : disable V1 engine (avoids CUDA issues in WSL2)
REM   --gpu-memory-utilization 0.9 : 90% for lightweight 1.5B model
REM   --max-num-seqs 256      : high concurrency for parallel agent queries
echo [vLLM] Starting vLLM Docker server...
echo.

docker run --rm ^
  --gpus all ^
  --ipc=host ^
  -e VLLM_USE_V1=0 ^
  -e HF_TOKEN=%HF_TOKEN% ^
  -p %VLLM_HOST_PORT%:8000 ^
  -v "%USERPROFILE%\.cache\huggingface:/root/.cache/huggingface" ^
  vllm/vllm-openai:latest ^
  %VLLM_MODEL% ^
  --host 0.0.0.0 ^
  --port 8000 ^
  --max-model-len 4096 ^
  --gpu-memory-utilization 0.55 ^
  --max-num-seqs 96 ^
  --enforce-eager

if errorlevel 1 (
    echo.
    echo [vLLM] Container failed to start.
    echo [vLLM] Common causes:
    echo   - NVIDIA Container Toolkit not installed
    echo   - Docker Desktop WSL2 GPU passthrough not configured
    echo   - Model not available on HuggingFace
    echo.
    echo [vLLM] Try Ollama for this model instead:
    echo   ollama pull Qwen/Qwen2.5-Coder-1.5B-Instruct:latest
    echo   ollama serve
    echo.
)

endlocal

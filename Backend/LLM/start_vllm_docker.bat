@echo off
setlocal

REM ── Configuration ──────────────────────────────────────────────────
REM Use the HuggingFace model ID (not a Windows path).
REM The HF cache is volume-mounted into the container, so the model
REM resolves automatically via the HuggingFace hub cache layout.
set "VLLM_MODEL=hugging-quants/Meta-Llama-3.1-8B-Instruct-AWQ-INT4"
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

REM ── Launch vLLM container with GPU ─────────────────────────────────
REM Notes:
REM   --gpus all    : expose all NVIDIA GPUs to the container
REM   --ipc=host    : shared memory for multi-GPU / large batch inference
REM   The AWQ INT4 model bundles its own tokenizer; no --tokenizer needed.
docker run --rm ^
  --gpus all ^
  --ipc=host ^
  -p %VLLM_HOST_PORT%:8000 ^
  -v "%USERPROFILE%\.cache\huggingface:/root/.cache/huggingface" ^
  vllm/vllm-openai:latest ^
  --model %VLLM_MODEL% ^
  --host 0.0.0.0 ^
  --port 8000 ^
  --max-model-len 8192 ^
  --quantization awq

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


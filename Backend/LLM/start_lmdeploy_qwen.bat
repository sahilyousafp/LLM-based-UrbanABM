@echo off
setlocal enabledelayedexpansion

REM ── Load HF_TOKEN from .env (project root) ─────────────────────────
cd /d "%~dp0..\.."
if exist ".env" (
    for /f "tokens=1,2 delims==" %%a in ('findstr /r /c:"^HF_TOKEN=" .env') do (
        set "HF_TOKEN=%%b"
    )
)
cd /d "%~dp0"

REM ── Configuration ──────────────────────────────────────────────────
set "LMDEPLOY_MODEL=Qwen/Qwen2.5-Coder-1.5B-Instruct"
set "LMDEPLOY_HOST_PORT=8002"

echo ========================================
echo   LMDeploy Server - Qwen2.5-Coder-1.5B-Instruct
echo ========================================
echo.
echo   Model: %LMDEPLOY_MODEL%
echo   Endpoint: http://127.0.0.1:%LMDEPLOY_HOST_PORT%/v1
echo   HF Token: loaded from .env
echo.

if not exist "%USERPROFILE%\.cache\huggingface" mkdir "%USERPROFILE%\.cache\huggingface"

echo [LMDeploy] Checking for NVIDIA GPU...
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo ERROR: No NVIDIA GPU detected
    exit /b 1
)
echo [LMDeploy] NVIDIA GPU found.

for /f "tokens=*" %%c in ('docker ps -q --filter "publish=%LMDEPLOY_HOST_PORT%"') do (
    docker stop %%c >nul
)

echo [LMDeploy] Starting LMDeploy Docker server...

docker run --rm ^
  --gpus all ^
  --ipc=host ^
  -e HF_TOKEN=%HF_TOKEN% ^
  -e HF_HOME=/root/.cache/huggingface ^
  -p %LMDEPLOY_HOST_PORT%:23333 ^
  -v "%USERPROFILE%\.cache\huggingface:/root/.cache/huggingface" ^
  openmmlab/lmdeploy:latest ^
  lmdeploy serve api_server %LMDEPLOY_MODEL% ^
  --server-port 23333 ^
  --model-format hf ^
  --cache-max-entry-count 0.3 ^
  --model-name qwen2.5-coder ^
  --tp 1

endlocal

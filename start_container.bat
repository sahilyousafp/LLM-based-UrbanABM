@echo off
setlocal enabledelayedexpansion

echo ========================================
echo   LLM Container Launcher
echo ========================================
echo.

REM ── Load LLM_PROVIDER from .env ──────────────────────────────────
if exist "%~dp0.env" (
    for /f "tokens=1,2 delims==" %%A in ('findstr /r /c:"^LLM_PROVIDER=" "%~dp0.env"') do set "LLM_PROVIDER=%%B"
    for /f "tokens=1,2 delims==" %%A in ('findstr /r /c:"^HF_TOKEN=" "%~dp0.env"') do set "HF_TOKEN=%%B"
)

if not defined LLM_PROVIDER (
    echo ERROR: LLM_PROVIDER not found in .env
    exit /b 1
)

echo Provider: %LLM_PROVIDER%

if /i "%LLM_PROVIDER%"=="vllm" (
    echo [Container] Starting vLLM container...
    call "%~dp0Backend\LLM\start_vllm_qwen.bat"
    goto :end
)

if /i "%LLM_PROVIDER%"=="lmdeploy" (
    echo [Container] Starting LMDeploy container...
    call "%~dp0Backend\LLM\start_lmdeploy_qwen.bat"
    goto :end
)

echo [Container] LLM_PROVIDER=%LLM_PROVIDER% does not use a local container.
echo [Container] No action needed. Run the appropriate start script manually if required:
echo   vLLM:     Backend\LLM\start_vllm_qwen.bat
echo   LMDeploy: Backend\LLM\start_lmdeploy_qwen.bat

:end
endlocal

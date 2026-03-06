@echo off
setlocal

set "VLLM_MODEL=unsloth/Qwen3.5-9B-GGUF"
set "VLLM_TOKENIZER=Qwen/Qwen2.5-9B-Instruct"
set "VLLM_HOST_PORT=8001"

if not exist "%USERPROFILE%\.cache\huggingface" mkdir "%USERPROFILE%\.cache\huggingface"

echo [vLLM] Starting vLLM Docker server (GPU)...
echo [vLLM] Model:    %VLLM_MODEL%
echo [vLLM] Endpoint: http://127.0.0.1:%VLLM_HOST_PORT%/v1
echo.

docker run --rm ^
  --runtime nvidia ^
  --gpus all ^
  --ipc=host ^
  -p %VLLM_HOST_PORT%:8000 ^
  -v "%USERPROFILE%\.cache\huggingface:/root/.cache/huggingface" ^
  vllm/vllm-openai:latest ^
  --model %VLLM_MODEL% ^
  --tokenizer %VLLM_TOKENIZER% ^
  --host 0.0.0.0 ^
  --port 8000 ^
  --max-model-len 8192

if errorlevel 1 (
    echo.
    echo [vLLM] GPU mode failed. Retrying in CPU mode...
    echo [vLLM] Note: CPU inference is slow for a 9B model.
    echo.
    docker run --rm ^
      --ipc=host ^
      -p %VLLM_HOST_PORT%:8000 ^
      -v "%USERPROFILE%\.cache\huggingface:/root/.cache/huggingface" ^
      vllm/vllm-openai:latest ^
      --model %VLLM_MODEL% ^
      --tokenizer %VLLM_TOKENIZER% ^
      --host 0.0.0.0 ^
      --port 8000 ^
      --max-model-len 8192 ^
      --device cpu
)


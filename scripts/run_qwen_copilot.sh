#!/usr/bin/env bash
set -euo pipefail

: "${P109_COPILOT_API_KEY:?Set P109_COPILOT_API_KEY}"

model="${P109_COPILOT_MODEL:-Qwen/Qwen3-4B-Instruct-2507}"
revision="${P109_COPILOT_REVISION:-cdbee75f17c01a7cc42f958dc650907174af0554}"
port="${P109_COPILOT_PORT:-8001}"
export CUDA_VISIBLE_DEVICES="${P109_COPILOT_GPU:-1}"

args=(serve "$model" --host 127.0.0.1 --port "$port" --api-key "$P109_COPILOT_API_KEY"
      --revision "$revision" --dtype half --max-model-len 8192 --gpu-memory-utilization 0.9)
exec vllm "${args[@]}"

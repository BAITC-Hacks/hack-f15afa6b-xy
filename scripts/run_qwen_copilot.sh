#!/usr/bin/env bash
set -euo pipefail

: "${P109_COPILOT_API_KEY:?Set P109_COPILOT_API_KEY}"

model="${P109_COPILOT_MODEL:-Qwen/Qwen3-4B-Instruct-2507}"
revision="${P109_COPILOT_REVISION:-cdbee75f17c01a7cc42f958dc650907174af0554}"
port="${P109_COPILOT_PORT:-8003}"
root="$(cd "$(dirname "$0")/.." && pwd)"
python_bin="${P109_COPILOT_PYTHON:-$root/.venv/bin/python}"
export CUDA_VISIBLE_DEVICES="${P109_COPILOT_GPU:-1}"

exec "$python_bin" "$root/scripts/serve_qwen_copilot.py" --model "$model" --revision "$revision" \
  --device cuda:0 --host 127.0.0.1 --port "$port"

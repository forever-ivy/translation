#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/Users/Code/workflow/translation"
cd "$ROOT_DIR"
export PATH="$HOME/.npm-global/bin:$PATH"

if [[ -f ".env.v4.local" ]]; then
  set -a
  source ".env.v4.local"
  set +a
fi

exec "${V4_PYTHON_BIN:-/Users/Code/workflow/translation/.venv/bin/python}" -m scripts.skill_run_worker

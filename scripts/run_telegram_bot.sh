#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${OPENCLAW_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
cd "$ROOT_DIR"
export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"

if [[ -f ".env.v4.local" ]]; then
  set -a
  source ".env.v4.local"
  set +a
fi

PYTHON_BIN="${V4_PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

is_truthy() {
  local v
  v="$(echo "${1:-}" | tr '[:upper:]' '[:lower:]' | xargs)"
  case "$v" in
    1|true|yes|y|on) return 0 ;;
    0|false|no|n|off|"") return 1 ;;
    *) return 0 ;;
  esac
}

if is_truthy "${OPENCLAW_RUN_WORKER_AUTOSTART:-1}"; then
  WORKER_LOG="${OPENCLAW_RUN_WORKER_LOG_FILE:-$HOME/.openclaw/runtime/translation/run_worker.log}"
  mkdir -p "$(dirname "$WORKER_LOG")"
  # Best-effort: the worker has its own singleton lock; if another instance is
  # already running, it will exit immediately.
  nohup "$PYTHON_BIN" -m scripts.skill_run_worker >>"$WORKER_LOG" 2>&1 &
fi

exec "$PYTHON_BIN" -m scripts.telegram_bot

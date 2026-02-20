#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${OPENCLAW_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
cd "$ROOT_DIR"
export PATH="$HOME/.npm-global/bin:$PATH"

if [[ -f ".env.v4.local" ]]; then
  set -a
  source ".env.v4.local"
  set +a
fi

WORK_ROOT="${V4_WORK_ROOT:-$HOME/Translation Task}"
KB_ROOT="${V4_KB_ROOT:-$HOME/Knowledge Repository}"
NOTIFY_TARGET="${OPENCLAW_NOTIFY_TARGET:-}"

"${V4_PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}" \
  -m scripts.openclaw_v4_dispatcher \
  --work-root "$WORK_ROOT" \
  --kb-root "$KB_ROOT" \
  --notify-target "$NOTIFY_TARGET" \
  pending-reminder

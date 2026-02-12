#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/Users/Code/workflow/translation"
cd "$ROOT_DIR"

if [[ -f ".env.v4.local" ]]; then
  set -a
  source ".env.v4.local"
  set +a
fi

WORK_ROOT="${V4_WORK_ROOT:-/Users/ivy/Library/CloudStorage/OneDrive-Personal/Translation Task}"
KB_ROOT="${V4_KB_ROOT:-/Users/ivy/Library/CloudStorage/OneDrive-Personal/Knowledge Repository}"
NOTIFY_TARGET="${OPENCLAW_NOTIFY_TARGET:-+8615071054627}"

"${V4_PYTHON_BIN:-/Users/Code/workflow/translation/.venv/bin/python}" \
  -m scripts.openclaw_v4_dispatcher \
  --work-root "$WORK_ROOT" \
  --kb-root "$KB_ROOT" \
  --notify-target "$NOTIFY_TARGET" \
  pending-reminder

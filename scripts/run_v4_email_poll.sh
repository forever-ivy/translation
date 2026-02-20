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

: "${V4_IMAP_HOST:?V4_IMAP_HOST is required in .env.v4.local}"
: "${V4_IMAP_USER:?V4_IMAP_USER is required in .env.v4.local}"
: "${V4_IMAP_PASSWORD:?V4_IMAP_PASSWORD is required in .env.v4.local}"

if [[ "${V4_IMAP_PASSWORD}" == "REPLACE_WITH_163_IMAP_AUTH_CODE" ]]; then
  echo "V4_IMAP_PASSWORD is still placeholder. Set your real IMAP auth code in .env.v4.local." >&2
  exit 2
fi

WORK_ROOT="${V4_WORK_ROOT:-$HOME/Translation Task}"
KB_ROOT="${V4_KB_ROOT:-$HOME/Knowledge Repository}"
NOTIFY_TARGET="${OPENCLAW_NOTIFY_TARGET:-}"
AUTO_RUN_ARGS=()
if [[ "${V5_EMAIL_AUTO_RUN:-0}" == "1" ]]; then
  AUTO_RUN_ARGS+=(--auto-run)
fi

"${V4_PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}" \
  -m scripts.openclaw_v4_dispatcher \
  --work-root "$WORK_ROOT" \
  --kb-root "$KB_ROOT" \
  --notify-target "$NOTIFY_TARGET" \
  email-poll \
  --imap-host "$V4_IMAP_HOST" \
  --imap-port "${V4_IMAP_PORT:-993}" \
  --imap-user "$V4_IMAP_USER" \
  --imap-password "$V4_IMAP_PASSWORD" \
  --mailbox "${V4_IMAP_MAILBOX:-INBOX}" \
  --from-filter "${V4_IMAP_FROM_FILTER:-modeh@eventranz.com}" \
  --max-messages "${V4_IMAP_MAX_MESSAGES:-5}" \
  ${AUTO_RUN_ARGS[@]+"${AUTO_RUN_ARGS[@]}"}

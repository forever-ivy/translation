#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${OPENCLAW_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
cd "$ROOT_DIR"

echo "[INFO] run_telegram_bot.sh is now a compatibility wrapper. Use ./scripts/start.sh --telegram for unified lifecycle."
exec "$SCRIPT_DIR/start.sh" --telegram

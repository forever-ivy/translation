#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${OPENCLAW_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
cd "$ROOT_DIR"
SRC_SKILL_DIR="$ROOT_DIR/skills/translation-router"
DEST_SKILL_DIR="${OPENCLAW_WORKSPACE_SKILLS_DIR:-$HOME/.openclaw/workspace/skills}/translation-router"
ENV_FILE="$ROOT_DIR/.env.v4.local"
WORKSPACE_AGENTS_MD="${OPENCLAW_WORKSPACE_DIR:-$HOME/.openclaw/workspace}/AGENTS.md"

if [[ ! -f "$SRC_SKILL_DIR/SKILL.md" ]]; then
  echo "ERROR: source skill not found: $SRC_SKILL_DIR/SKILL.md" >&2
  exit 2
fi

mkdir -p "$DEST_SKILL_DIR"
cp "$SRC_SKILL_DIR/SKILL.md" "$DEST_SKILL_DIR/SKILL.md"
echo "Installed skill: $DEST_SKILL_DIR/SKILL.md"

if [[ -f "$WORKSPACE_AGENTS_MD" ]]; then
  tmp_file="$(mktemp)"
  awk '
    BEGIN {skip=0}
    /<!-- V5\.3_STRICT_ROUTER_BEGIN -->/ {skip=1; next}
    /<!-- V5\.3_STRICT_ROUTER_END -->/ {skip=0; next}
    /<!-- V6_SKILL_TOOLS_BEGIN -->/ {skip=1; next}
    /<!-- V6_SKILL_TOOLS_END -->/ {skip=0; next}
    skip==0 {print}
  ' "$WORKSPACE_AGENTS_MD" > "$tmp_file"
  cat >> "$tmp_file" <<'EOF'

<!-- V5.3_STRICT_ROUTER_BEGIN -->
## Telegram Strict Router (V6.0)

- For Telegram direct inbound task messages, do not translate in chat.
- Route immediately via:
  `"${V4_PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}" -m scripts.skill_message_router --work-root "<V4_WORK_ROOT>" --kb-root "<V4_KB_ROOT>" --notify-target "<CHAT_ID>" --raw-text "<RAW_MESSAGE>"`
- Allowed chat commands: `new`, `run`, `status`, `ok`, `no {reason}`, `rerun`.
- Mandatory flow: `new -> (send files/text) -> run`.
- Never ask for files again if attachments were already present.
- Never echo inline `<file ...>` payload content.
<!-- V5.3_STRICT_ROUTER_END -->

<!-- V6_SKILL_TOOLS_BEGIN -->
## Available Skill Tools (V6.0)

- **pdf-extract**: `/opt/homebrew/bin/pdftotext -layout <input.pdf> -` — layout-aware PDF text extraction
- **sheetsmith**: `python ~/.openclaw/workspace/skills/sheetsmith/scripts/sheetsmith.py preview <file> --rows 9999` — spreadsheet preview
- **openclaw-mem**: `openclaw memory search "<query>" --max-results 5 --json` — optional cross-session memory search (pipeline uses local SQLite memories by default)
<!-- V6_SKILL_TOOLS_END -->
EOF
  mv "$tmp_file" "$WORKSPACE_AGENTS_MD"
  echo "Patched strict-router rule into $WORKSPACE_AGENTS_MD"
fi

if [[ -f "$ENV_FILE" ]]; then
  if ! grep -q '^OPENCLAW_STRICT_ROUTER=' "$ENV_FILE"; then
    echo 'OPENCLAW_STRICT_ROUTER=1' >> "$ENV_FILE"
    echo "Added OPENCLAW_STRICT_ROUTER=1 to $ENV_FILE"
  fi
  if ! grep -q '^OPENCLAW_TRANSLATION_THINKING=' "$ENV_FILE"; then
    echo 'OPENCLAW_TRANSLATION_THINKING=high' >> "$ENV_FILE"
    echo "Added OPENCLAW_TRANSLATION_THINKING=high to $ENV_FILE"
  fi
  if ! grep -q '^OPENCLAW_REQUIRE_NEW=' "$ENV_FILE"; then
    echo 'OPENCLAW_REQUIRE_NEW=1' >> "$ENV_FILE"
    echo "Added OPENCLAW_REQUIRE_NEW=1 to $ENV_FILE"
  fi
  if ! grep -q '^OPENCLAW_RAG_BACKEND=' "$ENV_FILE"; then
    echo 'OPENCLAW_RAG_BACKEND=clawrag' >> "$ENV_FILE"
    echo "Added OPENCLAW_RAG_BACKEND=clawrag to $ENV_FILE"
  fi
  if ! grep -q '^OPENCLAW_RAG_BASE_URL=' "$ENV_FILE"; then
    echo 'OPENCLAW_RAG_BASE_URL=http://127.0.0.1:8080' >> "$ENV_FILE"
    echo "Added OPENCLAW_RAG_BASE_URL=http://127.0.0.1:8080 to $ENV_FILE"
  fi
  if ! grep -q '^OPENCLAW_RAG_COLLECTION=' "$ENV_FILE"; then
    echo 'OPENCLAW_RAG_COLLECTION=translation-kb' >> "$ENV_FILE"
    echo "Added OPENCLAW_RAG_COLLECTION=translation-kb to $ENV_FILE"
  fi
  if ! grep -q '^OPENCLAW_RAG_COLLECTION_MODE=' "$ENV_FILE"; then
    echo 'OPENCLAW_RAG_COLLECTION_MODE=auto' >> "$ENV_FILE"
    echo "Added OPENCLAW_RAG_COLLECTION_MODE=auto to $ENV_FILE"
  fi
  if ! grep -q '^OPENCLAW_KB_ISOLATION_MODE=' "$ENV_FILE"; then
    echo 'OPENCLAW_KB_ISOLATION_MODE=company_strict' >> "$ENV_FILE"
    echo "Added OPENCLAW_KB_ISOLATION_MODE=company_strict to $ENV_FILE"
  fi
  if ! grep -q '^OPENCLAW_ARCHIVE_REQUIRE_FINAL_UPLOAD=' "$ENV_FILE"; then
    echo 'OPENCLAW_ARCHIVE_REQUIRE_FINAL_UPLOAD=1' >> "$ENV_FILE"
    echo "Added OPENCLAW_ARCHIVE_REQUIRE_FINAL_UPLOAD=1 to $ENV_FILE"
  fi
  if ! grep -q '^OPENCLAW_STATE_DB_PATH=' "$ENV_FILE"; then
    echo "OPENCLAW_STATE_DB_PATH=$HOME/.openclaw/runtime/translation/state.sqlite" >> "$ENV_FILE"
    echo "Added OPENCLAW_STATE_DB_PATH=$HOME/.openclaw/runtime/translation/state.sqlite to $ENV_FILE"
  fi
else
  cat >"$ENV_FILE" <<'EOF'
OPENCLAW_STRICT_ROUTER=1
OPENCLAW_TRANSLATION_THINKING=high
OPENCLAW_REQUIRE_NEW=1
OPENCLAW_RAG_BACKEND=clawrag
OPENCLAW_RAG_BASE_URL=http://127.0.0.1:8080
OPENCLAW_RAG_COLLECTION=translation-kb
OPENCLAW_RAG_COLLECTION_MODE=auto
OPENCLAW_KB_ISOLATION_MODE=company_strict
OPENCLAW_ARCHIVE_REQUIRE_FINAL_UPLOAD=1
OPENCLAW_STATE_DB_PATH=$HOME/.openclaw/runtime/translation/state.sqlite
EOF
  echo "Created $ENV_FILE with strict router + high reasoning defaults."
fi

if openclaw gateway restart >/dev/null 2>&1; then
  echo "OpenClaw gateway restarted (service mode)."
else
  echo "Gateway service restart unavailable, using foreground restart."
  openclaw gateway --force >/dev/null
  echo "OpenClaw gateway restarted (foreground mode)."
fi

echo "Verify:"
echo "  openclaw skills list | rg translation-router"
echo "  openclaw health --json"

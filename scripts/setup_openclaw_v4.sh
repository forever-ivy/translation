#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/Users/Code/workflow/translation"
WORKSPACE_DIR="${OPENCLAW_WORKSPACE_DIR:-$ROOT_DIR}"
PRIMARY_MODEL="${OPENCLAW_PRIMARY_MODEL:-openai-codex/gpt-5.2-codex}"
FALLBACK_MODEL="${OPENCLAW_FALLBACK_MODEL:-google/gemini-2.5-pro}"
OPENCLAW_WORKSPACE_SKILL_ROOT="${OPENCLAW_WORKSPACE_SKILL_ROOT:-$HOME/.openclaw/workspace}"
SKILL_LOCK_FILE="${SKILL_LOCK_FILE:-$ROOT_DIR/config/skill-lock.v6.json}"

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required" >&2
  exit 2
fi

ensure_agent() {
  local agent_id="$1"
  local model_id="$2"
  if openclaw agents list --json 2>/dev/null | jq -e --arg id "$agent_id" '.[] | select(.id == $id)' >/dev/null; then
    echo "Agent exists: $agent_id"
    return 0
  fi
  openclaw agents add "$agent_id" \
    --non-interactive \
    --workspace "$WORKSPACE_DIR" \
    --model "$model_id" \
    --json >/dev/null
  echo "Agent created: $agent_id"
}

upsert_cron_job() {
  local name="$1"
  shift
  local existing_ids
  existing_ids="$(openclaw cron list --json 2>/dev/null | jq -r --arg n "$name" '(.jobs // .items // [])[]? | select(.name==$n) | .id' || true)"
  if [[ -n "$existing_ids" ]]; then
    while IFS= read -r id; do
      [[ -z "$id" ]] && continue
      openclaw cron rm "$id" --json >/dev/null || true
    done <<< "$existing_ids"
  fi
  openclaw cron add --name "$name" "$@" --json >/dev/null
  echo "Cron configured: $name"
}

install_community_skills_from_lock() {
  if [[ ! -f "$SKILL_LOCK_FILE" ]]; then
    echo "WARN: skill lock not found: $SKILL_LOCK_FILE (skip community skill install)"
    return 0
  fi
  if ! command -v npx >/dev/null 2>&1; then
    echo "WARN: npx not found, skip community skill install"
    return 0
  fi

  echo "Installing community skills from lock: $SKILL_LOCK_FILE"
  while IFS= read -r item; do
    local slug version required
    slug="$(jq -r '.slug' <<<"$item")"
    version="$(jq -r '.version' <<<"$item")"
    required="$(jq -r '.required' <<<"$item")"
    [[ -z "$slug" || -z "$version" ]] && continue
    echo " - $slug@$version (required=$required)"
    if npx -y clawhub@latest --workdir "$OPENCLAW_WORKSPACE_SKILL_ROOT" --dir "skills" install "$slug" --version "$version" --force >/dev/null; then
      echo "   installed: $slug@$version"
    else
      if [[ "$required" == "true" ]]; then
        echo "ERROR: required skill install failed: $slug@$version" >&2
        exit 2
      fi
      echo "WARN: optional skill install failed: $slug@$version"
    fi
  done < <(jq -c '.skills[]' "$SKILL_LOCK_FILE")
}

echo "Ensuring V4 agents..."
ensure_agent "task-router" "$PRIMARY_MODEL"
ensure_agent "translator-core" "$PRIMARY_MODEL"
ensure_agent "review-core" "$FALLBACK_MODEL"
ensure_agent "qa-gate" "$PRIMARY_MODEL"

GLM_MODEL="${OPENCLAW_GLM_MODEL:-zai/glm-5}"
if [[ "${OPENCLAW_GLM_ENABLED:-0}" == "1" ]]; then
  ensure_agent "glm-reviewer" "$GLM_MODEL"
fi

echo "Configuring model routing..."
openclaw models set "$PRIMARY_MODEL"
openclaw models fallbacks clear || true
openclaw models fallbacks add "$FALLBACK_MODEL" || true

install_community_skills_from_lock

EMAIL_CMD="Execute this shell command exactly once and return only a short status JSON: cd $ROOT_DIR && ./scripts/run_v4_email_poll.sh"
REMINDER_CMD="Execute this shell command exactly once and return only a short status JSON: cd $ROOT_DIR && ./scripts/run_v4_pending_reminder.sh"

echo "Configuring OpenClaw cron jobs..."
upsert_cron_job "v4-email-poll" \
  --agent "task-router" \
  --every "2m" \
  --message "$EMAIL_CMD" \
  --no-deliver \
  --wake "now" \
  --timeout-seconds "120"

upsert_cron_job "v4-pending-reminder-am" \
  --agent "task-router" \
  --cron "0 9 * * *" \
  --tz "${OPENCLAW_CRON_TZ:-Asia/Shanghai}" \
  --message "$REMINDER_CMD" \
  --no-deliver \
  --wake "now" \
  --timeout-seconds "120"

upsert_cron_job "v4-pending-reminder-pm" \
  --agent "task-router" \
  --cron "0 19 * * *" \
  --tz "${OPENCLAW_CRON_TZ:-Asia/Shanghai}" \
  --message "$REMINDER_CMD" \
  --no-deliver \
  --wake "now" \
  --timeout-seconds "120"

echo
echo "V4 setup complete."
echo "Next:"
echo "1) Create $ROOT_DIR/.env.v4.local with IMAP credentials."
echo "2) chmod +x scripts/run_v4_email_poll.sh scripts/run_v4_pending_reminder.sh scripts/setup_openclaw_v4.sh"
echo "3) Install strict Telegram router skill: ./scripts/install_openclaw_translation_skill.sh"
echo "4) Check health: openclaw health --json"

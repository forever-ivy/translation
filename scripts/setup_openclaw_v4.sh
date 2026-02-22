#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${OPENCLAW_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
cd "$ROOT_DIR"
WORKSPACE_DIR="${OPENCLAW_WORKSPACE_DIR:-$ROOT_DIR}"
KIMI_CODING_MODEL="${OPENCLAW_KIMI_CODING_MODEL:-kimi-coding/k2p5}"
PRIMARY_MODEL="${OPENCLAW_PRIMARY_MODEL:-zai/glm-5}"
FALLBACK_MODEL="${OPENCLAW_FALLBACK_MODEL:-zai/glm-4.6v}"
IMAGE_MODEL="${OPENCLAW_IMAGE_MODEL:-$PRIMARY_MODEL}"
FALLBACK_CHAIN="${OPENCLAW_FALLBACK_CHAIN:-zai/glm-4.6v,openai-codex/gpt-5.3-codex,google-antigravity/gemini-3-flash}"
INCLUDE_KIMI_FALLBACK="${OPENCLAW_INCLUDE_KIMI_FALLBACK:-0}"
OPENCLAW_WORKSPACE_SKILL_ROOT="${OPENCLAW_WORKSPACE_SKILL_ROOT:-$HOME/.openclaw/workspace}"
SKILL_LOCK_FILE="${SKILL_LOCK_FILE:-$ROOT_DIR/config/skill-lock.v6.json}"
OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-$HOME/.openclaw/openclaw.json}"

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required" >&2
  exit 2
fi

ensure_agent() {
  local agent_id="$1"
  local model_id="$2"
  if openclaw agents list --json 2>/dev/null | jq -e --arg id "$agent_id" '.[] | select(.id == $id)' >/dev/null; then
    echo "Agent exists: $agent_id"
  else
    openclaw agents add "$agent_id" \
      --non-interactive \
      --workspace "$WORKSPACE_DIR" \
      --model "$model_id" \
      --json >/dev/null
    echo "Agent created: $agent_id"
  fi

  if openclaw models --agent "$agent_id" set "$model_id" >/dev/null 2>&1; then
    echo "Agent model set: $agent_id -> $model_id"
  else
    echo "WARN: failed to set model for $agent_id -> $model_id"
  fi

  force_agent_model_in_config "$agent_id" "$model_id"
  force_agent_workspace_in_config "$agent_id" "$WORKSPACE_DIR"
}

force_agent_model_in_config() {
  local agent_id="$1"
  local model_id="$2"
  [[ -z "$agent_id" || -z "$model_id" ]] && return 0
  [[ ! -f "$OPENCLAW_CONFIG_PATH" ]] && return 0

  local tmp
  tmp="$(mktemp)"
  if jq --arg id "$agent_id" --arg model "$model_id" '
    if (.agents.list // [] | any(.id == $id)) then
      .agents.list = ((.agents.list // []) | map(if .id == $id then .model = $model else . end))
    else
      .
    end
  ' "$OPENCLAW_CONFIG_PATH" > "$tmp"; then
    mv "$tmp" "$OPENCLAW_CONFIG_PATH"
    echo "Agent model forced in config: $agent_id -> $model_id"
  else
    rm -f "$tmp"
    echo "WARN: failed to update $OPENCLAW_CONFIG_PATH for agent $agent_id"
  fi
}

force_agent_workspace_in_config() {
  local agent_id="$1"
  local workspace="$2"
  [[ -z "$agent_id" || -z "$workspace" ]] && return 0
  [[ ! -f "$OPENCLAW_CONFIG_PATH" ]] && return 0

  local tmp
  tmp="$(mktemp)"
  if jq --arg id "$agent_id" --arg ws "$workspace" '
    if (.agents.list // [] | any(.id == $id)) then
      .agents.list = ((.agents.list // []) | map(if .id == $id then .workspace = $ws else . end))
    else
      .
    end
  ' "$OPENCLAW_CONFIG_PATH" > "$tmp"; then
    mv "$tmp" "$OPENCLAW_CONFIG_PATH"
    echo "Agent workspace forced in config: $agent_id -> $workspace"
  else
    rm -f "$tmp"
    echo "WARN: failed to update $OPENCLAW_CONFIG_PATH workspace for agent $agent_id"
  fi
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
    local installed_dir
    slug="$(jq -r '.slug' <<<"$item")"
    version="$(jq -r '.version' <<<"$item")"
    required="$(jq -r '.required' <<<"$item")"
    [[ -z "$slug" || -z "$version" ]] && continue
    installed_dir="$OPENCLAW_WORKSPACE_SKILL_ROOT/skills/$slug"
    if [[ -d "$installed_dir" ]]; then
      echo " - $slug@$version (required=$required)"
      echo "   already installed: $installed_dir (skip)"
      continue
    fi
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
ensure_agent "review-core" "$PRIMARY_MODEL"
ensure_agent "qa-gate" "$PRIMARY_MODEL"

GLM_MODEL="${OPENCLAW_GLM_MODEL:-zai/glm-5}"
if [[ "${OPENCLAW_GLM_ENABLED:-0}" == "1" ]]; then
  ensure_agent "glm-reviewer" "$PRIMARY_MODEL"
fi

echo "Configuring model routing..."
openclaw models set "$PRIMARY_MODEL"

# Enforce fallback order with a stable default chain.
declare -a DESIRED_MODELS=()
for model in "${GLM_MODEL}" "${FALLBACK_MODEL}"; do
  if [[ -n "$model" ]]; then
    DESIRED_MODELS+=("$model")
  fi
done
IFS=',' read -r -a EXTRA_MODELS <<<"$FALLBACK_CHAIN"
for model in "${EXTRA_MODELS[@]}"; do
  model="$(echo "$model" | xargs)"
  [[ -z "$model" ]] && continue
  DESIRED_MODELS+=("$model")
done
if [[ "$INCLUDE_KIMI_FALLBACK" == "1" && -n "$KIMI_CODING_MODEL" ]]; then
  DESIRED_MODELS+=("$KIMI_CODING_MODEL")
fi

# de-duplicate while preserving order
declare -a UNIQ_MODELS=()
for model in "${DESIRED_MODELS[@]:-}"; do
  [[ -z "$model" ]] && continue
  [[ "$model" == "$PRIMARY_MODEL" ]] && continue
  skip=0
  for existing in "${UNIQ_MODELS[@]:-}"; do
    if [[ "$existing" == "$model" ]]; then
      skip=1
      break
    fi
  done
  [[ $skip -eq 1 ]] && continue
  UNIQ_MODELS+=("$model")
done

echo "Updating OpenClaw fallbacks..."
openclaw models fallbacks clear || true
for model in "${UNIQ_MODELS[@]:-}"; do
  openclaw models fallbacks add "$model" || true
done

# Configure image model for vision workflows (best-effort).
if [[ -n "$IMAGE_MODEL" ]]; then
  openclaw models set-image "$IMAGE_MODEL" || true
  for agent in task-router translator-core review-core qa-gate glm-reviewer; do
    openclaw models --agent "$agent" set-image "$IMAGE_MODEL" >/dev/null 2>&1 || true
  done
fi

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
  --tz "${OPENCLAW_CRON_TZ:-America/New_York}" \
  --message "$REMINDER_CMD" \
  --no-deliver \
  --wake "now" \
  --timeout-seconds "120"

upsert_cron_job "v4-pending-reminder-pm" \
  --agent "task-router" \
  --cron "0 19 * * *" \
  --tz "${OPENCLAW_CRON_TZ:-America/New_York}" \
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

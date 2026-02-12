#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   OPENCLAW_HOOK_TOKEN="..." ./scripts/setup_openclaw_v2.sh
# Optional:
#   OPENCLAW_PRIMARY_MODEL="openai-codex/gpt-5.3-codex" OPENCLAW_GEMINI_MODEL="google/gemini-2.5-pro" ./scripts/setup_openclaw_v2.sh

HOOK_TOKEN="${OPENCLAW_HOOK_TOKEN:-}"
if [[ -z "$HOOK_TOKEN" ]]; then
  echo "ERROR: OPENCLAW_HOOK_TOKEN is required" >&2
  exit 2
fi

PRIMARY_MODEL="${OPENCLAW_PRIMARY_MODEL:-openai-codex/gpt-5.3-codex}"
GEMINI_MODEL="${OPENCLAW_GEMINI_MODEL:-google/gemini-2.5-pro}"
WORKSPACE_DIR="${OPENCLAW_WORKSPACE_DIR:-/Users/Code/workflow/translation}"

ensure_agent() {
  local agent_id="$1"
  local model_id="$2"

  if openclaw agents list --json 2>/dev/null | jq -e --arg id "$agent_id" '.[] | select(.id == $id)' >/dev/null; then
    echo "Agent exists: $agent_id"
    return 0
  fi

  echo "Creating agent: $agent_id"
  openclaw agents add "$agent_id" \
    --non-interactive \
    --workspace "$WORKSPACE_DIR" \
    --model "$model_id" \
    --json >/dev/null
}

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required by setup_openclaw_v2.sh" >&2
  exit 3
fi

echo "Ensuring translator agents..."
ensure_agent "translator-main" "$PRIMARY_MODEL"
ensure_agent "translator-diff" "${OPENCLAW_DIFF_MODEL:-$PRIMARY_MODEL}"
ensure_agent "translator-draft" "${OPENCLAW_DRAFT_MODEL:-$PRIMARY_MODEL}"
ensure_agent "translator-qa" "${OPENCLAW_QA_MODEL:-$PRIMARY_MODEL}"

echo "Configuring OpenClaw hooks..."
openclaw config set hooks.enabled true
openclaw config set hooks.path /hooks
openclaw config set hooks.token "$HOOK_TOKEN"

# Agent restriction key is version-dependent. Apply only when supported.
if openclaw config set hooks.allowedAgentIds '["translator-main"]' >/dev/null 2>&1; then
  echo "Applied hooks.allowedAgentIds=[translator-main]"
else
  echo "NOTICE: hooks.allowedAgentIds not supported in this OpenClaw version; enforce agentId in n8n payload."
fi

echo "Configuring model routing..."
openclaw models set "$PRIMARY_MODEL"

# Reset and set primary fallback chain.
openclaw models fallbacks clear || true
openclaw models fallbacks add "$GEMINI_MODEL" || true

echo "Done. Restart OpenClaw gateway to apply changes:"
echo "  openclaw gateway --force"

#!/usr/bin/env bash
set -euo pipefail

FORMAT="${1:---format}"
FORMAT_VAL="${2:-json}"
if [[ "$FORMAT" == "--format" ]]; then
  FORMAT_VAL="${2:-json}"
elif [[ "$FORMAT" == "--text" ]]; then
  FORMAT_VAL="text"
fi

# --- Tool detection helpers ---
detect_binary() {
  local cmd="$1"
  if command -v "$cmd" &>/dev/null; then
    command -v "$cmd"
  else
    echo ""
  fi
}

get_version() {
  local cmd="$1"
  local path
  path="$(detect_binary "$cmd")"
  [[ -z "$path" ]] && echo "" && return
  "$cmd" --version 2>/dev/null | head -1 || echo "unknown"
}

# --- Claude Code ---
CLAUDE_PATH="$(detect_binary claude)"
CLAUDE_VERSION=""
CLAUDE_AUTH=false
if [[ -n "$CLAUDE_PATH" ]]; then
  CLAUDE_VERSION="$(get_version claude)"
  [[ -d "$HOME/.claude" ]] && CLAUDE_AUTH=true
fi

# --- Codex ---
CODEX_PATH="$(detect_binary codex)"
CODEX_VERSION=""
CODEX_AUTH=false
if [[ -n "$CODEX_PATH" ]]; then
  CODEX_VERSION="$(get_version codex)"
  [[ -f "$HOME/.codex/config.json" ]] || [[ -n "${OPENAI_API_KEY:-}" ]] && CODEX_AUTH=true
fi

# --- OpenCode ---
OPENCODE_PATH="$(detect_binary opencode)"
OPENCODE_VERSION=""
OPENCODE_AUTH=false
if [[ -n "$OPENCODE_PATH" ]]; then
  OPENCODE_VERSION="$(get_version opencode)"
  [[ -f "$HOME/.local/share/opencode/auth.json" ]] && OPENCODE_AUTH=true
fi

# --- OpenClaw ---
OPENCLAW_PATH="$(detect_binary openclaw)"
OPENCLAW_VERSION=""
OPENCLAW_AUTH=false
if [[ -n "$OPENCLAW_PATH" ]]; then
  OPENCLAW_VERSION="$(get_version openclaw)"
  [[ -d "$HOME/.config/openclaw" ]] || [[ -d "$HOME/.openclaw" ]] && OPENCLAW_AUTH=true
fi

# --- Output ---
available_count=0
[[ -n "$CLAUDE_PATH" ]] && ((available_count++)) || true
[[ -n "$CODEX_PATH" ]] && ((available_count++)) || true
[[ -n "$OPENCODE_PATH" ]] && ((available_count++)) || true
[[ -n "$OPENCLAW_PATH" ]] && ((available_count++)) || true

if [[ "$FORMAT_VAL" == "text" ]]; then
  echo "=== Unified Agent Bridge: Tool Detection ==="
  echo ""
  printf "%-15s %-10s %-8s %-8s %s\n" "Tool" "Available" "Auth" "Version" "Path"
  printf "%-15s %-10s %-8s %-8s %s\n" "----" "---------" "----" "-------" "----"
  printf "%-15s %-10s %-8s %-8s %s\n" "claude-code" "$([[ -n "$CLAUDE_PATH" ]] && echo yes || echo no)" "$CLAUDE_AUTH" "$CLAUDE_VERSION" "$CLAUDE_PATH"
  printf "%-15s %-10s %-8s %-8s %s\n" "codex" "$([[ -n "$CODEX_PATH" ]] && echo yes || echo no)" "$CODEX_AUTH" "$CODEX_VERSION" "$CODEX_PATH"
  printf "%-15s %-10s %-8s %-8s %s\n" "opencode" "$([[ -n "$OPENCODE_PATH" ]] && echo yes || echo no)" "$OPENCODE_AUTH" "$OPENCODE_VERSION" "$OPENCODE_PATH"
  printf "%-15s %-10s %-8s %-8s %s\n" "openclaw" "$([[ -n "$OPENCLAW_PATH" ]] && echo yes || echo no)" "$OPENCLAW_AUTH" "$OPENCLAW_VERSION" "$OPENCLAW_PATH"
  echo ""
  echo "Available: $available_count/4 tools"
else
  python3 -c "
import json, sys
data = {
    'available_count': $available_count,
    'tools': {
        'claude-code': {
            'available': bool('$CLAUDE_PATH'),
            'version': '$CLAUDE_VERSION' or None,
            'authenticated': $([[ "$CLAUDE_AUTH" == "true" ]] && echo True || echo False),
            'path': '$CLAUDE_PATH' or None
        },
        'codex': {
            'available': bool('$CODEX_PATH'),
            'version': '$CODEX_VERSION' or None,
            'authenticated': $([[ "$CODEX_AUTH" == "true" ]] && echo True || echo False),
            'path': '$CODEX_PATH' or None
        },
        'opencode': {
            'available': bool('$OPENCODE_PATH'),
            'version': '$OPENCODE_VERSION' or None,
            'authenticated': $([[ "$OPENCODE_AUTH" == "true" ]] && echo True || echo False),
            'path': '$OPENCODE_PATH' or None
        },
        'openclaw': {
            'available': bool('$OPENCLAW_PATH'),
            'version': '$OPENCLAW_VERSION' or None,
            'authenticated': $([[ "$OPENCLAW_AUTH" == "true" ]] && echo True || echo False),
            'path': '$OPENCLAW_PATH' or None
        }
    }
}
print(json.dumps(data, indent=2))
"
fi

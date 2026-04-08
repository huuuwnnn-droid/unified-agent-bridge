#!/usr/bin/env bash
set -euo pipefail

# --- Args ---
TASK=${1:-}
MODE=${2:-execute}
MODEL=${3:-""}
WORKDIR=${4:-.}
EXTRA_ARGS=${5:-""}

# --- Helpers ---
run_with_timeout() {
  local secs="$1"; shift
  if command -v timeout &>/dev/null; then
    timeout "$secs" "$@"
  elif command -v gtimeout &>/dev/null; then
    gtimeout "$secs" "$@"
  else
    "$@" &
    local pid=$!
    ( sleep "$secs" && kill "$pid" 2>/dev/null ) &
    local watcher=$!
    wait "$pid" 2>/dev/null
    local rc=$?
    kill "$watcher" 2>/dev/null
    wait "$watcher" 2>/dev/null
    return $rc
  fi
}

log_debug() {
  printf '[codex] %s\n' "$*" >&2
}

now_ms() {
  python3 -c 'import time; print(int(time.time() * 1000))'
}

emit_json() {
  python3 - "$@" <<'PY'
import json
import sys

tool, task, status, output, tokens_used, cost_usd, duration_ms, exit_code, errors_json = sys.argv[1:10]
try:
    errors = json.loads(errors_json)
except Exception:
    errors = [errors_json] if errors_json else []

payload = {
    "tool": tool,
    "task": task,
    "status": status,
    "output": output,
    "files_changed": [],
    "tokens_used": int(tokens_used or 0),
    "cost_usd": float(cost_usd or 0.0),
    "duration_ms": int(duration_ms or 0),
    "exit_code": int(exit_code or 0),
    "errors": errors,
}
print(json.dumps(payload, ensure_ascii=False))
PY
}

parse_response() {
  RAW_OUTPUT="$1" python3 - <<'PY'
import json
import os

raw = os.environ.get("RAW_OUTPUT", "")
lines = [line for line in raw.splitlines() if line.strip()]
last_text = ""
tokens = 0
cost = 0.0

for line in lines:
    try:
        event = json.loads(line)
    except Exception:
        continue
    if not isinstance(event, dict):
        continue
    event_type = str(event.get("type") or event.get("event") or "").lower()
    text = event.get("text") or event.get("content") or event.get("message") or event.get("output")
    if isinstance(text, str) and text:
        if event_type in {"text", "message", "response.output_text.delta", "response.completed"} or not event_type:
            last_text = text
    usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
    if isinstance(event.get("tokens_used"), (int, float)):
        tokens = int(event["tokens_used"])
    elif isinstance(event.get("total_tokens"), (int, float)):
        tokens = int(event["total_tokens"])
    elif isinstance(usage.get("total_tokens"), (int, float)):
        tokens = int(usage["total_tokens"])
    if isinstance(event.get("cost_usd"), (int, float)):
        cost = float(event["cost_usd"])
    elif isinstance(event.get("total_cost_usd"), (int, float)):
        cost = float(event["total_cost_usd"])

if not last_text:
    last_text = raw

print(json.dumps({"output": last_text, "tokens_used": tokens, "cost_usd": cost}, ensure_ascii=False))
PY
}

# --- Validation ---
START_MS=$(now_ms)

if [[ -z "$TASK" ]]; then
  END_MS=$(now_ms)
  emit_json "codex" "$TASK" "failed" "" "0" "0.0" "$((END_MS - START_MS))" "2" '["missing task"]'
  exit 0
fi

if [[ ! -d "$WORKDIR" ]]; then
  END_MS=$(now_ms)
  emit_json "codex" "$TASK" "failed" "" "0" "0.0" "$((END_MS - START_MS))" "2" '["workdir not found"]'
  exit 0
fi

if ! command -v codex >/dev/null 2>&1; then
  END_MS=$(now_ms)
  emit_json "codex" "$TASK" "failed" "" "0" "0.0" "$((END_MS - START_MS))" "127" '["tool not installed"]'
  exit 0
fi

cd "$WORKDIR"

# --- Command ---
CMD=(codex exec "$TASK" --json --ephemeral)

case "$MODE" in
  reason)
    CMD+=(--sandbox read-only)
    ;;
  execute)
    CMD+=(--sandbox workspace-write --full-auto)
    ;;
  review|readonly)
    CMD+=(--sandbox read-only)
    ;;
  *)
    CMD+=(--sandbox workspace-write --full-auto)
    ;;
esac

if [[ -n "$MODEL" ]]; then
  CMD+=(--model "$MODEL")
fi

if [[ -n "$EXTRA_ARGS" ]]; then
  # shellcheck disable=SC2206
  EXTRA_PARTS=($EXTRA_ARGS)
  CMD+=("${EXTRA_PARTS[@]}")
fi

log_debug "mode=$MODE model=${MODEL:-default} workdir=$WORKDIR"
log_debug "command=${CMD[*]}"

# --- Execution ---
STDOUT_FILE=$(mktemp)
STDERR_FILE=$(mktemp)
EXIT_CODE=0
set +e
run_with_timeout 300 "${CMD[@]}" >"$STDOUT_FILE" 2>"$STDERR_FILE"
EXIT_CODE=$?
set -e

RAW_OUTPUT=$(<"$STDOUT_FILE")
DEBUG_OUTPUT=$(<"$STDERR_FILE")
rm -f "$STDOUT_FILE" "$STDERR_FILE"

if [[ -n "$DEBUG_OUTPUT" ]]; then
  printf '%s\n' "$DEBUG_OUTPUT" >&2
fi

PARSED=$(parse_response "$RAW_OUTPUT")
PARSED_OUTPUT=$(PARSED_JSON="$PARSED" python3 - <<'PY'
import json
import os
print(json.loads(os.environ["PARSED_JSON"])["output"])
PY
)
TOKENS_USED=$(PARSED_JSON="$PARSED" python3 - <<'PY'
import json
import os
print(json.loads(os.environ["PARSED_JSON"])["tokens_used"])
PY
)
COST_USD=$(PARSED_JSON="$PARSED" python3 - <<'PY'
import json
import os
print(json.loads(os.environ["PARSED_JSON"])["cost_usd"])
PY
)

STATUS="success"
ERRORS='[]'
LOWER_OUTPUT=$(RAW_OUTPUT="$RAW_OUTPUT" DEBUG_OUTPUT="$DEBUG_OUTPUT" python3 - <<'PY'
import os
print((os.environ.get("RAW_OUTPUT", "") + "\n" + os.environ.get("DEBUG_OUTPUT", "")).lower())
PY
)

if [[ $EXIT_CODE -eq 124 ]]; then
  STATUS="timeout"
  ERRORS='["command timed out"]'
elif [[ $EXIT_CODE -ne 0 ]]; then
  if [[ "$LOWER_OUTPUT" == *rate* || "$LOWER_OUTPUT" == *429* || "$LOWER_OUTPUT" == *503* ]]; then
    STATUS="rate_limited"
    ERRORS='["rate limit detected"]'
  else
    STATUS="failed"
    ERRORS='["command failed"]'
  fi
fi

END_MS=$(now_ms)
emit_json "codex" "$TASK" "$STATUS" "$PARSED_OUTPUT" "$TOKENS_USED" "$COST_USD" "$((END_MS - START_MS))" "$EXIT_CODE" "$ERRORS"

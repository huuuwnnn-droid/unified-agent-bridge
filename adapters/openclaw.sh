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
  printf '[openclaw] %s\n' "$*" >&2
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
text = raw.strip()
result = raw
tokens = 0
cost = 0.0

if text:
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            result = data.get("result") or data.get("output") or data.get("text") or data.get("message") or raw
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            if isinstance(data.get("tokens_used"), (int, float)):
                tokens = int(data["tokens_used"])
            elif isinstance(data.get("total_tokens"), (int, float)):
                tokens = int(data["total_tokens"])
            elif isinstance(usage.get("total_tokens"), (int, float)):
                tokens = int(usage["total_tokens"])
            if isinstance(data.get("cost_usd"), (int, float)):
                cost = float(data["cost_usd"])
            elif isinstance(data.get("total_cost_usd"), (int, float)):
                cost = float(data["total_cost_usd"])
    except Exception:
        pass

print(json.dumps({"output": result, "tokens_used": tokens, "cost_usd": cost}, ensure_ascii=False))
PY
}

# --- Validation ---
START_MS=$(now_ms)

if [[ -z "$TASK" ]]; then
  END_MS=$(now_ms)
  emit_json "openclaw" "$TASK" "failed" "" "0" "0.0" "$((END_MS - START_MS))" "2" '["missing task"]'
  exit 0
fi

if [[ ! -d "$WORKDIR" ]]; then
  END_MS=$(now_ms)
  emit_json "openclaw" "$TASK" "failed" "" "0" "0.0" "$((END_MS - START_MS))" "2" '["workdir not found"]'
  exit 0
fi

cd "$WORKDIR"

# --- Command ---
USE_HTTP=0
CMD=()
if command -v openclaw >/dev/null 2>&1; then
  CMD=(openclaw agent --message "$TASK")
  if [[ -n "$MODEL" ]]; then
    CMD+=(--model "$MODEL")
  fi
  if [[ -n "$EXTRA_ARGS" ]]; then
    # shellcheck disable=SC2206
    EXTRA_PARTS=($EXTRA_ARGS)
    CMD+=("${EXTRA_PARTS[@]}")
  fi
else
  USE_HTTP=1
fi

log_debug "mode=$MODE model=${MODEL:-default} workdir=$WORKDIR"
if [[ $USE_HTTP -eq 0 ]]; then
  log_debug "command=${CMD[*]}"
else
  log_debug "openclaw cli not found, trying local gateway"
fi

# --- Execution ---
STDOUT_FILE=$(mktemp)
STDERR_FILE=$(mktemp)
EXIT_CODE=0
set +e
if [[ $USE_HTTP -eq 0 ]]; then
  run_with_timeout 300 "${CMD[@]}" >"$STDOUT_FILE" 2>"$STDERR_FILE"
  EXIT_CODE=$?
else
  if command -v curl >/dev/null 2>&1; then
    REQUEST_BODY=$(TASK="$TASK" MODE="$MODE" MODEL="$MODEL" python3 - <<'PY'
import json
import os
payload = {
    "message": os.environ.get("TASK", ""),
    "mode": os.environ.get("MODE", "execute"),
}
model = os.environ.get("MODEL", "")
if model:
    payload["model"] = model
print(json.dumps(payload, ensure_ascii=False))
PY
)
    run_with_timeout 300 curl -sS -X POST http://localhost:18789 -H 'Content-Type: application/json' -d "$REQUEST_BODY" >"$STDOUT_FILE" 2>"$STDERR_FILE"
    EXIT_CODE=$?
  else
    EXIT_CODE=127
    printf 'tool not installed\n' >"$STDERR_FILE"
    : >"$STDOUT_FILE"
  fi
fi
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
  elif [[ $EXIT_CODE -eq 127 ]]; then
    STATUS="failed"
    ERRORS='["tool not installed"]'
  else
    STATUS="failed"
    ERRORS='["command failed"]'
  fi
fi

END_MS=$(now_ms)
emit_json "openclaw" "$TASK" "$STATUS" "$PARSED_OUTPUT" "$TOKENS_USED" "$COST_USD" "$((END_MS - START_MS))" "$EXIT_CODE" "$ERRORS"

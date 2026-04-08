#!/usr/bin/env bash
set -euo pipefail

# --- Args ---
TASK=${1:-}
MODE=${2:-execute}
MODEL=${3:-""}
WORKDIR=${4:-.}
EXTRA_ARGS=${5:-""}

# --- Helpers ---
log_debug() {
  printf '[claude-code] %s\n' "$*" >&2
}

now_ms() {
  python3 -c 'import time; print(int(time.time() * 1000))'
}

emit_json() {
  python3 - "$@" <<'PY'
import json
import os
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

def pick_number(obj, *keys):
    for key in keys:
        value = obj.get(key)
        if isinstance(value, (int, float)):
            return value
    return None

if text:
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            result = data.get("result") or data.get("output") or data.get("text") or raw
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            tokens = pick_number(data, "tokens_used", "total_tokens") or pick_number(usage, "total_tokens", "input_tokens", "output_tokens") or 0
            cost = pick_number(data, "cost_usd", "total_cost_usd") or 0.0
    except Exception:
        pass

print(json.dumps({"output": result, "tokens_used": tokens, "cost_usd": cost}, ensure_ascii=False))
PY
}

# --- Validation ---
START_MS=$(now_ms)

if [[ -z "$TASK" ]]; then
  END_MS=$(now_ms)
  emit_json "claude-code" "$TASK" "failed" "" "0" "0.0" "$((END_MS - START_MS))" "2" '["missing task"]'
  exit 0
fi

if [[ ! -d "$WORKDIR" ]]; then
  END_MS=$(now_ms)
  emit_json "claude-code" "$TASK" "failed" "" "0" "0.0" "$((END_MS - START_MS))" "2" '["workdir not found"]'
  exit 0
fi

if ! command -v claude >/dev/null 2>&1; then
  END_MS=$(now_ms)
  emit_json "claude-code" "$TASK" "failed" "" "0" "0.0" "$((END_MS - START_MS))" "127" '["tool not installed"]'
  exit 0
fi

cd "$WORKDIR"

# --- Command ---
CMD=(claude -p "$TASK" --output-format json)

case "$MODE" in
  reason)
    CMD+=(--allowedTools "")
    ;;
  execute)
    CMD+=(--permission-mode acceptEdits)
    ;;
  review|readonly)
    CMD+=(--allowedTools "Read,Grep,Glob")
    ;;
  *)
    CMD+=(--permission-mode acceptEdits)
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
timeout 300 "${CMD[@]}" >"$STDOUT_FILE" 2>"$STDERR_FILE"
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
  if [[ "$LOWER_OUTPUT" == *rate* || "$LOWER_OUTPUT" == *limit* || "$LOWER_OUTPUT" == *429* ]]; then
    STATUS="rate_limited"
    ERRORS='["rate limit detected"]'
  else
    STATUS="failed"
    ERRORS='["command failed"]'
  fi
fi

END_MS=$(now_ms)
emit_json "claude-code" "$TASK" "$STATUS" "$PARSED_OUTPUT" "$TOKENS_USED" "$COST_USD" "$((END_MS - START_MS))" "$EXIT_CODE" "$ERRORS"

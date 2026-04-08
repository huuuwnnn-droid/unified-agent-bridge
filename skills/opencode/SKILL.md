---
name: unified-agent-bridge
description: Cross-tool AI orchestration bridge. Dispatch tasks to Claude Code, Codex, OpenCode, and OpenClaw. Automatic rate limit failover, context handoff, and multi-tool workflow orchestration.
argument-hint: "Use /unified-agent-bridge to orchestrate tasks across multiple AI tools"
---

# Unified Agent Bridge

You now have access to a cross-tool orchestration system. You can dispatch tasks to other AI coding tools and collect their results.

Set the bridge root before running commands:

```bash
BRIDGE_HOME="${UNIFIED_BRIDGE_HOME:-$HOME/.local/share/unified-agent-bridge}"
```

If `UNIFIED_BRIDGE_HOME` is set, it takes precedence. Otherwise the bridge defaults to `$HOME/.local/share/unified-agent-bridge`.

Expected structure:

```text
${BRIDGE_HOME}/
├── scripts/
├── adapters/
├── skills/
└── config.json
```

## Available Commands

Run these via Bash tool:

### Detect Available Tools

```bash
BRIDGE_HOME="${UNIFIED_BRIDGE_HOME:-$HOME/.local/share/unified-agent-bridge}"
bash "${BRIDGE_HOME}/scripts/detect.sh"
```

### Dispatch a Task to Another Tool

```bash
BRIDGE_HOME="${UNIFIED_BRIDGE_HOME:-$HOME/.local/share/unified-agent-bridge}"
python3 "${BRIDGE_HOME}/scripts/bridge.py" dispatch --task "Your task here" --mode execute
python3 "${BRIDGE_HOME}/scripts/bridge.py" dispatch --task "Review code" --mode review --tool claude-code
```

### Execute a Multi-Step Workflow

```bash
BRIDGE_HOME="${UNIFIED_BRIDGE_HOME:-$HOME/.local/share/unified-agent-bridge}"
python3 "${BRIDGE_HOME}/scripts/bridge.py" chain --file tasks.json --workdir /project/path
```

### List Sessions from Another Tool

```bash
BRIDGE_HOME="${UNIFIED_BRIDGE_HOME:-$HOME/.local/share/unified-agent-bridge}"
# List recent sessions (default limit: 20)
python3 "${BRIDGE_HOME}/scripts/context-transfer.py" list --tool claude-code
python3 "${BRIDGE_HOME}/scripts/context-transfer.py" list --tool codex --limit 10
python3 "${BRIDGE_HOME}/scripts/context-transfer.py" list --tool opencode
python3 "${BRIDGE_HOME}/scripts/context-transfer.py" list --tool openclaw
```

Output is a JSON object with `sessions` array. Each entry has: `session_id`, `timestamp`, `preview` (first user message), `source` (file path), `size_bytes`.

### Export a Specific Session

```bash
BRIDGE_HOME="${UNIFIED_BRIDGE_HOME:-$HOME/.local/share/unified-agent-bridge}"
# Export latest session (default)
python3 "${BRIDGE_HOME}/scripts/context-transfer.py" export --tool claude-code
# Export a specific session by ID
python3 "${BRIDGE_HOME}/scripts/context-transfer.py" export --tool claude-code --session ses_xxxxx
python3 "${BRIDGE_HOME}/scripts/context-transfer.py" export --tool codex --session rollout-2026-04-03T13-45-11-xxxxx
```

### Context Handoff (when rate limited or switching tools)

```bash
BRIDGE_HOME="${UNIFIED_BRIDGE_HOME:-$HOME/.local/share/unified-agent-bridge}"
python3 "${BRIDGE_HOME}/scripts/context-transfer.py" handoff --from opencode --to claude-code --workdir .
```

### Monitor Quota

```bash
BRIDGE_HOME="${UNIFIED_BRIDGE_HOME:-$HOME/.local/share/unified-agent-bridge}"
python3 "${BRIDGE_HOME}/scripts/quota-monitor.py" status
python3 "${BRIDGE_HOME}/scripts/quota-monitor.py" suggest
```

### Generate Summary Report

```bash
BRIDGE_HOME="${UNIFIED_BRIDGE_HOME:-$HOME/.local/share/unified-agent-bridge}"
python3 "${BRIDGE_HOME}/scripts/summary-collector.py" report --format markdown
```

## Dispatch Modes

- **execute**: Full read/write access, let the tool make changes
- **reason**: Read-only reasoning, no file changes
- **review**: Code review mode, read-only access
- **readonly**: Strict read-only

## Auto-Failover

When a tool hits rate limit, `bridge.py` automatically tries the next available tool in order: `opencode → claude-code → codex → openclaw`.

## When to Use This Skill

- When your current tool is rate limited and you need to continue work
- When you want a second opinion from a different AI tool
- When a task is better suited for a specific tool's strengths
- When orchestrating a complex workflow across multiple tools
- When you need to hand off context from one session to another
- When the user asks to list or browse sessions from another tool (claude-code, codex, opencode, openclaw)
- When the user wants to pick up work from a specific session in another tool

## Important Rules

- Always check tool availability first with `detect.sh`
- Prefer `dispatch` over direct CLI calls for consistent JSON output
- Use `context-transfer.py` for handoff, not manual copy-paste
- Review results from other tools before accepting their changes

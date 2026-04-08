# Unified Agent Bridge

This file is loaded by Claude Code as a system prompt extension. Use it to access the unified cross-tool orchestration bridge and route work across multiple AI coding tools.

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

## Purpose

You can dispatch tasks to Claude Code, Codex, OpenCode, and OpenClaw through a single bridge layer, collect structured results, hand off context, and recover from rate limits with automatic failover.

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

### Context Handoff

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

- **execute**: Full read/write access, let the selected tool make changes
- **reason**: Read-only reasoning with no file changes
- **review**: Code review mode, read-only access
- **readonly**: Strict read-only execution

## Auto-Failover

When rate limited, `bridge.py` automatically tries the next available tool in this order: `opencode → claude-code → codex → openclaw`.

## When to Use This Bridge

- When Claude Code is rate limited and work must continue elsewhere
- When you want a second opinion from a different tool
- When a task fits another tool better
- When you need multi-step orchestration across tools
- When session context must be handed off cleanly

## Operating Rules

- Always run `detect.sh` first to confirm tool availability
- Prefer `bridge.py dispatch` over direct CLI calls for consistent JSON output
- Use `context-transfer.py` for handoff instead of manual copy-paste
- Review results from other tools before accepting their changes
- Treat this file as persistent operating guidance for cross-tool orchestration

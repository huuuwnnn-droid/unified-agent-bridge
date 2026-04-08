# Unified Agent Bridge

This AGENTS.md file defines project conventions for using the unified agent bridge inside Codex. Use it as the standard operating guide for dispatching work across Claude Code, Codex, OpenCode, and OpenClaw.

## Bridge Root

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

Use the bridge to dispatch tasks to other AI coding tools, collect structured outputs, hand off context between tools, and continue execution when one tool is unavailable or rate limited.

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

- **execute**: Full read/write access, let the tool make changes
- **reason**: Read-only reasoning, no file changes
- **review**: Code review mode, read-only access
- **readonly**: Strict read-only

## Auto-Failover

When a tool hits rate limit, `bridge.py` automatically tries the next available tool in order: `opencode → claude-code → codex → openclaw`.

## Recommended Workflow

1. Run `detect.sh` first
2. Use `bridge.py dispatch` for one-off work
3. Use `bridge.py chain` for multi-step orchestration
4. Use `context-transfer.py` when switching tools or sessions
5. Review results before merging any external tool output

## Rules

- Prefer bridge commands over direct adapter or CLI invocation
- Keep outputs structured by using bridge-managed commands
- Use quota monitoring before starting large workflows
- Treat handoff as a first-class workflow, not a manual process

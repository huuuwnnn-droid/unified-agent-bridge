# unified-agent-bridge

Cross-tool AI orchestration bridge for Claude Code, Codex, OpenCode, and OpenClaw

Author: huhu

## What is this?

A bridge that lets AI coding tools call each other.

Two core capabilities:

1. Dispatch tasks to other AI tools and collect results, with automatic rate limit failover.
2. Transfer session context between tools — list sessions, export, import, and hand off work.

## Supported Tools

| Tool | CLI Command | Dispatch | Session List | Context Export |
|------|-------------|----------|--------------|----------------|
| Claude Code | claude | Yes | Yes | Yes |
| Codex (OpenAI) | codex | Yes | Yes | Yes |
| OpenCode | opencode | Yes | Yes | Yes |
| OpenClaw | openclaw | Yes | Yes | Yes |

## Installation

### For OpenCode (as a skill)

```bash
# Clone to standard location
git clone https://github.com/huuuwnnn-droid/unified-agent-bridge.git ~/.local/share/unified-agent-bridge

# Add to OpenCode config (~/.config/opencode/oh-my-openagent.json)
{
  "skills": {
    "sources": [
      { "path": "~/.local/share/unified-agent-bridge/skills/opencode" }
    ]
  }
}

# Restart OpenCode
```

### For Claude Code

```bash
git clone https://github.com/huuuwnnn-droid/unified-agent-bridge.git ~/.local/share/unified-agent-bridge
# Reference the skill in your Claude Code project:
# Add to .claude/commands/ or use directly via bash
```

### For Codex

```bash
git clone https://github.com/huuuwnnn-droid/unified-agent-bridge.git ~/.local/share/unified-agent-bridge
# The AGENTS.md at skills/codex/ provides instructions
```

## Quick Start / Usage Examples

```bash
BRIDGE="$HOME/.local/share/unified-agent-bridge/scripts"

# Detect available tools
python3 "$BRIDGE/bridge.py" detect

# Dispatch a task to claude-code (reason mode, no file changes)
python3 "$BRIDGE/bridge.py" dispatch --task "Explain what this function does" --mode reason --tool claude-code

# Dispatch with automatic tool selection
python3 "$BRIDGE/bridge.py" dispatch --task "Review this code for bugs" --mode review

# List sessions from claude-code
python3 "$BRIDGE/context-transfer.py" list --tool claude-code --limit 10

# List sessions from opencode  
python3 "$BRIDGE/context-transfer.py" list --tool opencode

# Export a specific session's context
python3 "$BRIDGE/context-transfer.py" export --tool claude-code --session ses_xxxxx

# Hand off context from claude-code to opencode
python3 "$BRIDGE/context-transfer.py" handoff --from claude-code --to opencode

# Check quota usage
python3 "$BRIDGE/quota-monitor.py" status
```

### Using in OpenCode (conversational)

After installing as a skill, you can tell the agent:

- "List my claude-code sessions"
- "Export the context from claude-code session ses_xxxxx and continue that work here"  
- "Dispatch this task to claude-code: review src/auth.ts for security issues"
- "Hand off the current context to codex"

### Using in Claude Code

After loading the skill:

- "Use the bridge to dispatch this review task to opencode"
- "Show me my opencode sessions and import the latest one"

## Command Reference

### bridge.py

| Command | Description |
|---------|-------------|
| `detect` | Detect available AI tools and their status |
| `status` | Show bridge status and recent results |
| `dispatch --task TEXT --mode MODE [--tool TOOL] [--model MODEL] [--workdir DIR]` | Dispatch a task |
| `chain --file FILE [--workdir DIR]` | Run a chain of tasks from JSON file |

Dispatch modes: `execute` (read/write), `reason` (read-only reasoning), `review` (code review), `readonly`

### context-transfer.py

| Command | Description |
|---------|-------------|
| `list --tool TOOL [--limit N]` | List sessions for a tool |
| `export --tool TOOL [--session ID] [--workdir DIR]` | Export session context |
| `compress --file FILE [--max-tokens N]` | Compress exported context |
| `import --tool TOOL --summary TEXT [--workdir DIR]` | Import context into a tool |
| `handoff --from TOOL --to TOOL [--workdir DIR]` | Full export → compress → import pipeline |

### quota-monitor.py

| Command | Description |
|---------|-------------|
| `status` | Show dispatch statistics |
| `suggest` | Suggest which tool to use based on quota |

### summary-collector.py

| Command | Description |
|---------|-------------|
| `add --result JSON [--store FILE]` | Add a tool result to summary |
| `report [--format json|markdown] [--store FILE]` | Generate execution summary |

## Rate Limit Failover

When a tool hits rate limit (429/503), bridge.py automatically tries the next available tool. Order: opencode → claude-code → codex → openclaw (configurable in config.json).

## Configuration

The `config.json` file controls behavior:

- `preferred_tools` — tool priority order
- `auto_handoff_on_rate_limit` — enable/disable auto failover
- `context_transfer` — session export settings
- `tools.*` — per-tool configuration (command, session_dir, capabilities)

## Project Structure

```
unified-agent-bridge/
├── config.json              # Unified configuration
├── adapters/                # Tool-specific shell adapters
│   ├── claude-code.sh
│   ├── codex.sh
│   ├── opencode.sh
│   └── openclaw.sh
├── scripts/                 # Core Python scripts
│   ├── bridge.py            # Dispatch engine
│   ├── context-transfer.py  # Session list/export/import/handoff
│   ├── quota-monitor.py     # Usage tracking
│   └── summary-collector.py # Execution summaries
├── skills/                  # Per-tool skill entries
│   ├── opencode/SKILL.md
│   ├── claude-code/bridge.md
│   ├── codex/AGENTS.md
│   └── openclaw/skill-config.yaml
└── templates/
    └── handoff-context.md   # Context handoff template
```

## License

MIT

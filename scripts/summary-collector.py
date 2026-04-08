#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DECISION_PATTERNS = [
    re.compile(r"\bdecided to\b.*", re.IGNORECASE),
    re.compile(r"\bchose\b.*\bbecause\b.*", re.IGNORECASE),
    re.compile(r"\bsolution:\b.*", re.IGNORECASE),
    re.compile(r"\bapproach:\b.*", re.IGNORECASE),
]
RATE_LIMIT_PATTERNS = [
    re.compile(r"\brate limit\b", re.IGNORECASE),
    re.compile(r"\b(?:429|503|529)\b"),
]
HANDOFF_PATTERNS = [
    re.compile(r"\bhandoff\b", re.IGNORECASE),
    re.compile(r"\bhand off\b", re.IGNORECASE),
    re.compile(r"\btransfer(?:red)?\b", re.IGNORECASE),
]


def debug(message: str) -> None:
    print(message, file=sys.stderr)


@dataclass
class StepResult:
    step: int
    tool: str
    task: str
    status: str
    output: str = ""
    files_changed: list[str] = field(default_factory=list)
    tokens_used: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    exit_code: int | None = None
    errors: list[str] = field(default_factory=list)


class SummaryCollector:
    def __init__(self, config_path: str | None = None):
        self.script_dir = Path(__file__).resolve().parent
        self.project_root = self.script_dir.parent
        self.config_path = Path(config_path).expanduser().resolve() if config_path else self.project_root / "config.json"
        self.config = self._load_config()
        self.summary_config = self.config.get("summary", {})
        self.steps: list[StepResult] = []

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            debug(f"Config not found: {self.config_path}")
            return {}

        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            debug(f"Failed to parse config: {exc}")
            return {}

    def add_step(self, result: dict[str, Any], step_number: int | None = None):
        """Add a tool execution result as a step.
        result follows ToolResult schema: {tool, task, status, output, files_changed, tokens_used, cost_usd, duration_ms, exit_code, errors}"""
        step = self._normalize_step(result=result, step_number=step_number)
        self.steps.append(step)
        self.steps.sort(key=lambda item: item.step)

    def _normalize_step(self, result: dict[str, Any], step_number: int | None = None) -> StepResult:
        if not isinstance(result, dict):
            raise ValueError("result must be a dictionary")

        raw_errors = result.get("errors") or []
        if isinstance(raw_errors, str):
            raw_errors = [raw_errors]

        raw_files = result.get("files_changed") or []
        if isinstance(raw_files, str):
            raw_files = [raw_files]

        resolved_step = step_number or result.get("step") or (len(self.steps) + 1)
        return StepResult(
            step=int(resolved_step),
            tool=str(result.get("tool", "unknown")),
            task=str(result.get("task", "")),
            status=str(result.get("status", "unknown")),
            output=str(result.get("output", "") or ""),
            files_changed=[str(path) for path in raw_files],
            tokens_used=int(result.get("tokens_used") or 0),
            cost_usd=float(result.get("cost_usd") or 0.0),
            duration_ms=int(result.get("duration_ms") or 0),
            exit_code=result.get("exit_code"),
            errors=[str(error) for error in raw_errors],
        )

    def generate_summary(self, format="json") -> str:
        """Generate a comprehensive summary.
        JSON format: {
            "total_steps": N,
            "total_duration_ms": N,
            "total_tokens_used": N,
            "total_cost_usd": N.NN,
            "tools_used": ["claude-code", "codex"],
            "tool_chain": [
                {"step": 1, "tool": "claude-code", "task": "...", "status": "success", "duration_ms": N, "cost_usd": N.NN},
                ...
            ],
            "key_decisions": [...],
            "files_changed": [...],
            "errors_encountered": [...],
            "rate_limit_events": [...],
            "handoff_events": [...]
        }

        Markdown format: readable report with sections for Overview, Steps, Files Changed, Decisions, Errors
        """
        normalized = format.lower()
        if normalized in {"md", "markdown"}:
            return self.generate_markdown()
        if normalized != "json":
            raise ValueError(f"Unsupported format: {format}")

        summary = self._build_summary_dict()
        return json.dumps(summary, ensure_ascii=False, indent=2)

    def generate_markdown(self) -> str:
        """Generate a human-readable markdown summary report"""
        summary = self._build_summary_dict()
        lines = [
            "# Execution Summary",
            "",
            "## Overview",
            f"- **Total Steps**: {summary['total_steps']}",
            f"- **Total Duration (ms)**: {summary['total_duration_ms']}",
            f"- **Total Tokens Used**: {summary['total_tokens_used']}",
            f"- **Total Cost (USD)**: {summary['total_cost_usd']:.6f}",
            f"- **Tools Used**: {', '.join(summary['tools_used']) if summary['tools_used'] else 'None'}",
            "",
            "## Steps",
        ]

        if summary["tool_chain"]:
            for item in summary["tool_chain"]:
                lines.extend(
                    [
                        f"### Step {item['step']}: {item['tool']}",
                        f"- **Task**: {item['task'] or 'N/A'}",
                        f"- **Status**: {item['status']}",
                        f"- **Duration (ms)**: {item['duration_ms']}",
                        f"- **Cost (USD)**: {item['cost_usd']:.6f}",
                    ]
                )
                if self.summary_config.get("include_token_usage", True):
                    lines.append(f"- **Tokens Used**: {item['tokens_used']}")
                if item.get("exit_code") is not None:
                    lines.append(f"- **Exit Code**: {item['exit_code']}")
                step_files = item.get("files_changed", [])
                if step_files:
                    lines.append(f"- **Files Changed**: {', '.join(step_files)}")
                if item.get("errors"):
                    lines.append(f"- **Errors**: {' | '.join(item['errors'])}")
                lines.append("")
        else:
            lines.extend(["- No steps recorded.", ""])

        lines.append("## Files Changed")
        if summary["files_changed"]:
            lines.extend([f"- {path}" for path in summary["files_changed"]])
        else:
            lines.append("- None")
        lines.append("")

        lines.append("## Decisions")
        if summary["key_decisions"]:
            lines.extend([f"- {decision}" for decision in summary["key_decisions"]])
        else:
            lines.append("- None")
        lines.append("")

        lines.append("## Errors")
        if summary["errors_encountered"]:
            lines.extend([f"- {error}" for error in summary["errors_encountered"]])
        else:
            lines.append("- None")
        lines.append("")

        lines.append("## Rate Limit Events")
        if summary["rate_limit_events"]:
            lines.extend([f"- {event}" for event in summary["rate_limit_events"]])
        else:
            lines.append("- None")
        lines.append("")

        lines.append("## Handoff Events")
        if summary["handoff_events"]:
            lines.extend([f"- {event}" for event in summary["handoff_events"]])
        else:
            lines.append("- None")

        return "\n".join(lines) + "\n"

    def extract_key_decisions(self, output: str) -> list:
        """Heuristic extraction of key decisions from tool output.
        Look for patterns like:
        - "decided to..."
        - "chose ... because..."
        - "solution: ..."
        - "approach: ..."
        - Lines starting with "✅", "⚠️", "❌"
        - Lines starting with "Key:" or "Decision:"
        Return list of decision strings"""
        decisions: list[str] = []
        seen: set[str] = set()
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            matched = False
            if line.startswith(("✅", "⚠️", "❌")) or re.match(r"^(Key|Decision):", line, re.IGNORECASE):
                matched = True
            else:
                for pattern in DECISION_PATTERNS:
                    if pattern.search(line):
                        matched = True
                        break

            if matched:
                normalized = re.sub(r"\s+", " ", line)
                if normalized not in seen:
                    seen.add(normalized)
                    decisions.append(normalized)
        return decisions

    def save(self, path: str):
        """Save current summary to file (JSON or MD based on extension)"""
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        suffix = target.suffix.lower()
        if suffix == ".md":
            target.write_text(self.generate_markdown(), encoding="utf-8")
            return

        payload = {
            "config_path": str(self.config_path),
            "steps": [asdict(step) for step in self.steps],
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, path: str):
        """Load previous summary from file to continue accumulating"""
        source = Path(path).expanduser()
        if not source.exists():
            return

        payload = json.loads(source.read_text(encoding="utf-8"))
        raw_steps = payload.get("steps", []) if isinstance(payload, dict) else []
        self.steps = [self._normalize_step(item, step_number=item.get("step")) for item in raw_steps]
        self.steps.sort(key=lambda item: item.step)

    def _build_summary_dict(self) -> dict[str, Any]:
        tools_used = self._ordered_unique(step.tool for step in self.steps if step.tool)
        files_changed = self._ordered_unique(
            path
            for step in self.steps
            for path in step.files_changed
            if path
        )
        key_decisions = self._ordered_unique(
            decision
            for step in self.steps
            for decision in self.extract_key_decisions(step.output)
        ) if self.summary_config.get("include_key_decisions", True) else []
        errors_encountered = self._ordered_unique(
            error
            for step in self.steps
            for error in self._collect_errors(step)
        )
        rate_limit_events = self._ordered_unique(
            event
            for step in self.steps
            for event in self._extract_events(step, RATE_LIMIT_PATTERNS, include_errors=True)
        )
        handoff_events = self._ordered_unique(
            event
            for step in self.steps
            for event in self._extract_events(step, HANDOFF_PATTERNS, include_errors=False)
        )

        tool_chain = []
        if self.summary_config.get("include_tool_chain", True):
            for step in self.steps:
                tool_chain.append(
                    {
                        "step": step.step,
                        "tool": step.tool,
                        "task": step.task,
                        "status": step.status,
                        "duration_ms": step.duration_ms if self.summary_config.get("include_duration", True) else 0,
                        "tokens_used": step.tokens_used if self.summary_config.get("include_token_usage", True) else 0,
                        "cost_usd": step.cost_usd,
                        "exit_code": step.exit_code,
                        "files_changed": step.files_changed,
                        "errors": step.errors,
                    }
                )

        return {
            "total_steps": len(self.steps),
            "total_duration_ms": sum(step.duration_ms for step in self.steps) if self.summary_config.get("include_duration", True) else 0,
            "total_tokens_used": sum(step.tokens_used for step in self.steps) if self.summary_config.get("include_token_usage", True) else 0,
            "total_cost_usd": round(sum(step.cost_usd for step in self.steps), 6),
            "tools_used": tools_used,
            "tool_chain": tool_chain,
            "key_decisions": key_decisions,
            "files_changed": files_changed,
            "errors_encountered": errors_encountered,
            "rate_limit_events": rate_limit_events,
            "handoff_events": handoff_events,
        }

    def _collect_errors(self, step: StepResult) -> list[str]:
        errors = list(step.errors)
        if step.status.lower() not in {"success", "ok", "completed"} and step.output:
            first_line = step.output.strip().splitlines()[0]
            if first_line:
                errors.append(f"Step {step.step} ({step.tool}): {first_line}")
        return errors

    def _extract_events(self, step: StepResult, patterns: list[re.Pattern[str]], include_errors: bool) -> list[str]:
        events: list[str] = []
        sources = [line.strip() for line in step.output.splitlines() if line.strip()]
        if include_errors:
            sources.extend(error.strip() for error in step.errors if error.strip())

        for line in sources:
            if any(pattern.search(line) for pattern in patterns):
                events.append(f"Step {step.step} ({step.tool}): {line}")
        return events

    def _ordered_unique(self, values: Any) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            ordered.append(text)
        return ordered


def resolve_store_path(args: argparse.Namespace, collector: SummaryCollector) -> Path:
    if getattr(args, "store", None):
        return Path(args.store).expanduser()
    if getattr(args, "session", None):
        return collector.project_root / "summaries" / f"{args.session}.json"
    return Path(f"/tmp/unified-bridge-summary-{os.getpid()}.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect and report multi-tool execution summaries.")
    parser.add_argument("--config", help="Path to config.json", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a tool result to the current summary store")
    add_parser.add_argument("--result", required=True, help="JSON object matching ToolResult schema")
    add_parser.add_argument("--step-number", type=int, default=None, help="Explicit step number override")
    add_parser.add_argument("--store", default=None, help="Path to the summary state JSON file")
    add_parser.add_argument("--session", default=None, help="Named session stored under ../summaries/{name}.json")

    report_parser = subparsers.add_parser("report", help="Render summary output from the current store")
    report_parser.add_argument("--format", choices=["json", "markdown", "md"], default="json")
    report_parser.add_argument("--output", default=None, help="Optional output file path")
    report_parser.add_argument("--store", default=None, help="Path to the summary state JSON file")
    report_parser.add_argument("--session", default=None, help="Named session stored under ../summaries/{name}.json")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    collector = SummaryCollector(config_path=args.config)
    store_path = resolve_store_path(args, collector)

    if store_path.exists():
        collector.load(str(store_path))

    if args.command == "add":
        try:
            result = json.loads(args.result)
        except json.JSONDecodeError as exc:
            debug(f"Invalid --result JSON: {exc}")
            return 2

        collector.add_step(result=result, step_number=args.step_number)
        collector.save(str(store_path))
        print(
            json.dumps(
                {
                    "status": "ok",
                    "store": str(store_path),
                    "total_steps": len(collector.steps),
                },
                ensure_ascii=False,
            )
        )
        return 0

    if not store_path.exists():
        debug(f"Summary store not found: {store_path}")
        return 1

    rendered = collector.generate_summary(format=args.format)
    if args.output:
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

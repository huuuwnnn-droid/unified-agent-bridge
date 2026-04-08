#!/usr/bin/env python3

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


def debug(message: str) -> None:
    print(f"[quota-monitor] {message}", file=sys.stderr)


def emit_json(payload: Dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


@dataclass
class HistoryRecord:
    tool: str
    status: str
    output: str = ""
    exit_code: int = 0
    cost_usd: float = 0.0
    recorded_at: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RateLimitRisk:
    tool: str
    risk: str
    recent_rate_limits: int
    recommendation: str
    suggested_wait_seconds: int


class QuotaMonitor:
    def __init__(self, config_path: Optional[str] = None):
        self.script_dir = Path(__file__).resolve().parent
        self.project_root = self.script_dir.parent
        self.config_path = Path(config_path).expanduser() if config_path else self.project_root / "config.json"
        self.config = self._load_config()
        self.history_path = self.script_dir / ".quota-monitor-history.json"
        self.history: List[Dict[str, Any]] = self._load_history()

    def _load_config(self) -> Dict[str, Any]:
        debug(f"loading config from {self.config_path}")
        if not self.config_path.exists():
            raise FileNotFoundError(f"config not found: {self.config_path}")
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def _load_history(self) -> List[Dict[str, Any]]:
        if not self.history_path.exists():
            return []
        debug(f"loading history from {self.history_path}")
        try:
            payload = json.loads(self.history_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return payload
        except Exception as exc:
            debug(f"failed to load history: {exc}")
        return []

    def _save_history(self) -> None:
        debug(f"saving history to {self.history_path}")
        self.history_path.write_text(json.dumps(self.history, ensure_ascii=False, indent=2), encoding="utf-8")

    def _iter_recent(self, tool: Optional[str] = None, minutes: int = 5) -> List[Dict[str, Any]]:
        threshold = datetime.now() - timedelta(minutes=minutes)
        recent: List[Dict[str, Any]] = []
        for entry in self.history:
            if tool and entry.get("tool") != tool:
                continue
            timestamp = entry.get("recorded_at")
            if not timestamp:
                continue
            try:
                recorded_at = datetime.fromisoformat(timestamp)
            except ValueError:
                continue
            if recorded_at >= threshold:
                recent.append(entry)
        return recent

    def record_result(self, result: Dict[str, Any]):
        record = {
            **result,
            "recorded_at": datetime.now().isoformat(),
        }
        self.history.append(record)
        self._save_history()

    def check_rate_limit_risk(self, tool: str) -> Dict[str, Any]:
        recent = self._iter_recent(tool=tool, minutes=5)
        rate_limit_count = sum(1 for item in recent if item.get("status") == "rate_limited")
        if rate_limit_count == 0:
            risk = RateLimitRisk(tool=tool, risk="low", recent_rate_limits=0, recommendation="continue", suggested_wait_seconds=0)
        elif rate_limit_count <= 2:
            risk = RateLimitRisk(tool=tool, risk="medium", recent_rate_limits=rate_limit_count, recommendation="continue", suggested_wait_seconds=60)
        else:
            risk = RateLimitRisk(tool=tool, risk="high", recent_rate_limits=rate_limit_count, recommendation="switch", suggested_wait_seconds=300)
        return asdict(risk)

    def suggest_next_tool(self, exclude: Optional[List[str]] = None) -> Optional[str]:
        exclude = exclude or []
        preferred = self.config.get("preferred_tools", [])
        candidates = [tool for tool in preferred if tool not in exclude]
        if not candidates:
            candidates = [tool for tool in self.config.get("tools", {}).keys() if tool not in exclude]
        best_tool: Optional[str] = None
        best_score: Optional[tuple] = None
        for tool in candidates:
            risk = self.check_rate_limit_risk(tool)
            recent = self._iter_recent(tool=tool, minutes=5)
            total_recent = len(recent)
            if risk["risk"] == "high":
                continue
            score = (risk["recent_rate_limits"], total_recent, preferred.index(tool) if tool in preferred else len(preferred))
            if best_score is None or score < best_score:
                best_score = score
                best_tool = tool
        return best_tool

    def get_summary(self) -> Dict[str, Any]:
        by_tool = Counter(entry.get("tool", "unknown") for entry in self.history)
        by_status = Counter(entry.get("status", "unknown") for entry in self.history)
        total_cost = 0.0
        for entry in self.history:
            try:
                total_cost += float(entry.get("cost_usd", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
        return {
            "total_dispatches": len(self.history),
            "by_tool": dict(by_tool),
            "by_status": dict(by_status),
            "rate_limits": by_status.get("rate_limited", 0),
            "costs": {"total_cost_usd": round(total_cost, 6)},
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor tool quota and rate limits")
    parser.add_argument("--config", dest="config_path", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status")

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--tool", required=True)

    suggest_parser = subparsers.add_parser("suggest")
    suggest_parser.add_argument("--exclude", nargs="*", default=[])

    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("--file", help="JSON file containing a ToolResult payload")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        monitor = QuotaMonitor(config_path=args.config_path)
        if args.command == "status":
            emit_json(monitor.get_summary())
            return 0
        if args.command == "check":
            emit_json(monitor.check_rate_limit_risk(args.tool))
            return 0
        if args.command == "suggest":
            emit_json({"suggested_tool": monitor.suggest_next_tool(exclude=args.exclude)})
            return 0
        if args.command == "record":
            if args.file:
                result = json.loads(Path(args.file).expanduser().resolve().read_text(encoding="utf-8"))
            else:
                result = json.load(sys.stdin)
            monitor.record_result(result)
            emit_json({"status": "recorded", "history_size": len(monitor.history)})
            return 0
        raise ValueError(f"unknown command: {args.command}")
    except Exception as exc:
        emit_json({"status": "failed", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

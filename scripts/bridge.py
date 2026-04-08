#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent
CONFIG = SCRIPT_DIR.parent / "config.json"
DETECT = SCRIPT_DIR / "detect.sh"
ADAPTERS = SCRIPT_DIR.parent / "adapters"
MAX_FALLBACK_ATTEMPTS = 3
MAX_HISTORY = 20


@dataclass
class ToolResult:
    tool: str
    task: str
    status: str
    output: str
    files_changed: list
    tokens_used: int
    cost_usd: float
    duration_ms: int
    exit_code: int
    errors: list

    def to_dict(self):
        return asdict(self)


class Bridge:
    def __init__(self, config_path=None):
        self.config_path = Path(config_path) if config_path else CONFIG
        self.config = self._load_config()
        self.detect_result = self._run_detect()
        self.last_results = []
        self.total_cost = 0.0

    def _debug(self, message):
        print("[bridge] " + str(message), file=sys.stderr)

    def _load_config(self):
        with self.config_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _run_detect(self):
        command = [str(DETECT), "--format", "json"]
        self._debug("running detect.sh")
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.stderr:
            sys.stderr.write(completed.stderr)
        if completed.returncode != 0:
            raise RuntimeError("detect.sh failed with exit code " + str(completed.returncode))
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("detect.sh returned invalid JSON") from exc

    def _record_result(self, result):
        self.last_results.append(result)
        self.last_results = self.last_results[-MAX_HISTORY:]
        self.total_cost += float(result.cost_usd or 0.0)

    def _tool_candidates(self, requested_tool=None):
        preferred = list(self.config.get("preferred_tools") or [])
        candidates = []
        if requested_tool:
            candidates.append(requested_tool)
        for tool_name in preferred:
            if tool_name not in candidates:
                candidates.append(tool_name)
        if not requested_tool:
            for tool_name in sorted((self.detect_result.get("tools") or {}).keys()):
                if tool_name not in candidates:
                    candidates.append(tool_name)
        return candidates

    def available_tools(self):
        tools = self.detect_result.get("tools") or {}
        available = []
        for tool_name, info in tools.items():
            if info.get("available") and info.get("authenticated"):
                available.append(tool_name)
        return available

    def _adapter_path(self, tool_name):
        return ADAPTERS / (tool_name + ".sh")

    def _ensure_adapter(self, tool_name):
        adapter = self._adapter_path(tool_name)
        if not adapter.exists():
            raise FileNotFoundError("adapter not found for tool " + tool_name)
        return adapter

    def _result_from_payload(self, payload, fallback_tool, task):
        if not isinstance(payload, dict):
            raise ValueError("adapter output must be a JSON object")
        return ToolResult(
            tool=str(payload.get("tool") or fallback_tool),
            task=str(payload.get("task") or task),
            status=str(payload.get("status") or "failed"),
            output=str(payload.get("output") or ""),
            files_changed=list(payload.get("files_changed") or []),
            tokens_used=int(payload.get("tokens_used") or 0),
            cost_usd=float(payload.get("cost_usd") or 0.0),
            duration_ms=int(payload.get("duration_ms") or 0),
            exit_code=int(payload.get("exit_code") or 0),
            errors=list(payload.get("errors") or []),
        )

    def _call_adapter(self, tool_name, task, mode, model, workdir):
        adapter = self._ensure_adapter(tool_name)
        command = [
            str(adapter),
            task,
            mode or "execute",
            model or "",
            str(Path(workdir).resolve()),
        ]
        self._debug("dispatching via " + tool_name)
        started = time.time()
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.stderr:
            sys.stderr.write(completed.stderr)
        duration_ms = int((time.time() - started) * 1000)
        stdout = completed.stdout.strip()
        if not stdout:
            payload = {
                "tool": tool_name,
                "task": task,
                "status": "failed",
                "output": "",
                "files_changed": [],
                "tokens_used": 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "exit_code": completed.returncode,
                "errors": ["adapter returned empty stdout"],
            }
            return self._result_from_payload(payload, tool_name, task)
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = {
                "tool": tool_name,
                "task": task,
                "status": "failed",
                "output": stdout,
                "files_changed": [],
                "tokens_used": 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "exit_code": completed.returncode,
                "errors": ["adapter returned invalid JSON"],
            }
        result = self._result_from_payload(payload, tool_name, task)
        if result.duration_ms <= 0:
            result.duration_ms = duration_ms
        if result.exit_code == 0 and completed.returncode != 0:
            result.exit_code = completed.returncode
        return result

    def dispatch(self, task, tool=None, mode="execute", model=None, workdir="."):
        if not task:
            raise ValueError("task is required")
        candidates = []
        available = set(self.available_tools())
        for tool_name in self._tool_candidates(tool):
            if tool_name in available and tool_name not in candidates:
                candidates.append(tool_name)
        if not candidates:
            raise RuntimeError("no available authenticated tools")

        auto_handoff = bool(self.config.get("auto_handoff_on_rate_limit", False))
        attempted = []
        aggregated_errors = []
        last_result = None

        for index, tool_name in enumerate(candidates):
            if index > MAX_FALLBACK_ATTEMPTS:
                break
            attempted.append(tool_name)
            result = self._call_adapter(tool_name, task, mode, model, workdir)
            aggregated_errors.extend(result.errors)
            result.errors = aggregated_errors[:]
            self._record_result(result)
            last_result = result
            if result.status != "rate_limited":
                return result
            if not auto_handoff:
                return result
            if index >= MAX_FALLBACK_ATTEMPTS:
                break
            self._debug("rate limited on " + tool_name + ", trying next tool")

        if last_result is None:
            raise RuntimeError("dispatch did not execute any tool")
        if last_result.status == "rate_limited":
            combined = last_result.errors[:]
            combined.append("all fallback attempts exhausted: " + ", ".join(attempted))
            last_result.errors = combined
        return last_result

    def dispatch_chain(self, tasks, workdir="."):
        results = []
        for item in tasks:
            if not isinstance(item, dict):
                raise ValueError("each task entry must be an object")
            result = self.dispatch(
                task=item.get("task"),
                tool=item.get("tool"),
                mode=item.get("mode") or "execute",
                model=item.get("model"),
                workdir=item.get("workdir") or workdir,
            )
            results.append(result)
            if result.status != "success":
                break
        return results

    def get_status(self):
        return {
            "config_path": str(self.config_path),
            "available_tools": self.available_tools(),
            "tool_detection": self.detect_result,
            "last_results": [result.to_dict() for result in self.last_results],
            "total_cost": round(self.total_cost, 6),
        }


def _load_chain_file(path_value):
    chain_path = Path(path_value)
    with chain_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict) and isinstance(data.get("tasks"), list):
        return data.get("tasks")
    if isinstance(data, list):
        return data
    raise ValueError("chain file must be a list or an object with a tasks array")


def build_parser():
    parser = argparse.ArgumentParser(description="Unified agent bridge dispatcher")
    parser.add_argument("--config", default=None, help="Path to config.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    dispatch_parser = subparsers.add_parser("dispatch", help="Dispatch a single task")
    dispatch_parser.add_argument("--task", required=True, help="Task to send to the adapter")
    dispatch_parser.add_argument("--tool", default=None, help="Preferred tool name")
    dispatch_parser.add_argument("--mode", default="execute", help="Dispatch mode")
    dispatch_parser.add_argument("--model", default=None, help="Model override")
    dispatch_parser.add_argument("--workdir", default=".", help="Working directory")

    chain_parser = subparsers.add_parser("chain", help="Dispatch a chain of tasks")
    chain_parser.add_argument("--file", required=True, help="JSON file with tasks")
    chain_parser.add_argument("--workdir", default=".", help="Default working directory")

    subparsers.add_parser("detect", help="Run detect.sh and print JSON")
    subparsers.add_parser("status", help="Show bridge status")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    bridge = Bridge(config_path=args.config)

    if args.command == "dispatch":
        result = bridge.dispatch(
            task=args.task,
            tool=args.tool,
            mode=args.mode,
            model=args.model,
            workdir=args.workdir,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False))
        return 0

    if args.command == "chain":
        tasks = _load_chain_file(args.file)
        results = bridge.dispatch_chain(tasks, workdir=args.workdir)
        print(json.dumps([result.to_dict() for result in results], ensure_ascii=False))
        return 0

    if args.command == "detect":
        print(json.dumps(bridge.detect_result, ensure_ascii=False))
        return 0

    if args.command == "status":
        print(json.dumps(bridge.get_status(), ensure_ascii=False))
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"status": "failed", "errors": [str(exc)]}, ensure_ascii=False))
        sys.exit(1)

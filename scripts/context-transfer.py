#!/usr/bin/env python3

import argparse
from datetime import datetime, timezone
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


def debug(message: str) -> None:
    print(f"[context-transfer] {message}", file=sys.stderr)


def emit_json(payload: Dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


@dataclass
class SessionContext:
    tool: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    files_changed: List[str] = field(default_factory=list)
    todo_state: List[Any] = field(default_factory=list)
    key_decisions: List[str] = field(default_factory=list)
    summary: str = ""
    source: str = ""


@dataclass
class ToolResult:
    tool: str
    status: str
    output: str
    exit_code: int
    errors: List[str] = field(default_factory=list)
    command: List[str] = field(default_factory=list)


class ContextTransfer:
    def __init__(self, config_path: Optional[str] = None):
        self.script_dir = Path(__file__).resolve().parent
        self.project_root = self.script_dir.parent
        self.config_path = Path(config_path).expanduser() if config_path else self.project_root / "config.json"
        self.config = self._load_config()
        self.context_config = self.config.get("context_transfer", {})
        self.summary_config = self.config.get("summary", {})

    def _load_config(self) -> Dict[str, Any]:
        debug(f"loading config from {self.config_path}")
        if not self.config_path.exists():
            raise FileNotFoundError(f"config not found: {self.config_path}")
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def _tool_config(self, tool: str) -> Dict[str, Any]:
        tools = self.config.get("tools", {})
        if tool not in tools:
            raise ValueError(f"unsupported tool: {tool}")
        return tools[tool]

    def _run_command(self, command: List[str], workdir: str = ".", timeout: int = 300) -> ToolResult:
        cwd = Path(workdir).expanduser().resolve()
        debug(f"running command: {' '.join(command)} (cwd={cwd})")
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            return ToolResult(tool=command[0], status="failed", output="", exit_code=127, errors=["tool not installed"], command=command)
        except subprocess.TimeoutExpired:
            return ToolResult(tool=command[0], status="timeout", output="", exit_code=124, errors=["command timed out"], command=command)

        output = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        lowered = f"{output}\n{stderr}".lower()
        status = "success" if completed.returncode == 0 else "failed"
        errors: List[str] = []
        if completed.returncode != 0:
            if any(marker in lowered for marker in ("rate limit", "429", "503")):
                status = "rate_limited"
                errors.append("rate limit detected")
            else:
                errors.append(stderr or "command failed")
        if stderr:
            debug(stderr)
        return ToolResult(tool=command[0], status=status, output=output or stderr, exit_code=completed.returncode, errors=errors, command=command)

    def _latest_session_file(self, session_dir: Path) -> Optional[Path]:
        if not session_dir.exists():
            return None
        candidates: List[Path] = []
        for pattern in ("**/*.jsonl", "**/*.json"):
            candidates.extend(path for path in session_dir.glob(pattern) if path.is_file())
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def _isoformat_timestamp(self, timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _collect_session_files(self, search_dirs: List[Path]) -> List[Path]:
        candidates: List[Path] = []
        for search_dir in search_dirs:
            if not search_dir.exists():
                continue
            for pattern in ("**/*.jsonl", "**/*.json"):
                candidates.extend(path for path in search_dir.glob(pattern) if path.is_file())

        seen = set()
        unique: List[Path] = []
        for path in candidates:
            resolved = str(path.resolve())
            if resolved not in seen:
                seen.add(resolved)
                unique.append(path)
        unique.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return unique

    def _safe_json_loads(self, raw: str) -> Optional[Any]:
        try:
            return json.loads(raw)
        except Exception:
            return None

    def _load_claude_history(self) -> Dict[str, Dict[str, Any]]:
        history_file = Path("~/.claude/history.jsonl").expanduser()
        if not history_file.exists():
            return {}
        index: Dict[str, Dict[str, Any]] = {}
        for line in history_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            parsed = self._safe_json_loads(line)
            if isinstance(parsed, dict) and parsed.get("sessionId"):
                index[str(parsed["sessionId"])] = parsed
        return index

    def _extract_text_from_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        texts.append(str(text))
                elif isinstance(item, str):
                    texts.append(item)
            return " ".join(texts)
        return ""

    def _extract_first_user_message(self, session_file: Path) -> str:
        try:
            with session_file.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    parsed = self._safe_json_loads(line)
                    if not isinstance(parsed, dict):
                        continue

                    # Codex format: {"type": "response_item", "payload": {"role": "user", "content": [...]}}
                    payload = parsed.get("payload")
                    if isinstance(payload, dict) and str(payload.get("role", "")).lower() in ("user", "human"):
                        content = payload.get("content")
                        text = self._extract_text_from_content(content).strip()
                        if text and not text.startswith("<"):
                            return text[:120]
                        continue

                    msg_type = str(parsed.get("type") or parsed.get("role") or "").lower()
                    if msg_type not in ("user", "human"):
                        continue
                    content = parsed.get("content") or parsed.get("text") or parsed.get("message") or parsed.get("display") or ""
                    text = self._extract_text_from_content(content).strip()
                    if text:
                        return text[:120]
        except Exception:
            return ""
        return ""

    @staticmethod
    def _derive_title(preview: str) -> str:
        """Derive a short session title from the first user message.

        Mimics Claude Code's local title derivation (``ge1``): collapse
        whitespace and truncate to 80 characters, appending '…' when
        truncated.
        """
        import re

        if not preview:
            return ""
        collapsed = re.sub(r"\s+", " ", preview).strip()
        if not collapsed:
            return ""
        if len(collapsed) > 80:
            return collapsed[:79] + "…"
        return collapsed

    def _session_search_dirs(self, tool: str) -> List[Path]:
        tool_cfg = self._tool_config(tool)
        search_dirs: List[Path] = []
        session_dir_raw = tool_cfg.get("session_dir", "")
        if session_dir_raw:
            search_dirs.append(Path(session_dir_raw).expanduser())
        if tool == "claude-code":
            transcripts_dir = Path("~/.claude/transcripts").expanduser()
            if transcripts_dir not in search_dirs:
                search_dirs.append(transcripts_dir)
        return search_dirs

    def _find_session_file(self, tool: str, session_id: str) -> Optional[Path]:
        for search_dir in self._session_search_dirs(tool):
            if not search_dir.exists():
                continue
            for ext in (".jsonl", ".json"):
                candidate = search_dir / (session_id + ext)
                if candidate.is_file():
                    return candidate
            for pattern in ("**/{0}.jsonl".format(session_id), "**/{0}.json".format(session_id), "**/*{0}*".format(session_id)):
                for match in search_dir.glob(pattern):
                    if match.is_file():
                        return match
        return None

    def _normalize_message(self, entry: Any) -> List[Dict[str, Any]]:
        if isinstance(entry, dict):
            role = str(entry.get("role") or entry.get("type") or entry.get("speaker") or "unknown")
            content = entry.get("content")
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text") or item.get("content") or item.get("message")
                        if isinstance(text, str) and text.strip():
                            text_parts.append(text.strip())
                    elif isinstance(item, str) and item.strip():
                        text_parts.append(item.strip())
                content = "\n".join(text_parts)
            elif not isinstance(content, str):
                content = entry.get("text") or entry.get("message") or entry.get("output") or ""
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            text = content.strip()
            if text:
                return [{"role": role, "content": text}]
            if isinstance(entry.get("messages"), list):
                messages: List[Dict[str, Any]] = []
                for nested in entry["messages"]:
                    messages.extend(self._normalize_message(nested))
                return messages
        elif isinstance(entry, list):
            messages = []
            for item in entry:
                messages.extend(self._normalize_message(item))
            return messages
        elif isinstance(entry, str) and entry.strip():
            return [{"role": "unknown", "content": entry.strip()}]
        return []

    def _extract_files_changed(self, records: List[Any]) -> List[str]:
        files: List[str] = []
        keys = ("file", "path", "filepath", "file_path", "target_file")
        for record in records:
            if isinstance(record, dict):
                for key in keys:
                    value = record.get(key)
                    if isinstance(value, str) and value.strip():
                        files.append(value.strip())
                tool_name = str(record.get("tool") or record.get("name") or "").lower()
                if tool_name in {"write", "edit", "multiedit", "apply_patch"}:
                    for key in keys:
                        value = record.get("input", {}).get(key) if isinstance(record.get("input"), dict) else None
                        if isinstance(value, str) and value.strip():
                            files.append(value.strip())
                for value in record.values():
                    if isinstance(value, list):
                        files.extend(self._extract_files_changed(value))
                    elif isinstance(value, dict):
                        files.extend(self._extract_files_changed([value]))
        deduped: List[str] = []
        for item in files:
            if item not in deduped:
                deduped.append(item)
        return deduped

    def _extract_todo_state(self, records: List[Any]) -> List[Any]:
        todos: List[Any] = []
        for record in records:
            if isinstance(record, dict):
                for key in ("todo_state", "todos", "todo"):
                    value = record.get(key)
                    if isinstance(value, list):
                        todos.extend(value)
                for value in record.values():
                    if isinstance(value, list):
                        todos.extend(self._extract_todo_state(value))
                    elif isinstance(value, dict):
                        todos.extend(self._extract_todo_state([value]))
        return todos

    def _extract_key_decisions(self, messages: List[Dict[str, Any]]) -> List[str]:
        decisions: List[str] = []
        if not self.context_config.get("include_key_decisions", True):
            return decisions
        markers = ("decision", "decided", "决定", "方案", "结论", "will use", "using ")
        for message in messages:
            text = message.get("content", "")
            lowered = text.lower()
            if any(marker in lowered for marker in markers) or any(marker in text for marker in ("决定", "方案", "结论")):
                decisions.append(text[:240])
        return decisions[:10]

    def _build_summary(self, messages: List[Dict[str, Any]]) -> str:
        if not messages:
            return ""
        assistant_messages = [m["content"] for m in messages if m.get("role", "").lower() in {"assistant", "model"} and m.get("content")]
        source = assistant_messages[-1] if assistant_messages else messages[-1].get("content", "")
        return source[:1000]

    def _parse_session_file(self, session_file: Path, tool: str) -> Dict[str, Any]:
        debug(f"parsing session file {session_file}")
        raw = session_file.read_text(encoding="utf-8", errors="replace")
        records: List[Any] = []
        if session_file.suffix == ".jsonl":
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                parsed = self._safe_json_loads(line)
                records.append(parsed if parsed is not None else {"role": "unknown", "content": line})
        else:
            parsed = self._safe_json_loads(raw)
            if isinstance(parsed, list):
                records.extend(parsed)
            elif isinstance(parsed, dict):
                records.append(parsed)
                if isinstance(parsed.get("messages"), list):
                    records.extend(parsed["messages"])
            else:
                records.append({"role": "unknown", "content": raw})

        messages: List[Dict[str, Any]] = []
        for record in records:
            messages.extend(self._normalize_message(record))

        context = SessionContext(
            tool=tool,
            messages=messages,
            files_changed=self._extract_files_changed(records),
            todo_state=self._extract_todo_state(records) if self.context_config.get("include_todo_state", True) else [],
            key_decisions=self._extract_key_decisions(messages),
            summary=self._build_summary(messages),
            source=str(session_file),
        )
        return asdict(context)

    def _list_file_backed_sessions(self, tool: str, limit: int) -> Dict[str, Any]:
        unique = self._collect_session_files(self._session_search_dirs(tool))
        history_index = self._load_claude_history() if tool == "claude-code" else {}
        sessions: List[Dict[str, Any]] = []
        for path in unique[:limit]:
            stat = path.stat()
            session_id = path.stem
            preview = self._extract_first_user_message(path)
            entry: Dict[str, Any] = {
                "session_id": session_id,
                "title": self._derive_title(preview),
                "timestamp": self._isoformat_timestamp(stat.st_mtime),
                "preview": preview,
                "source": str(path),
                "size_bytes": stat.st_size,
            }
            history_entry = history_index.get(session_id)
            if history_entry:
                project = history_entry.get("project")
                if project:
                    entry["project"] = str(project)
                if not entry["preview"] and history_entry.get("display"):
                    entry["preview"] = str(history_entry["display"])[:120]
                    if not entry["title"]:
                        entry["title"] = self._derive_title(entry["preview"])
            sessions.append(entry)
        return {"tool": tool, "sessions": sessions, "total": len(unique)}

    def _list_file_backed_sessions_generic(self, tool: str, session_dir: str, limit: int) -> Dict[str, Any]:
        unique = self._collect_session_files([Path(session_dir).expanduser()])
        sessions: List[Dict[str, Any]] = []
        for path in unique[:limit]:
            stat = path.stat()
            preview = self._extract_first_user_message(path)
            sessions.append(
                {
                    "session_id": path.stem,
                    "title": self._derive_title(preview),
                    "timestamp": self._isoformat_timestamp(stat.st_mtime),
                    "preview": preview,
                    "source": str(path),
                    "size_bytes": stat.st_size,
                }
            )
        return {"tool": tool, "sessions": sessions, "total": len(unique)}

    def _parse_command_session_items(self, tool: str, items: List[Any], limit: int) -> Dict[str, Any]:
        sessions: List[Dict[str, Any]] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            preview = str(item.get("title") or item.get("name") or item.get("preview") or item.get("display") or "")[:120]
            sessions.append(
                {
                    "session_id": str(item.get("id") or item.get("session_id") or item.get("sessionId") or ""),
                    "title": self._derive_title(
                        str(item.get("title") or item.get("name") or "")
                    ) or self._derive_title(preview),
                    "timestamp": str(item.get("updated") or item.get("timestamp") or item.get("created") or ""),
                    "project": str(item.get("project") or item.get("path") or item.get("cwd") or ""),
                    "preview": preview,
                    "source": "command",
                }
            )
        sessions.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
        return {"tool": tool, "sessions": sessions, "total": len(items)}

    def _list_command_backed_sessions(self, tool: str, limit: int) -> Dict[str, Any]:
        if tool == "opencode":
            result = self._run_command(["opencode", "session", "list"], timeout=30)
            if result.status == "success" and result.output:
                parsed = self._safe_json_loads(result.output)
                if isinstance(parsed, list):
                    return self._parse_command_session_items(tool, parsed, limit)
                if isinstance(parsed, dict) and isinstance(parsed.get("sessions"), list):
                    return self._parse_command_session_items(tool, parsed["sessions"], limit)
            fallback = self._list_file_backed_sessions_generic(tool, "~/.local/share/opencode/sessions/", limit)
            if fallback.get("total"):
                return fallback
            return self._list_file_backed_sessions_generic(tool, "~/.local/share/opencode/snapshot/", limit)

        if tool == "openclaw":
            result = self._run_command(["openclaw", "sessions"], timeout=30)
            if result.status == "success" and result.output:
                parsed = self._safe_json_loads(result.output)
                if isinstance(parsed, list):
                    return self._parse_command_session_items(tool, parsed, limit)
                if isinstance(parsed, dict) and isinstance(parsed.get("sessions"), list):
                    return self._parse_command_session_items(tool, parsed["sessions"], limit)
            tool_cfg = self._tool_config(tool)
            session_dir = tool_cfg.get("session_dir", "")
            if session_dir:
                fallback = self._list_file_backed_sessions_generic(tool, session_dir, limit)
                if fallback.get("total"):
                    return fallback
            return {"tool": tool, "sessions": [], "total": 0, "note": "openclaw not available or no sessions found"}

        raise ValueError(f"unsupported tool for list: {tool}")

    def _export_file_backed_context(self, tool: str, session: Optional[str] = None) -> Dict[str, Any]:
        session_file: Optional[Path]
        if session:
            session_file = self._find_session_file(tool, session)
            if session_file is None:
                raise ValueError(f"session not found: {session}")
        else:
            search_dirs = self._session_search_dirs(tool)
            candidates = self._collect_session_files(search_dirs)
            session_file = candidates[0] if candidates else None
        if session_file is None:
            source = str(self._session_search_dirs(tool)[0]) if self._session_search_dirs(tool) else ""
            return asdict(SessionContext(tool=tool, summary="", source=source))
        return self._parse_session_file(session_file, tool)

    def _export_command_context(self, tool: str, workdir: str, session: Optional[str] = None) -> Dict[str, Any]:
        if tool == "opencode":
            if session:
                result = self._run_command(["opencode", "export", session], workdir=workdir)
                if result.status != "success":
                    found = self._find_session_file(tool, session)
                    if found is not None:
                        return self._parse_session_file(found, tool)
            else:
                result = self._run_command(["opencode", "export", "--format", "json"], workdir=workdir)
        elif tool == "openclaw":
            if session:
                result = self._run_command(["openclaw", "session", "export", session], workdir=workdir)
                if result.status != "success":
                    found = self._find_session_file(tool, session)
                    if found is not None:
                        return self._parse_session_file(found, tool)
            else:
                result = self._run_command(["openclaw", "sessions"], workdir=workdir)
        else:
            raise ValueError(f"unsupported command export tool: {tool}")

        parsed = self._safe_json_loads(result.output)
        if isinstance(parsed, dict):
            context = SessionContext(
                tool=tool,
                messages=self._normalize_message(parsed.get("messages", parsed)),
                files_changed=parsed.get("files_changed", []) if isinstance(parsed.get("files_changed"), list) else [],
                todo_state=parsed.get("todo_state", []) if isinstance(parsed.get("todo_state"), list) else [],
                key_decisions=parsed.get("key_decisions", []) if isinstance(parsed.get("key_decisions"), list) else [],
                summary=str(parsed.get("summary") or result.output[:1000]),
                source="command",
            )
            return asdict(context)
        return asdict(
            SessionContext(
                tool=tool,
                messages=[{"role": "system", "content": result.output}] if result.output else [],
                summary=result.output[:1000],
                source="command",
            )
        )

    def list_sessions(self, tool: str, limit: int = 20) -> Dict[str, Any]:
        if tool in {"claude-code", "codex"}:
            return self._list_file_backed_sessions(tool, limit)
        if tool in {"opencode", "openclaw"}:
            return self._list_command_backed_sessions(tool, limit)
        raise ValueError(f"unsupported tool: {tool}")

    def export_context(self, tool: str, workdir: str = ".", session: Optional[str] = None) -> Dict[str, Any]:
        if tool in {"claude-code", "codex"}:
            return self._export_file_backed_context(tool, session=session)
        if tool in {"opencode", "openclaw"}:
            return self._export_command_context(tool, workdir, session=session)
        raise ValueError(f"unsupported tool: {tool}")

    def compress_context(self, context: Dict[str, Any], max_tokens: int = 4000) -> str:
        max_chars = max_tokens * 4
        lines: List[str] = []
        tool_name = context.get("tool", "unknown")
        summary = str(context.get("summary") or "").strip()
        files_changed = context.get("files_changed") if isinstance(context.get("files_changed"), list) else []
        key_decisions = context.get("key_decisions") if isinstance(context.get("key_decisions"), list) else []
        todo_state = context.get("todo_state") if isinstance(context.get("todo_state"), list) else []
        messages = context.get("messages") if isinstance(context.get("messages"), list) else []
        recent_count = int(self.context_config.get("include_recent_messages", 5) or 5)

        lines.append(f"Tool: {tool_name}")
        if summary:
            lines.append("Summary:")
            lines.append(summary)

        if key_decisions:
            lines.append("Key decisions:")
            for decision in key_decisions[:10]:
                lines.append(f"- {str(decision).strip()}")

        if self.context_config.get("include_files_changed", True) and files_changed:
            lines.append("Files changed:")
            for file_path in files_changed[:50]:
                lines.append(f"- {file_path}")

        if self.context_config.get("include_todo_state", True) and todo_state:
            lines.append("TODO state:")
            for item in todo_state[:20]:
                if isinstance(item, dict):
                    status = item.get("status", "unknown")
                    content = item.get("content") or item.get("title") or json.dumps(item, ensure_ascii=False)
                    lines.append(f"- [{status}] {content}")
                else:
                    lines.append(f"- {item}")

        if messages:
            lines.append("Recent messages:")
            for message in messages[-recent_count:]:
                role = message.get("role", "unknown")
                content = str(message.get("content", "")).strip().replace("\n", " ")
                if content:
                    lines.append(f"- {role}: {content[:400]}")

        compressed = "\n".join(lines).strip()
        if len(compressed) <= max_chars:
            return compressed
        truncated = compressed[: max_chars - 32].rstrip()
        return f"{truncated}\n[truncated to fit token budget]"

    def import_context(self, tool: str, summary: str, workdir: str = ".") -> Dict[str, Any]:
        prompts = {
            "claude-code": ["claude", "-p", f"Context from previous session: {summary}. Continue working."],
            "codex": ["codex", "exec", f"Context: {summary}. Continue."],
            "opencode": ["opencode", "run", f"Context: {summary}. Continue."],
            "openclaw": ["openclaw", "agent", "--message", f"Context: {summary}. Continue."],
        }
        if tool not in prompts:
            raise ValueError(f"unsupported tool: {tool}")
        result = self._run_command(prompts[tool], workdir=workdir)
        return asdict(result)

    def handoff(self, from_tool: str, to_tool: str, workdir: str = ".") -> Dict[str, Any]:
        exported = self.export_context(from_tool, workdir=workdir)
        compressed = self.compress_context(exported, max_tokens=int(self.context_config.get("max_summary_tokens", 4000) or 4000))
        imported = self.import_context(to_tool, compressed, workdir=workdir)
        return {
            "from_tool": from_tool,
            "to_tool": to_tool,
            "exported_context": exported,
            "compressed_summary": compressed,
            "import_result": imported,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Transfer context between agent tools")
    parser.add_argument("--config", dest="config_path", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--tool", required=True)
    export_parser.add_argument("--workdir", default=".")
    export_parser.add_argument("--session", default=None)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--tool", required=True)
    list_parser.add_argument("--limit", type=int, default=20)

    compress_parser = subparsers.add_parser("compress")
    compress_parser.add_argument("--file", required=True)
    compress_parser.add_argument("--max-tokens", type=int, default=None)

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--tool", required=True)
    import_parser.add_argument("--summary", required=True)
    import_parser.add_argument("--workdir", default=".")

    handoff_parser = subparsers.add_parser("handoff")
    handoff_parser.add_argument("--from", dest="from_tool", required=True)
    handoff_parser.add_argument("--to", dest="to_tool", required=True)
    handoff_parser.add_argument("--workdir", default=".")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        transfer = ContextTransfer(config_path=args.config_path)
        if args.command == "list":
            emit_json(transfer.list_sessions(args.tool, limit=args.limit))
            return 0
        if args.command == "export":
            emit_json(transfer.export_context(args.tool, workdir=args.workdir, session=args.session))
            return 0
        if args.command == "compress":
            context_path = Path(args.file).expanduser().resolve()
            debug(f"compressing context file {context_path}")
            context = json.loads(context_path.read_text(encoding="utf-8"))
            max_tokens = args.max_tokens or int(transfer.context_config.get("max_summary_tokens", 4000) or 4000)
            emit_json({"summary": transfer.compress_context(context, max_tokens=max_tokens)})
            return 0
        if args.command == "import":
            emit_json(transfer.import_context(args.tool, args.summary, workdir=args.workdir))
            return 0
        if args.command == "handoff":
            emit_json(transfer.handoff(args.from_tool, args.to_tool, workdir=args.workdir))
            return 0
        raise ValueError(f"unknown command: {args.command}")
    except Exception as exc:
        emit_json({"status": "failed", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

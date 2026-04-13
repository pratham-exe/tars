"""Session scanning — discovers and enriches Claude Code sessions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tars.scanner.models import HistoricalSession, Session
from tars.scanner.tmux import build_tmux_pane_map, find_tmux_pane
from tars.scanner.transcripts import aggregate_token_usage, parse_transcript_summary
from tars.scanner.utils import (
    CLAUDE_DIR,
    PROJECTS_DIR,
    SESSIONS_DIR,
    extract_project_name,
    find_transcript,
    is_pid_alive,
    is_session_active,
    read_last_n_lines,
    ts_to_dt,
)


def scan_sessions(active_only: bool = True) -> list[Session]:
    """Scan for Claude Code sessions."""
    sessions: list[Session] = []
    if not SESSIONS_DIR.exists():
        return sessions

    pane_map = build_tmux_pane_map()
    now = datetime.now(timezone.utc)

    for session_file in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(session_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        pid = data.get("pid", 0)
        alive = is_pid_alive(pid)
        if active_only and not alive:
            continue

        session_id = data.get("sessionId", "")
        started_at = ts_to_dt(data.get("startedAt", 0))
        cwd = data.get("cwd", "")
        transcript_file = find_transcript(session_id, cwd)
        duration = int((now - started_at).total_seconds())

        session = Session(
            pid=pid,
            session_id=session_id,
            cwd=cwd,
            started_at=started_at,
            kind=data.get("kind", "interactive"),
            entrypoint=data.get("entrypoint", "cli"),
            name=data.get("name", ""),
            is_alive=alive,
            is_recently_active=is_session_active(pid, transcript_file, session_file),
            project_name=extract_project_name(cwd),
            transcript_path=transcript_file,
            duration_secs=max(0, duration),
        )

        session.tmux_pane = find_tmux_pane(pid, pane_map)

        transcript_info = parse_transcript_summary(transcript_file)
        session.last_activity = transcript_info["last_activity"]
        session.last_activity_time = transcript_info["last_activity_time"]
        session.tool_count = transcript_info["tool_count"]
        session.recent_tools = transcript_info["recent_tools"]
        session.message_count = transcript_info["message_count"]

        token_info = aggregate_token_usage(transcript_file)
        session.total_input_tokens = token_info["input_tokens"]
        session.total_output_tokens = token_info["output_tokens"]
        session.total_cache_read_tokens = token_info["cache_read_tokens"]
        session.total_cache_create_tokens = token_info["cache_create_tokens"]

        sessions.append(session)

    sessions.sort(key=lambda s: s.started_at, reverse=True)
    return sessions


def cleanup_dead_sessions() -> list[Path]:
    """Find session files whose PIDs are dead."""
    stale: list[Path] = []
    if not SESSIONS_DIR.exists():
        return stale
    for session_file in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(session_file.read_text())
            if not is_pid_alive(data.get("pid", 0)):
                stale.append(session_file)
        except (json.JSONDecodeError, OSError):
            stale.append(session_file)
    return stale


def remove_dead_sessions() -> int:
    """Remove session files for dead processes."""
    stale = cleanup_dead_sessions()
    for f in stale:
        try:
            f.unlink()
        except OSError:
            pass
    return len(stale)


def get_session_history(session_id: str, limit: int = 30) -> list[dict]:
    """Read history entries for a specific session from history.jsonl."""
    history_file = CLAUDE_DIR / "history.jsonl"
    if not history_file.exists():
        return []
    lines = read_last_n_lines(history_file, 500)
    entries = []
    for line in lines:
        try:
            entry = json.loads(line.strip())
            if entry.get("sessionId") == session_id:
                entries.append(entry)
        except json.JSONDecodeError:
            continue
    return entries[-limit:]


def list_resumable_sessions(limit: int = 30) -> list[HistoricalSession]:
    """List historical sessions that can be resumed, sorted by most recent."""
    if not PROJECTS_DIR.exists():
        return []

    results: list[HistoricalSession] = []
    active_ids: set[str] = set()

    if SESSIONS_DIR.exists():
        for sf in SESSIONS_DIR.glob("*.json"):
            try:
                data = json.loads(sf.read_text())
                sid = data.get("sessionId", "")
                if sid and is_pid_alive(data.get("pid", 0)):
                    active_ids.add(sid)
            except (json.JSONDecodeError, OSError):
                pass

    # Build a name lookup from session files
    session_names: dict[str, str] = {}
    if SESSIONS_DIR.exists():
        for sf in SESSIONS_DIR.glob("*.json"):
            try:
                sd = json.loads(sf.read_text())
                sid = sd.get("sessionId", "")
                name = sd.get("name", "")
                if sid and name:
                    session_names[sid] = name
            except (json.JSONDecodeError, OSError):
                pass

    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for f in project_dir.glob("*.jsonl"):
            uuid = f.stem
            if "-" not in uuid:
                continue
            try:
                stat = f.stat()
                lines = 0
                first_prompt = ""

                transcript_name = ""

                # Read last 30 lines to find the most recent agent-name
                tail_lines = read_last_n_lines(f, 30)
                for tl in reversed(tail_lines):
                    try:
                        te = json.loads(tl)
                        if te.get("type") == "agent-name":
                            transcript_name = te.get("agentName", "")
                            break
                    except json.JSONDecodeError:
                        pass

                with open(f, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        lines += 1
                        try:
                            entry = json.loads(line)
                            # Also check early lines for agent-name
                            if not transcript_name and entry.get("type") == "agent-name":
                                transcript_name = entry.get("agentName", "")
                            if not first_prompt and entry.get("type") == "user":
                                msg = entry.get("message", {})
                                content = msg.get("content", "")
                                if isinstance(content, str) and content:
                                    first_prompt = content[:80]
                                elif isinstance(content, list):
                                    for b in content:
                                        if isinstance(b, dict) and b.get("type") == "text":
                                            first_prompt = b.get("text", "")[:80]
                                            break
                            if lines > 30 and first_prompt:
                                lines += sum(1 for _ in fh)
                                break
                        except json.JSONDecodeError:
                            pass

                if lines < 3:
                    continue

                # Prefer: session file name > transcript name > empty
                name = session_names.get(uuid, "") or transcript_name

                from datetime import timezone
                results.append(HistoricalSession(
                    session_id=uuid,
                    name=name,
                    first_prompt=first_prompt,
                    line_count=lines,
                    modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    is_alive=uuid in active_ids,
                ))
            except OSError:
                continue

    results.sort(key=lambda s: s.modified, reverse=True)
    return results[:limit]

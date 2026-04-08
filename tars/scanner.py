"""Scans ~/.claude/ for active sessions, transcripts, and activity."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import psutil


CLAUDE_DIR = Path.home() / ".claude"
SESSIONS_DIR = CLAUDE_DIR / "sessions"
TRANSCRIPTS_DIR = CLAUDE_DIR / "transcripts"
PROJECTS_DIR = CLAUDE_DIR / "projects"

# A session is "recently active" if its transcript was modified within this many seconds
ACTIVE_THRESHOLD_SECS = 30


@dataclass
class ToolActivity:
    tool_name: str
    timestamp: str
    description: str = ""


@dataclass
class TranscriptEntry:
    """A single parsed line from a transcript JSONL."""
    entry_type: str  # user, assistant, tool_use, tool_result
    timestamp: str = ""
    content: str = ""
    tool_name: str = ""
    tool_desc: str = ""

    @property
    def dt(self) -> datetime | None:
        if not self.timestamp:
            return None
        return _iso_to_dt(self.timestamp)

    @property
    def display(self) -> str:
        if self.entry_type == "user":
            return f"[#ebdbb2]> {self.content}[/#ebdbb2]"
        elif self.entry_type == "assistant":
            return f"[#b8bb26]{self.content}[/#b8bb26]"
        elif self.entry_type == "tool_use":
            return f"[#fe8019]  ⚡ {self.tool_name}[/#fe8019] [dim]{self.tool_desc}[/dim]"
        elif self.entry_type == "tool_result":
            return f"[#83a598]  ← {self.tool_name}[/#83a598] [dim]{self.content}[/dim]"
        return f"[dim]{self.entry_type}: {self.content}[/dim]"


@dataclass
class Session:
    pid: int
    session_id: str
    cwd: str
    started_at: datetime
    kind: str = "interactive"
    entrypoint: str = "cli"
    name: str = ""
    is_alive: bool = False
    is_recently_active: bool = False
    last_activity: str = ""
    last_activity_time: datetime | None = None
    tool_count: int = 0
    recent_tools: list[ToolActivity] = field(default_factory=list)
    message_count: int = 0
    project_name: str = ""
    tmux_pane: str = ""
    transcript_path: Path | None = None
    duration_secs: int = 0

    @property
    def duration_display(self) -> str:
        s = self.duration_secs
        if s < 60:
            return f"{s}s"
        m = s // 60
        if m < 60:
            return f"{m}m"
        h = m // 60
        remaining_m = m % 60
        if h < 24:
            return f"{h}h {remaining_m}m"
        d = h // 24
        remaining_h = h % 24
        return f"{d}d {remaining_h}h"


def _ts_to_dt(ms_timestamp: int | float) -> datetime:
    return datetime.fromtimestamp(ms_timestamp / 1000, tz=timezone.utc)


def _iso_to_dt(iso_str: str) -> datetime:
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc)


def _is_pid_alive(pid: int) -> bool:
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _extract_project_name(cwd: str) -> str:
    return Path(cwd).name if cwd else "unknown"


def _build_tmux_pane_map() -> dict[int, str]:
    """Build a map of pane_pid -> 'session:window' from tmux."""
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_pid} #{session_name}:#{window_index}"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            return {}
        pane_map: dict[int, str] = {}
        for line in result.stdout.strip().splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2:
                try:
                    pane_map[int(parts[0])] = parts[1]
                except ValueError:
                    continue
        return pane_map
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}


def _find_tmux_pane(pid: int, pane_map: dict[int, str]) -> str:
    """Walk up the process tree to find which tmux pane owns this PID."""
    # Check if the PID itself is a pane (happens when zsh exec's into claude)
    if pid in pane_map:
        return pane_map[pid]
    try:
        proc = psutil.Process(pid)
        for _ in range(5):
            parent = proc.parent()
            if parent is None:
                break
            if parent.pid in pane_map:
                return pane_map[parent.pid]
            proc = parent
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return ""


def _read_last_n_lines(filepath: Path, n: int = 50) -> list[str]:
    """Read last n lines of a file efficiently."""
    try:
        with open(filepath, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            if file_size == 0:
                return []
            buf_size = min(file_size, n * 2048)
            f.seek(max(0, file_size - buf_size))
            data = f.read().decode("utf-8", errors="replace")
            lines = data.strip().splitlines()
            return lines[-n:]
    except (OSError, UnicodeDecodeError):
        return []


def _find_transcript(session_id: str, cwd: str = "") -> Path | None:
    """Find the transcript JSONL file for a session.
    Checks project directory first (UUID.jsonl), then transcripts dir."""
    # Project-level transcripts: ~/.claude/projects/{encoded-cwd}/{session-uuid}.jsonl
    if cwd and PROJECTS_DIR.exists():
        encoded_cwd = cwd.replace("/", "-")
        project_dir = PROJECTS_DIR / encoded_cwd
        if project_dir.exists():
            candidate = project_dir / f"{session_id}.jsonl"
            if candidate.exists():
                return candidate

    # Fallback: search all project dirs
    if PROJECTS_DIR.exists():
        for project_dir in PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            candidate = project_dir / f"{session_id}.jsonl"
            if candidate.exists():
                return candidate

    # Legacy: transcripts dir
    if TRANSCRIPTS_DIR.exists():
        for pattern in [f"ses_{session_id}*", f"*{session_id[:8]}*"]:
            matches = list(TRANSCRIPTS_DIR.glob(pattern))
            if matches:
                return matches[0]
    return None


def _is_file_recently_modified(filepath: Path, threshold_secs: int = ACTIVE_THRESHOLD_SECS) -> bool:
    """Check if a file was modified within the last N seconds."""
    try:
        mtime = filepath.stat().st_mtime
        return (time.time() - mtime) < threshold_secs
    except OSError:
        return False


def _is_session_active(pid: int, transcript_file: Path | None, session_file: Path | None) -> bool:
    """Determine if a session is actively doing work. Checks multiple signals."""
    # Check transcript file modification
    if transcript_file and _is_file_recently_modified(transcript_file):
        return True

    # Check session JSON file modification
    if session_file and _is_file_recently_modified(session_file):
        return True

    # Check if the process is using CPU (actively working vs idle waiting for input)
    try:
        proc = psutil.Process(pid)
        # Get CPU usage over a very short interval
        cpu = proc.cpu_percent(interval=0.1)
        if cpu > 1.0:  # More than 1% CPU = actively doing something
            return True
        # Also check children (Claude spawns subprocesses for tools)
        for child in proc.children(recursive=True):
            try:
                child_cpu = child.cpu_percent(interval=0)
                if child_cpu > 1.0:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    return False


def _extract_content_text(entry: dict) -> str:
    """Extract text content from a user or assistant entry (handles both formats)."""
    # Project format: message.content
    msg = entry.get("message", {})
    content = msg.get("content", "") if msg else entry.get("content", "")

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
    return ""


def _extract_tool_uses(entry: dict) -> list[tuple[str, str]]:
    """Extract tool_use blocks from an assistant message content array."""
    msg = entry.get("message", {})
    content = msg.get("content", []) if msg else []
    tools = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name", "unknown")
                inp = block.get("input", {})
                desc = ""
                if isinstance(inp, dict):
                    desc = inp.get("description", "") or inp.get("command", "") or inp.get("prompt", "") or ""
                tools.append((name, desc))
    return tools


def _get_timestamp(entry: dict) -> str:
    """Get timestamp from entry — check both top-level and message-level."""
    ts = entry.get("timestamp", "")
    if not ts:
        msg = entry.get("message", {})
        if msg:
            ts = msg.get("timestamp", "")
    return ts


def _parse_transcript(transcript_file: Path | None) -> dict:
    """Parse transcript for a session (handles both legacy and project formats)."""
    info = {
        "last_activity": "",
        "last_activity_time": None,
        "tool_count": 0,
        "recent_tools": [],
        "message_count": 0,
    }

    if not transcript_file or not transcript_file.exists():
        return info

    lines = _read_last_n_lines(transcript_file, 100)
    tools: list[ToolActivity] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        entry_type = entry.get("type", "")
        timestamp = _get_timestamp(entry)

        if entry_type == "user":
            info["message_count"] += 1
            text = _extract_content_text(entry)
            if text:
                info["last_activity"] = text[:120]
            if timestamp:
                info["last_activity_time"] = _iso_to_dt(timestamp)

        elif entry_type == "assistant":
            # Check for tool_use blocks inside assistant content
            tool_uses = _extract_tool_uses(entry)
            for tool_name, desc in tool_uses:
                tools.append(ToolActivity(tool_name=tool_name, timestamp=timestamp, description=desc))
                info["tool_count"] += 1
                info["last_activity"] = f"⚡ {tool_name}: {desc}"

            if timestamp:
                info["last_activity_time"] = _iso_to_dt(timestamp)

        elif entry_type == "tool_use":
            # Legacy format: top-level tool_use entries
            tool_name = entry.get("tool_name", "unknown")
            desc = ""
            tool_input = entry.get("tool_input", {})
            if isinstance(tool_input, dict):
                desc = tool_input.get("description", "") or tool_input.get("command", "") or ""
                desc = desc
            tools.append(ToolActivity(tool_name=tool_name, timestamp=timestamp, description=desc))
            info["tool_count"] += 1
            info["last_activity"] = f"⚡ {tool_name}: {desc}"
            if timestamp:
                info["last_activity_time"] = _iso_to_dt(timestamp)

    info["recent_tools"] = tools[-5:]
    return info


def _parse_raw_to_entries(raw: dict) -> list[TranscriptEntry]:
    """Parse a single JSONL line into one or more TranscriptEntries.
    Handles both legacy format and project format."""
    entries: list[TranscriptEntry] = []
    entry_type = raw.get("type", "")
    timestamp = _get_timestamp(raw)

    if entry_type in ("agent-color", "permission-mode", "summary"):
        return entries  # skip metadata

    if entry_type == "user":
        text = _extract_content_text(raw)
        if text:
            entries.append(TranscriptEntry(
                entry_type="user", timestamp=timestamp, content=text,
            ))

    elif entry_type == "assistant":
        msg = raw.get("message", {})
        content = msg.get("content", []) if msg else raw.get("content", [])

        # Extract text and tool_use blocks
        text_parts = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        name = block.get("name", "unknown")
                        inp = block.get("input", {})
                        desc = ""
                        if isinstance(inp, dict):
                            desc = inp.get("description", "") or inp.get("command", "") or inp.get("prompt", "") or ""
                        entries.append(TranscriptEntry(
                            entry_type="tool_use", timestamp=timestamp,
                            tool_name=name, tool_desc=desc,
                        ))
                    elif block.get("type") == "tool_result":
                        content_val = block.get("content", "")
                        if isinstance(content_val, list):
                            content_val = " ".join(
                                b.get("text", "") for b in content_val
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        entries.append(TranscriptEntry(
                            entry_type="tool_result", timestamp=timestamp,
                            tool_name=block.get("tool_use_id", ""),
                            content=str(content_val),
                        ))
        elif isinstance(content, str):
            text_parts.append(content)

        combined = " ".join(text_parts).strip()
        if combined:
            entries.append(TranscriptEntry(
                entry_type="assistant", timestamp=timestamp,
                content=combined,
            ))

    elif entry_type == "tool_use":
        # Legacy format
        tool_input = raw.get("tool_input", {})
        desc = ""
        if isinstance(tool_input, dict):
            desc = tool_input.get("description", "") or tool_input.get("command", "") or ""
        entries.append(TranscriptEntry(
            entry_type="tool_use", timestamp=timestamp,
            tool_name=raw.get("tool_name", "unknown"), tool_desc=desc,
        ))

    elif entry_type == "tool_result":
        entries.append(TranscriptEntry(
            entry_type="tool_result", timestamp=timestamp,
            tool_name=raw.get("tool_name", ""),
            content=str(raw.get("tool_output", "")),
        ))

    return entries


def parse_transcript_entries(transcript_file: Path, last_n: int = 50) -> list[TranscriptEntry]:
    """Parse transcript into structured entries for display."""
    if not transcript_file or not transcript_file.exists():
        return []

    lines = _read_last_n_lines(transcript_file, last_n)
    entries: list[TranscriptEntry] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        entries.extend(_parse_raw_to_entries(raw))

    return entries


def tail_transcript(transcript_file: Path, last_position: int = 0) -> tuple[list[TranscriptEntry], int]:
    """Read new entries from a transcript file since last_position.
    Returns (new_entries, new_position)."""
    if not transcript_file or not transcript_file.exists():
        return [], last_position

    try:
        file_size = transcript_file.stat().st_size
        if file_size <= last_position:
            return [], last_position

        with open(transcript_file, "r", encoding="utf-8", errors="replace") as f:
            f.seek(last_position)
            new_data = f.read()
            new_position = f.tell()

        entries: list[TranscriptEntry] = []
        for line in new_data.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            entries.extend(_parse_raw_to_entries(raw))

        return entries, new_position
    except OSError:
        return [], last_position


def scan_sessions(active_only: bool = True) -> list[Session]:
    """Scan for Claude Code sessions."""
    sessions: list[Session] = []

    if not SESSIONS_DIR.exists():
        return sessions

    pane_map = _build_tmux_pane_map()
    now = datetime.now(timezone.utc)

    for session_file in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(session_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        pid = data.get("pid", 0)
        alive = _is_pid_alive(pid)

        if active_only and not alive:
            continue

        session_id = data.get("sessionId", "")
        started_at = _ts_to_dt(data.get("startedAt", 0))
        cwd = data.get("cwd", "")
        transcript_file = _find_transcript(session_id, cwd)

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
            is_recently_active=_is_session_active(pid, transcript_file, session_file),
            project_name=_extract_project_name(cwd),
            transcript_path=transcript_file,
            duration_secs=max(0, duration),
        )

        session.tmux_pane = _find_tmux_pane(pid, pane_map)

        transcript_info = _parse_transcript(transcript_file)
        session.last_activity = transcript_info["last_activity"]
        session.last_activity_time = transcript_info["last_activity_time"]
        session.tool_count = transcript_info["tool_count"]
        session.recent_tools = transcript_info["recent_tools"]
        session.message_count = transcript_info["message_count"]

        sessions.append(session)

    sessions.sort(key=lambda s: s.started_at, reverse=True)
    return sessions


def cleanup_dead_sessions() -> list[Path]:
    """Find session files whose PIDs are dead. Returns list of stale file paths."""
    stale: list[Path] = []
    if not SESSIONS_DIR.exists():
        return stale
    for session_file in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(session_file.read_text())
            pid = data.get("pid", 0)
            if not _is_pid_alive(pid):
                stale.append(session_file)
        except (json.JSONDecodeError, OSError):
            stale.append(session_file)
    return stale


def remove_dead_sessions() -> int:
    """Remove session files for dead processes. Returns count removed."""
    stale = cleanup_dead_sessions()
    for f in stale:
        try:
            f.unlink()
        except OSError:
            pass
    return len(stale)


def get_daily_stats() -> dict:
    """Read stats from stats-cache.json."""
    stats_file = CLAUDE_DIR / "stats-cache.json"
    if not stats_file.exists():
        return {}
    try:
        data = json.loads(stats_file.read_text())
        today = datetime.now().strftime("%Y-%m-%d")
        for entry in data.get("dailyActivity", []):
            if entry.get("date") == today:
                return entry
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def get_session_history(session_id: str, limit: int = 30) -> list[dict]:
    """Read history entries for a specific session from history.jsonl."""
    history_file = CLAUDE_DIR / "history.jsonl"
    if not history_file.exists():
        return []

    lines = _read_last_n_lines(history_file, 500)
    entries = []
    for line in lines:
        try:
            entry = json.loads(line.strip())
            if entry.get("sessionId") == session_id:
                entries.append(entry)
        except json.JSONDecodeError:
            continue
    return entries[-limit:]


def switch_to_tmux_pane(pane: str) -> bool:
    """Switch tmux focus to the given pane (e.g. 'ai:5'), even across tmux sessions."""
    if not pane:
        return False
    try:
        # select-window targets the window within its session
        subprocess.run(
            ["tmux", "select-window", "-t", pane],
            capture_output=True, timeout=3,
        )
        # switch-client moves the current client to that session
        # pane is "session:window", we need just the session name
        session_name = pane.split(":")[0] if ":" in pane else pane
        subprocess.run(
            ["tmux", "switch-client", "-t", session_name],
            capture_output=True, timeout=3,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def send_keys_to_tmux(pane: str, text: str) -> bool:
    """Send keystrokes to a tmux pane. Returns True on success."""
    if not pane:
        return False
    try:
        # Send the text, then press Enter
        subprocess.run(
            ["tmux", "send-keys", "-t", pane, text, "Enter"],
            capture_output=True, timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _detect_jarvis_tmux_session() -> str:
    """Find the tmux session where existing Jarvis/Claude sessions live."""
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_pid} #{session_name}"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            return ""
        # Check which tmux session has the most Claude processes
        from collections import Counter
        session_counts: Counter[str] = Counter()
        pane_pids = {}
        for line in result.stdout.strip().splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2:
                try:
                    pane_pids[int(parts[0])] = parts[1]
                except ValueError:
                    pass

        sessions = scan_sessions(active_only=True)
        for s in sessions:
            try:
                proc = psutil.Process(s.pid)
                parent = proc.parent()
                if parent and parent.pid in pane_pids:
                    session_counts[pane_pids[parent.pid]] += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if session_counts:
            return session_counts.most_common(1)[0][0]
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return ""


def spawn_session_in_tmux(cwd: str | None = None, name: str | None = None) -> bool:
    """Spawn a new Jarvis session in the ai tmux session."""
    try:
        # Find which tmux session has the Jarvis sessions
        target_session = _detect_jarvis_tmux_session()

        cmd = ["tmux", "new-window", "-d"]
        if target_session:
            cmd.extend(["-t", target_session])
        if cwd:
            cmd.extend(["-c", cwd])
        jarvis_cmd = "jarvis"
        if name:
            jarvis_cmd = f'jarvis --name "{name}"'
        cmd.extend(["zsh", "-ic", jarvis_cmd])
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (FileNotFoundError, OSError):
        return False


@dataclass
class HistoricalSession:
    """A past session that can be resumed."""
    session_id: str
    name: str
    first_prompt: str
    line_count: int
    modified: datetime
    is_alive: bool


def list_resumable_sessions(limit: int = 30) -> list[HistoricalSession]:
    """List historical sessions that can be resumed, sorted by most recent."""
    if not PROJECTS_DIR.exists():
        return []

    # Find all project dirs
    results: list[HistoricalSession] = []
    active_ids = set()

    # Get active session IDs
    if SESSIONS_DIR.exists():
        for sf in SESSIONS_DIR.glob("*.json"):
            try:
                data = json.loads(sf.read_text())
                sid = data.get("sessionId", "")
                if sid and _is_pid_alive(data.get("pid", 0)):
                    active_ids.add(sid)
            except (json.JSONDecodeError, OSError):
                pass

    # Scan all project transcript files
    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for f in project_dir.glob("*.jsonl"):
            uuid = f.stem
            if "-" not in uuid:  # Skip non-UUID files
                continue

            try:
                stat = f.stat()
                lines = 0
                name = ""
                first_prompt = ""

                with open(f, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        lines += 1
                        if lines > 20 and first_prompt:
                            break
                        try:
                            entry = json.loads(line)
                            # Look for session name in active sessions
                            if not name and SESSIONS_DIR.exists():
                                for sf in SESSIONS_DIR.glob("*.json"):
                                    try:
                                        sd = json.loads(sf.read_text())
                                        if sd.get("sessionId") == uuid:
                                            name = sd.get("name", "")
                                            break
                                    except (json.JSONDecodeError, OSError):
                                        pass

                            # Get first user prompt
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
                        except json.JSONDecodeError:
                            pass

                if lines < 3:  # Skip nearly empty sessions
                    continue

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


def resume_session_in_tmux(session_id: str, cwd: str | None = None) -> bool:
    """Resume a specific session in a new tmux window."""
    try:
        target_session = _detect_jarvis_tmux_session()

        cmd = ["tmux", "new-window", "-d"]
        if target_session:
            cmd.extend(["-t", target_session])
        if cwd:
            cmd.extend(["-c", cwd])
        cmd.extend(["zsh", "-ic", f'claude --resume "{session_id}"'])
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (FileNotFoundError, OSError):
        return False


def send_notification(title: str, message: str) -> None:
    """Send a macOS desktop notification."""
    try:
        subprocess.Popen([
            "osascript", "-e",
            f'display notification "{message}" with title "{title}"',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, OSError):
        pass


# ═════════════════════════════════════════════════════════════════════════════
# Context Transfer
# ═════════════════════════════════════════════════════════════════════════════

def extract_session_context(session: Session, max_entries: int = 30) -> str:
    """Extract a readable context summary from a session's transcript."""
    if not session.transcript_path or not session.transcript_path.exists():
        return ""

    entries = parse_transcript_entries(session.transcript_path, last_n=max_entries)
    if not entries:
        return ""

    lines = []
    name = session.name or session.tmux_pane or session.session_id[:12]
    lines.append(f"=== Context from session: {name} ===")
    lines.append(f"Directory: {session.cwd}")
    lines.append("")

    for entry in entries:
        if entry.entry_type == "user":
            lines.append(f"USER: {entry.content}")
        elif entry.entry_type == "assistant":
            # Keep assistant responses but trim very long ones
            text = entry.content
            if len(text) > 500:
                text = text[:500] + "... [truncated]"
            lines.append(f"ASSISTANT: {text}")
        elif entry.entry_type == "tool_use":
            lines.append(f"TOOL: {entry.tool_name} — {entry.tool_desc}")

    return "\n".join(lines)


def transfer_context(source: Session, target: Session) -> bool:
    """Transfer context from source session to target session via tmux send-keys."""
    if not target.tmux_pane:
        return False

    context = extract_session_context(source)
    if not context:
        return False

    # Build a prompt that gives the target session the context
    source_name = source.name or source.tmux_pane or "another session"
    prompt = (
        f"Here is context transferred from session '{source_name}'. "
        f"Use this to understand what was done and continue the work if needed:\n\n"
        f"{context}\n\n"
        f"Acknowledge you received this context and summarize the key points."
    )

    return send_keys_to_tmux(target.tmux_pane, prompt)


# ═════════════════════════════════════════════════════════════════════════════
# Task Delegation
# ═════════════════════════════════════════════════════════════════════════════

def delegate_task(task: str, cwd: str | None = None) -> list[str]:
    """Break a task into sub-tasks using claude -p and spawn sessions for each.
    Returns list of spawned session names."""
    # Use claude headless to break down the task
    prompt = (
        f"Break this task into 2-4 independent sub-tasks that can be worked on in parallel by separate AI coding agents. "
        f"Each sub-task should be self-contained and actionable. "
        f"Output ONLY a JSON array, no markdown, no explanation. Format: "
        f'[{{"name": "short-kebab-name", "prompt": "detailed instruction for the agent"}}]\n\n'
        f"Task: {task}"
    )

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True, text=True, timeout=60,
            cwd=cwd,
        )
        if result.returncode != 0:
            return []

        # Parse the JSON output — claude --output-format json wraps in a result object
        import re
        output = result.stdout.strip()

        # Try to parse the full output as JSON first (claude wraps it)
        try:
            wrapper = json.loads(output)
            # claude --output-format json returns {"result": "...", ...}
            if isinstance(wrapper, dict) and "result" in wrapper:
                output = wrapper["result"]
        except json.JSONDecodeError:
            pass

        # Find the JSON array in the output
        match = re.search(r'\[.*\]', output, re.DOTALL)
        if not match:
            return []

        subtasks = json.loads(match.group())
        if not isinstance(subtasks, list):
            return []

        spawned = []
        for st in subtasks:
            name = st.get("name", "subtask")
            prompt_text = st.get("prompt", task)

            if spawn_session_in_tmux(cwd=cwd, name=name):
                spawned.append(name)
                # Wait for session to start, then send the prompt
                import threading

                def _send_prompt(pane_name: str, text: str):
                    # Give jarvis time to boot
                    time.sleep(8)
                    # Find the new session's tmux pane
                    pane_map = _build_tmux_pane_map()
                    # Search for any pane - the newest sessions are at highest window indices
                    sessions = scan_sessions(active_only=True)
                    for s in sessions:
                        if s.name == pane_name and s.tmux_pane:
                            send_keys_to_tmux(s.tmux_pane, text)
                            return
                    # Fallback: try all panes, find idle ones
                    # Just send to the most recently created window
                    try:
                        r = subprocess.run(
                            ["tmux", "list-windows", "-F", "#{window_index}"],
                            capture_output=True, text=True, timeout=3,
                        )
                        if r.returncode == 0:
                            windows = r.stdout.strip().splitlines()
                            if windows:
                                send_keys_to_tmux(f":{windows[-1]}", text)
                    except (subprocess.TimeoutExpired, OSError):
                        pass

                threading.Thread(
                    target=_send_prompt,
                    args=(name, prompt_text),
                    daemon=True,
                ).start()

        return spawned

    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []


# ═════════════════════════════════════════════════════════════════════════════
# Auto-Journaling
# ═════════════════════════════════════════════════════════════════════════════

def generate_journal(session: Session, output_dir: str | None = None) -> str | None:
    """Generate a journal entry for a specific session.
    Returns the file path of the written journal, or None on failure."""
    context = extract_session_context(session, max_entries=50)
    if not context:
        return None

    name = session.name or session.tmux_pane or session.session_id[:12]
    session_summary = (
        f"Session: {name}\n"
        f"Duration: {session.duration_display} | Messages: {session.message_count} | Tools: {session.tool_count}\n"
        f"Directory: {session.cwd}\n\n"
        f"{context}"
    )

    prompt = (
        f"You are writing a work journal entry for a software engineer based on a single Claude Code session. "
        f"Write a concise markdown journal entry that captures:\n"
        f"1. What was accomplished (bullet points)\n"
        f"2. Key decisions made\n"
        f"3. Open items / things to follow up on\n"
        f"4. Any interesting findings or learnings\n\n"
        f"Keep it concise and useful for future reference. "
        f"Use markdown formatting. Do NOT include any preamble like 'Here is...' — just the journal content.\n\n"
        f"Session transcript:\n\n{session_summary}"
    )

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True, text=True, timeout=90,
        )
        if result.returncode != 0:
            return None

        output = result.stdout.strip()

        # Parse claude JSON wrapper
        try:
            wrapper = json.loads(output)
            if isinstance(wrapper, dict) and "result" in wrapper:
                journal_content = wrapper["result"]
            else:
                journal_content = output
        except json.JSONDecodeError:
            journal_content = output

        # Write to journal file
        today = datetime.now()
        # Include session name in filename for per-session journals
        slug = (session.name or session.tmux_pane or session.session_id[:8]).replace(":", "-").replace(" ", "-").lower()
        filename = today.strftime("%d%b%Y").lower() + f"_{slug}.md"
        date_header = today.strftime("%A, %B %d, %Y")

        full_content = f"# Session Journal — {name}\n## {date_header}\n\n{journal_content}\n"

        # Determine output directory
        if not output_dir:
            # Try to find the jarvis journal directory
            candidates = [
                Path.home() / "different" / "jarvis-ai-assistant" / "jarvis-assistant" / "journal",
                Path.home() / "journal",
            ]
            for c in candidates:
                if c.exists():
                    output_dir = str(c)
                    break
            if not output_dir:
                output_dir = str(candidates[0])

        journal_dir = Path(output_dir)
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal_path = journal_dir / filename
        journal_path.write_text(full_content)

        return str(journal_path)

    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None

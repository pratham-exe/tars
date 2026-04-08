"""Low-level utilities shared across scanner modules."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import psutil

CLAUDE_DIR = Path.home() / ".claude"
SESSIONS_DIR = CLAUDE_DIR / "sessions"
TRANSCRIPTS_DIR = CLAUDE_DIR / "transcripts"
PROJECTS_DIR = CLAUDE_DIR / "projects"

ACTIVE_THRESHOLD_SECS = 30


def ts_to_dt(ms_timestamp: int | float) -> datetime:
    return datetime.fromtimestamp(ms_timestamp / 1000, tz=timezone.utc)


def iso_to_dt(iso_str: str) -> datetime:
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc)


def is_pid_alive(pid: int) -> bool:
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def extract_project_name(cwd: str) -> str:
    return Path(cwd).name if cwd else "unknown"


def read_last_n_lines(filepath: Path, n: int = 50) -> list[str]:
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


def find_transcript(session_id: str, cwd: str = "") -> Path | None:
    """Find the transcript JSONL file for a session."""
    if cwd and PROJECTS_DIR.exists():
        encoded_cwd = cwd.replace("/", "-")
        project_dir = PROJECTS_DIR / encoded_cwd
        if project_dir.exists():
            candidate = project_dir / f"{session_id}.jsonl"
            if candidate.exists():
                return candidate

    if PROJECTS_DIR.exists():
        for project_dir in PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            candidate = project_dir / f"{session_id}.jsonl"
            if candidate.exists():
                return candidate

    if TRANSCRIPTS_DIR.exists():
        for pattern in [f"ses_{session_id}*", f"*{session_id[:8]}*"]:
            matches = list(TRANSCRIPTS_DIR.glob(pattern))
            if matches:
                return matches[0]
    return None


import time


def is_file_recently_modified(filepath: Path, threshold_secs: int = ACTIVE_THRESHOLD_SECS) -> bool:
    try:
        mtime = filepath.stat().st_mtime
        return (time.time() - mtime) < threshold_secs
    except OSError:
        return False


def is_session_active(pid: int, transcript_file: Path | None, session_file: Path | None) -> bool:
    """Determine if a session is actively doing work."""
    if transcript_file and is_file_recently_modified(transcript_file):
        return True
    if session_file and is_file_recently_modified(session_file):
        return True
    try:
        proc = psutil.Process(pid)
        cpu = proc.cpu_percent(interval=0.1)
        if cpu > 1.0:
            return True
        for child in proc.children(recursive=True):
            try:
                if child.cpu_percent(interval=0) > 1.0:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return False

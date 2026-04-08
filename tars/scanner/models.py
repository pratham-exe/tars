"""Data models for TARS scanner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


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
        from tars.scanner.utils import iso_to_dt
        return iso_to_dt(self.timestamp)

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


@dataclass
class HistoricalSession:
    """A past session that can be resumed."""
    session_id: str
    name: str
    first_prompt: str
    line_count: int
    modified: datetime
    is_alive: bool

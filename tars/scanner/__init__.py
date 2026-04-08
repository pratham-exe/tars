"""Scanner package — reads Claude Code session data, tmux integration, orchestration."""

from tars.scanner.models import HistoricalSession, Session, ToolActivity, TranscriptEntry
from tars.scanner.sessions import (
    cleanup_dead_sessions,
    get_session_history,
    list_resumable_sessions,
    remove_dead_sessions,
    scan_sessions,
)
from tars.scanner.transcripts import parse_transcript_entries, tail_transcript
from tars.scanner.tmux import (
    resume_session_in_tmux,
    send_keys_to_tmux,
    spawn_session_in_tmux,
    switch_to_tmux_pane,
)
from tars.scanner.actions import (
    delegate_task,
    extract_session_context,
    generate_journal,
    transfer_context,
)

__all__ = [
    "HistoricalSession",
    "Session",
    "ToolActivity",
    "TranscriptEntry",
    "cleanup_dead_sessions",
    "delegate_task",
    "extract_session_context",
    "generate_journal",
    "get_session_history",
    "list_resumable_sessions",
    "parse_transcript_entries",
    "remove_dead_sessions",
    "resume_session_in_tmux",
    "scan_sessions",
    "send_keys_to_tmux",
    "spawn_session_in_tmux",
    "switch_to_tmux_pane",
    "tail_transcript",
    "transfer_context",
]

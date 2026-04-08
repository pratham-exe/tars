"""High-level actions — context transfer, task delegation, journaling."""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from tars.scanner.models import Session
from tars.scanner.transcripts import parse_transcript_entries
from tars.scanner.tmux import build_tmux_pane_map, send_keys_to_tmux, spawn_session_in_tmux


# ── Context Transfer ─────────────────────────────────────────────────────────

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

    source_name = source.name or source.tmux_pane or "another session"
    prompt = (
        f"Here is context transferred from session '{source_name}'. "
        f"Use this to understand what was done and continue the work if needed:\n\n"
        f"{context}\n\n"
        f"Acknowledge you received this context and summarize the key points."
    )

    return send_keys_to_tmux(target.tmux_pane, prompt)


# ── Task Delegation ──────────────────────────────────────────────────────────

def delegate_task(task: str, cwd: str | None = None) -> list[str]:
    """Break a task into sub-tasks using claude -p and spawn sessions for each."""
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

        output = result.stdout.strip()
        try:
            wrapper = json.loads(output)
            if isinstance(wrapper, dict) and "result" in wrapper:
                output = wrapper["result"]
        except json.JSONDecodeError:
            pass

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

                def _send_prompt(pane_name: str, text: str):
                    time.sleep(8)
                    from tars.scanner.sessions import scan_sessions
                    sessions = scan_sessions(active_only=True)
                    for s in sessions:
                        if s.name == pane_name and s.tmux_pane:
                            send_keys_to_tmux(s.tmux_pane, text)
                            return
                    # Fallback: newest window
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


# ── Auto-Journaling ──────────────────────────────────────────────────────────

def generate_journal(session: Session, output_dir: str | None = None) -> str | None:
    """Generate a journal entry for a specific session."""
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
        try:
            wrapper = json.loads(output)
            if isinstance(wrapper, dict) and "result" in wrapper:
                journal_content = wrapper["result"]
            else:
                journal_content = output
        except json.JSONDecodeError:
            journal_content = output

        today = datetime.now()
        slug = (session.name or session.tmux_pane or session.session_id[:8]).replace(":", "-").replace(" ", "-").lower()
        filename = today.strftime("%d%b%Y").lower() + f"_{slug}.md"
        date_header = today.strftime("%A, %B %d, %Y")
        full_content = f"# Session Journal — {name}\n## {date_header}\n\n{journal_content}\n"

        if not output_dir:
            # Use the session's cwd to find a journal directory nearby
            session_dir = Path(session.cwd) if session.cwd else Path.home()
            candidates = [
                session_dir / "journal",
                session_dir.parent / "journal",
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

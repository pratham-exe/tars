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

def delegate_task(task: str, cwd: str | None = None) -> dict:
    """Spawn an orchestrator Jarvis session that breaks the task down and spawns its own workers.
    Returns {"orchestrator": name, "workers": []} — workers are spawned by the orchestrator itself."""
    try:
        orch_name = "orchestrator"
        if not spawn_session_in_tmux(cwd=cwd, name=orch_name):
            return {}

        def _brief_orchestrator():
            time.sleep(10)

            from tars.scanner.sessions import scan_sessions
            sessions = scan_sessions(active_only=True)
            orch_session = next((s for s in sessions if s.name == orch_name), None)
            if not orch_session or not orch_session.tmux_pane:
                return

            # Detect which tmux session to spawn workers in
            tmux_session = orch_session.tmux_pane.split(":")[0] if orch_session.tmux_pane else "ai"
            project_cwd = cwd or orch_session.cwd

            orch_prompt = (
                f"You are the ORCHESTRATOR. Your job is to break a task into sub-tasks, spawn worker sessions, send them work, and track progress.\n\n"
                f"TASK:\n{task}\n\n"
                f"HOW TO SPAWN WORKERS:\n"
                f"Use the Bash tool to spawn each worker Jarvis session:\n"
                f'  tmux new-window -d -t {tmux_session} -c "{project_cwd}" zsh -ic \'jarvis --name "worker-<name>"\'\n'
                f"Wait ~8 seconds after spawning for each worker to boot.\n\n"
                f"HOW TO SEND TASKS TO WORKERS:\n"
                f"1. Write the prompt to a temp file:  echo \'your prompt here\' > /tmp/tars-worker-msg.txt\n"
                f"2. Send it:  tmux load-buffer /tmp/tars-worker-msg.txt && tmux paste-buffer -t {tmux_session}:<window> && tmux send-keys -t {tmux_session}:<window> Enter\n"
                f"To find worker window numbers:  tmux list-windows -t {tmux_session} -F '#{{window_index}} #{{window_name}}'\n\n"
                f"HOW TO CHECK WORKER PROGRESS:\n"
                f"Worker transcripts are in ~/.claude/projects/ as JSONL files. Find them by session ID.\n"
                f"Or list windows:  tmux list-windows -t {tmux_session}\n"
                f"Read the last 30 lines of a worker's transcript to see what they're doing.\n\n"
                f"WORKFLOW:\n"
                f"1. Analyze the task and decide how many workers you need (2-4)\n"
                f"2. Spawn that many worker sessions\n"
                f"3. Send each worker their specific sub-task\n"
                f"4. Wait for the user to ask for status — then check worker transcripts and report\n"
                f"5. When all workers are done, synthesize results\n\n"
                f"START NOW: Analyze the task, decide on sub-tasks, and spawn workers."
            )
            send_keys_to_tmux(orch_session.tmux_pane, orch_prompt)

        threading.Thread(target=_brief_orchestrator, daemon=True).start()

        return {
            "orchestrator": orch_name,
            "workers": [],  # Workers will be spawned by the orchestrator itself
        }

    except (FileNotFoundError, OSError):
        return {}


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

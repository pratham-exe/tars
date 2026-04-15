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

SCRATCHPAD_DIR = Path("/tmp/tars-delegation")


def _setup_scratchpad(task: str) -> Path:
    """Create a fresh scratchpad directory for a delegation."""
    import shutil
    if SCRATCHPAD_DIR.exists():
        shutil.rmtree(SCRATCHPAD_DIR)
    SCRATCHPAD_DIR.mkdir(parents=True, exist_ok=True)

    # Write the task file
    (SCRATCHPAD_DIR / "task.md").write_text(f"# Task\n\n{task}\n")
    # Create status file
    (SCRATCHPAD_DIR / "status.json").write_text("{}")
    # Create orchestrator notes
    (SCRATCHPAD_DIR / "orchestrator.md").write_text("# Orchestrator Notes\n\n")

    return SCRATCHPAD_DIR


def read_scratchpad() -> dict:
    """Read the current scratchpad state for display in TARS."""
    result = {
        "task": "",
        "status": {},
        "worker_reports": [],
        "orchestrator_notes": "",
    }
    if not SCRATCHPAD_DIR.exists():
        return result

    task_file = SCRATCHPAD_DIR / "task.md"
    if task_file.exists():
        result["task"] = task_file.read_text()

    status_file = SCRATCHPAD_DIR / "status.json"
    if status_file.exists():
        try:
            result["status"] = json.loads(status_file.read_text())
        except json.JSONDecodeError:
            pass

    orch_file = SCRATCHPAD_DIR / "orchestrator.md"
    if orch_file.exists():
        result["orchestrator_notes"] = orch_file.read_text()

    # Read all worker report files
    for f in sorted(SCRATCHPAD_DIR.glob("worker-*.md")):
        result["worker_reports"].append({
            "name": f.stem,
            "content": f.read_text(),
        })

    return result


def delegate_task(task: str, cwd: str | None = None) -> dict:
    """Spawn an orchestrator Jarvis session that breaks the task down and spawns its own workers.
    Uses a shared scratchpad at /tmp/tars-delegation/ for inter-session communication."""
    try:
        scratchpad = _setup_scratchpad(task)
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

            tmux_session = orch_session.tmux_pane.split(":")[0] if orch_session.tmux_pane else "ai"
            project_cwd = cwd or orch_session.cwd

            orch_prompt = (
                f"You are the ORCHESTRATOR. You break tasks into sub-tasks, spawn workers, and track progress.\n\n"
                f"TASK:\n{task}\n\n"
                f"SHARED SCRATCHPAD: {scratchpad}\n"
                f"This directory is shared between you and all workers for communication.\n\n"
                f"FILES IN SCRATCHPAD:\n"
                f"  - task.md — the original task (already written)\n"
                f"  - status.json — you maintain this: {{\"worker-name\": \"status\"}}\n"
                f"    Statuses: pending, in-progress, done, failed, blocked\n"
                f"  - orchestrator.md — your notes, synthesis, decisions\n"
                f"  - worker-<name>.md — each worker writes their progress/findings here\n\n"
                f"HOW TO SPAWN WORKERS:\n"
                f'  tmux new-window -d -t {tmux_session} -c "{project_cwd}" zsh -ic \'jarvis --name "worker-<name>"\'\n'
                f"Wait ~8 seconds after spawning.\n\n"
                f"HOW TO SEND TASKS TO WORKERS:\n"
                f"1. Write prompt to file:  echo 'prompt' > /tmp/tars-worker-msg.txt\n"
                f"2. Send:  tmux load-buffer /tmp/tars-worker-msg.txt && tmux paste-buffer -t {tmux_session}:<window> && tmux send-keys -t {tmux_session}:<window> Enter\n"
                f"Find windows:  tmux list-windows -t {tmux_session} -F '#{{window_index}} #{{window_name}}'\n\n"
                f"IMPORTANT — TELL EACH WORKER:\n"
                f"When sending a task to a worker, include these instructions in the prompt:\n"
                f"  'SCRATCHPAD: Write your progress and findings to {scratchpad}/worker-<your-name>.md\n"
                f"   Update it as you work. Write a summary when done.\n"
                f"   Check {scratchpad}/orchestrator.md for instructions from the orchestrator.'\n\n"
                f"YOUR WORKFLOW:\n"
                f"1. Analyze the task, decide sub-tasks and number of workers (2-4)\n"
                f"2. Spawn workers and send each their sub-task WITH scratchpad instructions\n"
                f"3. Update status.json as workers progress\n"
                f"4. Read worker-*.md files to check their progress\n"
                f"5. Write synthesis to orchestrator.md when workers are done\n"
                f"6. When asked for status, read all scratchpad files and report\n\n"
                f"START NOW: Analyze the task, decide on sub-tasks, and spawn workers."
            )
            send_keys_to_tmux(orch_session.tmux_pane, orch_prompt)

        threading.Thread(target=_brief_orchestrator, daemon=True).start()

        return {
            "orchestrator": orch_name,
            "workers": [],
            "scratchpad": str(scratchpad),
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

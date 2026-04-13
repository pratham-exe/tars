"""Tmux integration — pane detection, send-keys, spawn, resume, switch."""

from __future__ import annotations

import subprocess
from collections import Counter

import psutil

from tars.scanner.utils import is_pid_alive


def build_tmux_pane_map() -> dict[int, str]:
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


def find_tmux_pane(pid: int, pane_map: dict[int, str]) -> str:
    """Walk up the process tree to find which tmux pane owns this PID."""
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


def switch_to_tmux_pane(pane: str) -> bool:
    """Switch tmux focus to the given pane, even across tmux sessions."""
    if not pane:
        return False
    try:
        subprocess.run(
            ["tmux", "select-window", "-t", pane],
            capture_output=True, timeout=3,
        )
        session_name = pane.split(":")[0] if ":" in pane else pane
        subprocess.run(
            ["tmux", "switch-client", "-t", session_name],
            capture_output=True, timeout=3,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def send_keys_to_tmux(pane: str, text: str) -> bool:
    """Send text to a tmux pane and press Enter.
    Uses bracket paste mode to prevent newlines from being interpreted as Enter."""
    if not pane:
        return False
    try:
        import tempfile
        import time
        import os

        clean_text = text.rstrip("\n")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(clean_text)
            tmp_path = f.name
        try:
            # Load text into tmux buffer
            r1 = subprocess.run(
                ["tmux", "load-buffer", tmp_path],
                capture_output=True, timeout=5,
            )
            if r1.returncode != 0:
                return False

            # Enable bracket paste mode so the app treats the paste as one block
            # This prevents newlines from being interpreted as Enter
            subprocess.run(
                ["tmux", "send-keys", "-t", pane, "\x1b[200~"],
                capture_output=True, timeout=5,
            )
            # Paste the buffer
            subprocess.run(
                ["tmux", "paste-buffer", "-t", pane],
                capture_output=True, timeout=5,
            )
            # End bracket paste mode
            subprocess.run(
                ["tmux", "send-keys", "-t", pane, "\x1b[201~"],
                capture_output=True, timeout=5,
            )

            time.sleep(0.3)

            # Now press Enter to submit
            subprocess.run(
                ["tmux", "send-keys", "-t", pane, "Enter"],
                capture_output=True, timeout=5,
            )
        finally:
            os.unlink(tmp_path)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def detect_jarvis_tmux_session() -> str:
    """Find the tmux session where existing Jarvis/Claude sessions live."""
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_pid} #{session_name}"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            return ""
        session_counts: Counter[str] = Counter()
        pane_pids: dict[int, str] = {}
        for line in result.stdout.strip().splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2:
                try:
                    pane_pids[int(parts[0])] = parts[1]
                except ValueError:
                    pass

        # Import here to avoid circular dependency
        from tars.scanner.sessions import scan_sessions
        sessions = scan_sessions(active_only=True)
        for s in sessions:
            try:
                proc = psutil.Process(s.pid)
                # Check the PID itself and its parent
                if s.pid in pane_pids:
                    session_counts[pane_pids[s.pid]] += 1
                else:
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
        target_session = detect_jarvis_tmux_session()
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


def resume_session_in_tmux(session_id: str, cwd: str | None = None) -> bool:
    """Resume a specific session in a new tmux window."""
    try:
        target_session = detect_jarvis_tmux_session()
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

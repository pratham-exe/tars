"""Delegation screen — live dashboard for orchestrator + worker sessions."""

from __future__ import annotations

import subprocess

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Label, Static

from tars.helpers import escape_markup, time_ago, truncate
from tars.modals import PromptModal
from tars.scanner import (
    Session,
    scan_sessions,
    send_keys_to_tmux,
    switch_to_tmux_pane,
    tail_transcript,
    parse_transcript_entries,
)
from tars.theme import AQUA, BLUE, FG, GRAY, GREEN, ORANGE, PURPLE, RED, YELLOW

CSS = """
Screen {
    background: $surface;
}

#deleg-container {
    height: 1fr;
    padding: 1 2;
}

#deleg-header {
    height: auto;
    padding: 0 0 1 0;
}

#deleg-title {
    text-style: bold;
    color: $accent;
    width: 1fr;
}

#deleg-hints {
    color: $text-muted;
    width: auto;
}

#deleg-task {
    height: auto;
    background: $panel;
    padding: 1 2;
    margin-bottom: 1;
}

#session-tabs {
    height: 2;
    padding: 0;
    margin-bottom: 0;
}

.tab-item {
    height: 2;
    padding: 0 2;
    content-align: center middle;
    background: $panel;
}

.tab-item.active-tab {
    background: $boost;
    border-bottom: tall $accent;
    text-style: bold;
    color: $accent;
}

#transcript-area {
    height: 1fr;
    padding: 1 2;
    background: $boost;
}

#transcript-header {
    height: auto;
    padding-bottom: 1;
}

#transcript-name {
    text-style: bold;
    color: $accent;
    width: 1fr;
}

#transcript-status {
    width: auto;
}

#transcript-scroll {
    height: 1fr;
}

.ts-entry {
    height: auto;
    padding: 0;
}

.ts-time {
    color: $accent;
    width: 10;
}

.ts-content {
    width: 1fr;
}
"""


class DelegationScreen(Screen):
    """Live dashboard for a delegated task — switch between orchestrator and workers."""

    CSS = CSS

    BINDINGS = [
        Binding("escape", "go_back", "Back", priority=True),
        Binding("q", "go_back", "Back", priority=True),
        Binding("r", "refresh", "Refresh", priority=True),
        Binding("i", "prompt_current", "Send", priority=True),
        Binding("s", "ask_status", "Status", priority=True),
        Binding("o", "goto_current", "Go to", priority=True),
        Binding("X", "kill_all", "Kill All", priority=True),
        Binding("b", "toggle_scratchpad", "Board", priority=True),
        Binding("H", "prev_tab", show=False, priority=True),
        Binding("L", "next_tab", show=False, priority=True),
        Binding("j", "scroll_down", show=False, priority=True),
        Binding("k", "scroll_up", show=False, priority=True),
        Binding("G", "scroll_end", show=False, priority=True),
        Binding("g", "scroll_start", show=False, priority=True),
    ]

    def __init__(self, task_description: str, orchestrator_name: str) -> None:
        super().__init__()
        self._task_description: str = str(task_description)
        self._orch_name: str = str(orchestrator_name)
        self._all_sessions: list[Session] = []  # orchestrator + workers
        self._active_tab: int = 0  # 0 = orchestrator, 1+ = workers
        self._tail_positions: dict[str, int] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="deleg-container"):
            with Horizontal(id="deleg-header"):
                yield Label(f"[{ORANGE}][b]Delegation[/b][/{ORANGE}]", id="deleg-title")
                yield Label(
                    "[dim]q[/dim] back  [dim]H/L[/dim] switch  [dim]i[/dim] send  [dim]s[/dim] status  [dim]b[/dim] board  [dim]o[/dim] go to  [dim]X[/dim] kill all",
                    id="deleg-hints",
                )
            yield Static("", id="deleg-task")
            yield Horizontal(id="session-tabs")
            with Vertical(id="transcript-area"):
                with Horizontal(id="transcript-header"):
                    yield Label("", id="transcript-name")
                    yield Label("", id="transcript-status")
                yield VerticalScroll(id="transcript-scroll")

    def on_mount(self) -> None:
        task_display = escape_markup(truncate(self._task_description, 200))
        self.query_one("#deleg-task", Static).update(f"[b]Task:[/b] {task_display}")
        self._refresh_sessions()
        self.set_interval(3, self._refresh_sessions)
        self.set_interval(1.5, self._poll_active_transcript)

    # ── Data ─────────────────────────────────────────────────────────────

    @work(thread=True)
    def _refresh_sessions(self) -> None:
        sessions = scan_sessions(active_only=True)
        self.app.call_from_thread(self._apply_sessions, sessions)

    def _apply_sessions(self, sessions: list[Session]) -> None:
        # Orchestrator first, then workers
        orch = next((s for s in sessions if s.name == self._orch_name), None)
        workers = [s for s in sessions if s.name and s.name.startswith("worker-") and s.tmux_pane]
        workers.sort(key=lambda s: s.name)

        self._all_sessions = []
        if orch:
            self._all_sessions.append(orch)
        self._all_sessions.extend(workers)

        if self._active_tab >= len(self._all_sessions):
            self._active_tab = 0

        self._render_tabs()
        self._render_active_transcript()

    def _render_tabs(self) -> None:
        tabs_container = self.query_one("#session-tabs", Horizontal)
        tabs_container.remove_children()

        widgets = []
        for i, s in enumerate(self._all_sessions):
            name = s.name or s.tmux_pane or "?"

            if s.is_alive and s.is_recently_active:
                dot = f"[{GREEN}]⦿[/{GREEN}]"
            elif s.is_alive:
                dot = f"[{YELLOW}]●[/{YELLOW}]"
            else:
                dot = f"[{RED}]●[/{RED}]"

            # Shorten name for tab
            display_name = name if len(name) <= 15 else name[:12] + "…"
            tab = Label(f" {dot} {display_name} ", classes="tab-item")
            if i == self._active_tab:
                tab.add_class("active-tab")
            widgets.append(tab)

        tabs_container.mount_all(widgets)

    def _render_active_transcript(self) -> None:
        if not self._all_sessions:
            self.query_one("#transcript-name", Label).update(f"[{GRAY}]No sessions yet[/{GRAY}]")
            self.query_one("#transcript-status", Label).update("")
            return

        session = self._all_sessions[self._active_tab]
        name = session.name or session.tmux_pane or "?"
        pane = session.tmux_pane or "?"

        if session.is_alive and session.is_recently_active:
            status = f"[{GREEN}]● ACTIVE[/{GREEN}]"
        elif session.is_alive:
            status = f"[{YELLOW}]● IDLE[/{YELLOW}]"
        else:
            status = f"[{RED}]● DEAD[/{RED}]"

        self.query_one("#transcript-name", Label).update(
            f"[{ORANGE}][b]{name}[/b][/{ORANGE}]  [{GRAY}]{pane}[/{GRAY}]  "
            f"[{BLUE}]{session.message_count} msgs[/{BLUE}]  "
            f"[{PURPLE}]{session.tool_count} tools[/{PURPLE}]  "
            f"[{GRAY}]{session.duration_display}[/{GRAY}]"
        )
        self.query_one("#transcript-status", Label).update(status)

        # Load transcript
        scroll = self.query_one("#transcript-scroll", VerticalScroll)
        scroll.remove_children()

        if not session.transcript_path:
            scroll.mount(Label(f"[{GRAY}]No transcript yet[/{GRAY}]"))
            return

        entries = parse_transcript_entries(session.transcript_path, last_n=100)
        widgets = []
        for entry in entries:
            dt = entry.dt
            time_str = dt.strftime("%H:%M:%S") if dt else ""
            row = Horizontal(classes="ts-entry")
            row.compose_add_child(Label(f"[{GRAY}]{time_str}[/{GRAY}]", classes="ts-time"))
            row.compose_add_child(Label(entry.display, classes="ts-content"))
            widgets.append(row)

        scroll.mount_all(widgets)

        try:
            self._tail_positions[session.session_id] = session.transcript_path.stat().st_size
        except OSError:
            pass

        scroll.scroll_end(animate=False)

    def _poll_active_transcript(self) -> None:
        if not self._all_sessions:
            return
        session = self._all_sessions[self._active_tab]
        if not session.transcript_path:
            return

        last_pos = self._tail_positions.get(session.session_id, 0)
        new_entries, new_pos = tail_transcript(session.transcript_path, last_pos)
        if not new_entries:
            return

        self._tail_positions[session.session_id] = new_pos
        scroll = self.query_one("#transcript-scroll", VerticalScroll)

        for entry in new_entries:
            dt = entry.dt
            time_str = dt.strftime("%H:%M:%S") if dt else ""
            row = Horizontal(classes="ts-entry")
            row.compose_add_child(Label(f"[{GRAY}]{time_str}[/{GRAY}]", classes="ts-time"))
            row.compose_add_child(Label(entry.display, classes="ts-content"))
            scroll.mount(row)

        scroll.scroll_end(animate=False)

    # ── Tab switching ────────────────────────────────────────────────────

    def action_next_tab(self) -> None:
        if self._all_sessions:
            self._active_tab = (self._active_tab + 1) % len(self._all_sessions)
            self._render_tabs()
            self._render_active_transcript()

    def action_prev_tab(self) -> None:
        if self._all_sessions:
            self._active_tab = (self._active_tab - 1) % len(self._all_sessions)
            self._render_tabs()
            self._render_active_transcript()

    # ── Actions ──────────────────────────────────────────────────────────

    def _get_active_session(self) -> Session | None:
        if self._all_sessions and 0 <= self._active_tab < len(self._all_sessions):
            return self._all_sessions[self._active_tab]
        return None

    def action_prompt_current(self) -> None:
        session = self._get_active_session()
        if not session or not session.tmux_pane:
            self.notify("No active session", timeout=2)
            return

        def on_result(text: str) -> None:
            if text:
                if send_keys_to_tmux(session.tmux_pane, text):
                    self.notify(f"Sent to {session.name or session.tmux_pane}", timeout=2)
                else:
                    self.notify("Failed to send", timeout=2)

        self.app.push_screen(PromptModal(session.tmux_pane), on_result)

    def action_ask_status(self) -> None:
        """Quick-send status request to orchestrator."""
        orch = next((s for s in self._all_sessions if s.name == self._orch_name), None)
        if not orch or not orch.tmux_pane:
            self.notify("Orchestrator not found", timeout=2)
            return
        if send_keys_to_tmux(orch.tmux_pane, "Check the status of all workers by reading their transcript files. Report what each worker is doing and their progress."):
            # Switch to orchestrator tab to see the response
            orch_idx = next((i for i, s in enumerate(self._all_sessions) if s.name == self._orch_name), 0)
            self._active_tab = orch_idx
            self._render_tabs()
            self._render_active_transcript()
            self.notify("Asked orchestrator for status", timeout=2)
        else:
            self.notify("Failed to send", timeout=2)

    def action_goto_current(self) -> None:
        session = self._get_active_session()
        if session and session.tmux_pane:
            switch_to_tmux_pane(session.tmux_pane)
        else:
            self.notify("No tmux pane", timeout=2)

    def action_toggle_scratchpad(self) -> None:
        from tars.modals import ScratchpadModal
        self.app.push_screen(ScratchpadModal())

    def action_kill_all(self) -> None:
        """Kill all workers and orchestrator."""
        from tars.modals import ConfirmModal
        count = len(self._all_sessions)
        if not count:
            self.notify("No sessions to kill", timeout=2)
            return

        def on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            import os
            import signal
            killed = 0
            for s in self._all_sessions:
                try:
                    os.kill(s.pid, signal.SIGTERM)
                    if s.tmux_pane:
                        subprocess.run(
                            ["tmux", "kill-window", "-t", s.tmux_pane],
                            capture_output=True, timeout=3,
                        )
                    killed += 1
                except (ProcessLookupError, PermissionError):
                    pass
            self.notify(f"Killed {killed} session(s)", timeout=2)
            self._refresh_sessions()

        self.app.push_screen(
            ConfirmModal(f"Kill all [b]{count}[/b] sessions (orchestrator + workers)?"),
            on_confirm,
        )

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self._refresh_sessions()

    def action_scroll_down(self) -> None:
        self.query_one("#transcript-scroll", VerticalScroll).scroll_down()

    def action_scroll_up(self) -> None:
        self.query_one("#transcript-scroll", VerticalScroll).scroll_up()

    def action_scroll_end(self) -> None:
        self.query_one("#transcript-scroll", VerticalScroll).scroll_end()

    def action_scroll_start(self) -> None:
        self.query_one("#transcript-scroll", VerticalScroll).scroll_home()

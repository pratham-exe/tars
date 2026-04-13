"""Home screen — card-based session list with grouping and actions."""

from __future__ import annotations

import os
import signal
import subprocess
from itertools import groupby

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Input, Label, Static

from tars.helpers import escape_markup, format_started, time_ago, truncate
from tars.modals import ConfirmModal, DelegateModal, ResumePickerModal, SpawnModal
from tars.scanner import (
    HistoricalSession,
    Session,
    delegate_task,
    list_resumable_sessions,
    resume_session_in_tmux,
    scan_sessions,
    spawn_session_in_tmux,
)
from tars.screens.detail import DetailScreen
from tars.theme import (
    AQUA,
    BLUE,
    FG,
    GRAY,
    GREEN,
    ORANGE,
    PURPLE,
    RED,
    TARS_ASCII,
    YELLOW,
)

CSS = """
Screen {
    background: $surface;
    overflow: hidden;
}

#main-container {
    height: 1fr;
}

#top-section {
    height: auto;
}

#banner {
    color: $accent;
    text-align: center;
    padding: 0 1;
    height: auto;
    text-style: bold;
}

#subtitle {
    color: $text-muted;
    text-align: center;
    padding: 0 1 0 1;
    height: auto;
}

#session-scroll {
    height: 1fr;
    padding: 0 1;
}

.session-card {
    height: auto;
    padding: 1 2;
    margin: 0 0 1 0;
    background: $panel;
}

.session-card.selected {
    background: $boost;
    border-left: tall $accent;
}

.session-card.alive-active {
    border-left: tall $success;
}

.session-card.alive-active.selected {
    border-left: tall $accent;
}

.group-header {
    height: 1;
    padding: 0 1;
    margin: 1 0 0 0;
    color: $accent;
    text-style: bold;
}

#filter-bar {
    display: none;
    dock: bottom;
    height: 3;
    padding: 0 1;
    background: $panel;
}

#filter-bar.visible {
    display: block;
}

#filter-input {
    width: 1fr;
}
"""


class HomeScreen(Screen):
    """Main screen with card-based session list."""

    CSS = CSS

    BINDINGS = [
        Binding("q", "exit_app", "Quit", priority=True),
        Binding("r", "refresh", "Refresh", priority=True),
        Binding("n", "spawn_session", "New", priority=True),
        Binding("a", "resume_session", "Resume", priority=True),
        Binding("d", "delegate_task", "Delegate", priority=True),
        Binding("D", "open_delegation", "Deleg View", priority=True),
        Binding("x", "kill_session", "Kill", priority=True),
        Binding("slash", "open_filter", "/Search", priority=True),
        Binding("j", "cursor_down", show=False, priority=True),
        Binding("k", "cursor_up", show=False, priority=True),
        Binding("enter", "open_detail", show=False, priority=True),
        Binding("escape", "close_filter", show=False, priority=True),
    ]

    sessions: list[Session] = []
    filter_text: reactive[str] = reactive("")

    def __init__(self) -> None:
        super().__init__()
        self._cursor: int = 0
        self._visible_session_ids: list[str] = []
        self._last_delegation: tuple[str, str] | None = None  # (task, orch_name)

    def compose(self) -> ComposeResult:
        with Vertical(id="main-container"):
            with Vertical(id="top-section"):
                yield Static(TARS_ASCII, id="banner")
                yield Label(
                    '"Everybody good? Plenty of slaves for my robot colony?" — TARS, Interstellar',
                    id="subtitle",
                )
            yield VerticalScroll(id="session-scroll")
            with Horizontal(id="filter-bar"):
                yield Input(placeholder="Filter sessions...", id="filter-input")
        yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        self._do_refresh()
        self.set_interval(3, self._do_refresh)

    # ── Cursor / selection ───────────────────────────────────────────────

    def _get_selected_session(self) -> Session | None:
        if not self._visible_session_ids or self._cursor >= len(self._visible_session_ids):
            return None
        sid = self._visible_session_ids[self._cursor]
        return next((s for s in self.sessions if s.session_id == sid), None)

    def action_cursor_down(self) -> None:
        if self._visible_session_ids:
            self._cursor = (self._cursor + 1) % len(self._visible_session_ids)
            self._update_selection()

    def action_cursor_up(self) -> None:
        if self._visible_session_ids:
            self._cursor = (self._cursor - 1) % len(self._visible_session_ids)
            self._update_selection()

    def _update_selection(self) -> None:
        scroll = self.query_one("#session-scroll", VerticalScroll)
        cards = list(scroll.query(".session-card"))
        for i, card in enumerate(cards):
            if i == self._cursor:
                card.add_class("selected")
            else:
                card.remove_class("selected")
        if 0 <= self._cursor < len(cards):
            cards[self._cursor].scroll_visible()

    def action_open_detail(self) -> None:
        session = self._get_selected_session()
        if session:
            self.app.push_screen(DetailScreen(session))

    # ── Data refresh ─────────────────────────────────────────────────────

    @work(thread=True)
    def _do_refresh(self) -> None:
        sessions = scan_sessions(active_only=False)
        self.app.call_from_thread(self._apply_refresh, sessions)

    def _apply_refresh(self, sessions: list[Session]) -> None:
        self.sessions = sessions
        self._render_cards()

    def _render_cards(self) -> None:
        scroll = self.query_one("#session-scroll", VerticalScroll)
        scroll.remove_children()

        filtered = self._get_filtered_sorted_sessions()
        self._visible_session_ids = [s.session_id for s in filtered]

        if self._cursor >= len(self._visible_session_ids):
            self._cursor = max(0, len(self._visible_session_ids) - 1)

        if not filtered:
            scroll.mount(Label(f"\n  [{GRAY}]No sessions found[/{GRAY}]"))
            return

        def tmux_key(s: Session) -> str:
            return s.tmux_pane.split(":")[0] if s.tmux_pane else "other"

        grouped = groupby(filtered, key=tmux_key)
        card_idx = 0
        widgets = []

        for group_name, group_sessions in grouped:
            sessions_list = list(group_sessions)
            widgets.append(
                Label(
                    f"[{AQUA}]▎[/{AQUA}] [{AQUA}]{group_name.upper()}[/{AQUA}]  [{GRAY}]({len(sessions_list)})[/{GRAY}]",
                    classes="group-header",
                )
            )

            for session in sessions_list:
                is_selected = card_idx == self._cursor

                if session.is_alive and session.is_recently_active:
                    dot = f"[{GREEN}]⦿ [/{GREEN}]"
                    status_label = f"[{GREEN}]ACTIVE[/{GREEN}]"
                elif session.is_alive:
                    dot = f"[{YELLOW}]● [/{YELLOW}]"
                    status_label = f"[{YELLOW}]IDLE[/{YELLOW}]"
                else:
                    dot = f"[{RED}]● [/{RED}]"
                    status_label = f"[{RED}]DEAD[/{RED}]"

                tmux = session.tmux_pane or "—"
                activity = escape_markup(truncate(session.last_activity, 50)) if session.last_activity else f"[{GRAY}]no recent activity[/{GRAY}]"
                active_ago = time_ago(session.last_activity_time)
                name_part = f"  [{FG}][b]{escape_markup(session.name)}[/b][/{FG}]" if session.name else ""

                line1 = (
                    f"{dot}"
                    f"[{ORANGE}][b]{tmux}[/b][/{ORANGE}]"
                    f"{name_part}  "
                    f"{status_label}  "
                    f"[{FG}]{session.duration_display}[/{FG}]  "
                    f"[{GRAY}]{format_started(session.started_at)}[/{GRAY}]"
                )
                line2 = (
                    f"  [{BLUE}]{session.message_count} msgs[/{BLUE}]  "
                    f"[{PURPLE}]{session.tool_count} tools[/{PURPLE}]  "
                    f"[{GRAY}]active {active_ago}[/{GRAY}]"
                )
                line3 = f"  [{GRAY}]{activity}[/{GRAY}]"

                card = Static(f"{line1}\n{line2}\n{line3}", classes="session-card")
                if is_selected:
                    card.add_class("selected")
                if session.is_alive and session.is_recently_active:
                    card.add_class("alive-active")
                widgets.append(card)
                card_idx += 1

        scroll.mount_all(widgets)

    def _get_filtered_sorted_sessions(self) -> list[Session]:
        sessions = [s for s in self.sessions if s.tmux_pane]
        if self.filter_text:
            ft = self.filter_text.lower()
            sessions = [
                s for s in sessions
                if ft in s.project_name.lower()
                or ft in s.name.lower()
                or ft in s.tmux_pane.lower()
                or ft in s.session_id.lower()
                or ft in str(s.pid)
            ]
        sessions.sort(key=lambda s: s.started_at, reverse=True)
        return sessions

    # ── Actions ──────────────────────────────────────────────────────────

    def action_exit_app(self) -> None:
        self.app.exit()

    def action_refresh(self) -> None:
        self._do_refresh()

    def action_open_filter(self) -> None:
        self.query_one("#filter-bar").add_class("visible")
        self.query_one("#filter-input", Input).focus()

    def action_close_filter(self) -> None:
        bar = self.query_one("#filter-bar")
        if bar.has_class("visible"):
            bar.remove_class("visible")
            self.filter_text = ""
            self.query_one("#filter-input", Input).value = ""
            self._render_cards()

    @on(Input.Changed, "#filter-input")
    def on_filter_changed(self, event: Input.Changed) -> None:
        self.filter_text = event.value
        self._render_cards()

    @on(Input.Submitted, "#filter-input")
    def on_filter_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#filter-bar").remove_class("visible")

    def action_kill_session(self) -> None:
        session = self._get_selected_session()
        if not session:
            return
        if not session.is_alive:
            self.notify("Session already dead", timeout=2)
            return
        name = session.name or session.tmux_pane or str(session.pid)

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                try:
                    os.kill(session.pid, signal.SIGTERM)
                    if session.tmux_pane:
                        subprocess.run(
                            ["tmux", "kill-window", "-t", session.tmux_pane],
                            capture_output=True, timeout=3,
                        )
                    self.notify(f"Killed: {name}", timeout=2)
                    self._do_refresh()
                except (ProcessLookupError, PermissionError):
                    self.notify("Failed to kill", timeout=2)

        self.app.push_screen(ConfirmModal(f"Kill session [b]{name}[/b] (PID {session.pid})?"), on_confirm)

    def action_spawn_session(self) -> None:
        cwd = self.sessions[0].cwd if self.sessions else None

        def on_result(name: str | None) -> None:
            if name is None:
                return
            session_name = name.strip() or None
            if spawn_session_in_tmux(cwd, name=session_name):
                label = f"'{session_name}'" if session_name else ""
                self.notify(f"Spawned new Jarvis session {label}", timeout=2)
                self.set_timer(2, self._do_refresh)
            else:
                self.notify("Failed to spawn", timeout=2)

        self.app.push_screen(SpawnModal(), on_result)

    def action_delegate_task(self) -> None:
        cwd = self.sessions[0].cwd if self.sessions else None

        def on_result(task: str | None) -> None:
            if not task:
                return
            self.notify("Delegating task — breaking down and spawning agents...", timeout=10)
            # Store args for the worker thread to pick up
            self._deleg_task = task
            self._deleg_cwd = cwd
            self._run_delegation()

        self.app.push_screen(DelegateModal(), on_result)

    @work(thread=True)
    def _run_delegation(self) -> None:
        task_text = self._deleg_task
        cwd = self._deleg_cwd
        result = delegate_task(task_text, cwd)
        if result and result.get("orchestrator"):
            orch_name = result["orchestrator"]
            self._pending_delegation = (task_text, orch_name)
            self.app.call_from_thread(self._open_pending_delegation)
        else:
            self.app.call_from_thread(
                self.notify, "Failed to spawn orchestrator", timeout=5
            )
        self.app.call_from_thread(self._apply_refresh, scan_sessions(active_only=False))

    def _open_pending_delegation(self) -> None:
        if hasattr(self, "_pending_delegation"):
            task_text, orch_name = self._pending_delegation
            del self._pending_delegation
            self._last_delegation = (str(task_text), str(orch_name))
            from tars.screens.delegation import DelegationScreen
            self.notify("Orchestrator spawned — it will create workers", timeout=3)
            self.app.push_screen(DelegationScreen(str(task_text), str(orch_name)))

    def action_open_delegation(self) -> None:
        """Reopen the last delegation dashboard."""
        if not self._last_delegation:
            self.notify("No active delegation — press d to start one", timeout=2)
            return
        task_text, orch_name = self._last_delegation
        from tars.screens.delegation import DelegationScreen
        self.app.push_screen(DelegationScreen(task_text, orch_name))

    def action_resume_session(self) -> None:
        self.notify("Loading sessions...", timeout=2)
        self._load_resume_list()

    @work(thread=True)
    def _load_resume_list(self) -> None:
        sessions = list_resumable_sessions(limit=30)
        self.app.call_from_thread(self._show_resume_picker, sessions)

    def _show_resume_picker(self, sessions: list[HistoricalSession]) -> None:
        if not sessions:
            self.notify("No sessions found", timeout=2)
            return
        cwd = self.sessions[0].cwd if self.sessions else None

        def on_pick(picked: HistoricalSession | None) -> None:
            if picked is None:
                return
            if picked.is_alive:
                self.notify("Session is already alive", timeout=2)
                return
            if resume_session_in_tmux(picked.session_id, cwd=cwd):
                name = picked.name or picked.first_prompt[:30] or picked.session_id[:12]
                self.notify(f"Resumed: {name}", timeout=2)
                self.set_timer(3, self._do_refresh)
            else:
                self.notify("Failed to resume", timeout=2)

        self.app.push_screen(ResumePickerModal(sessions), on_pick)

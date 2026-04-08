"""Detail screen — session info, prompt history, live transcript tail, interaction."""

from __future__ import annotations

from datetime import datetime, timezone

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Label, Static

from tars.helpers import time_ago
from tars.modals import ConfirmModal, PromptModal, SessionPickerModal
from tars.scanner import (
    Session,
    generate_journal,
    get_session_history,
    parse_transcript_entries,
    scan_sessions,
    send_keys_to_tmux,
    switch_to_tmux_pane,
    tail_transcript,
    transfer_context,
)
from tars.theme import GRAY, GREEN, ORANGE, RED, YELLOW

CSS = """
Screen {
    background: $surface;
}

#detail-container {
    height: 1fr;
    padding: 1 2;
}

#detail-header {
    height: auto;
    padding: 0 0 1 0;
}

#detail-name {
    text-style: bold;
    color: $accent;
    width: 1fr;
}

#detail-hints {
    color: $text-muted;
    width: auto;
}

#detail-info {
    height: auto;
    background: $panel;
    padding: 1 2;
    margin-bottom: 1;
}

#history-section {
    height: auto;
    max-height: 10;
    padding: 1 2;
    background: $panel;
    margin-bottom: 1;
}

#history-section.focused-section {
    border-left: tall $accent;
}

#history-title {
    text-style: bold;
    color: $secondary;
    padding-bottom: 1;
}

#history-scroll {
    height: auto;
    max-height: 8;
}

.history-entry {
    height: auto;
    padding: 0;
}

.history-time {
    color: $accent;
    width: 14;
    text-style: bold;
}

.history-text {
    width: 1fr;
}

#transcript-section {
    height: 1fr;
    padding: 1 2;
    background: $boost;
}

#transcript-section.focused-section {
    border-left: tall $accent;
}

#transcript-title-bar {
    height: auto;
    padding-bottom: 1;
}

#transcript-title {
    text-style: bold;
    color: $secondary;
}

#transcript-live {
    color: $success;
    width: auto;
    dock: right;
}

#transcript-scroll {
    height: 1fr;
}

.transcript-entry {
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

SECTIONS = ["history", "transcript"]


class DetailScreen(Screen):
    """Full-screen detail view with live transcript tail and tmux interaction."""

    CSS = CSS

    BINDINGS = [
        Binding("escape", "go_back", "Back", priority=True),
        Binding("q", "go_back", "Back", priority=True),
        Binding("r", "refresh_detail", "Refresh", priority=True),
        Binding("i", "open_prompt", "Send", priority=True),
        Binding("t", "transfer_context", "Transfer", priority=True),
        Binding("w", "write_journal", "Journal", priority=True),
        Binding("o", "goto_session", "Go to", priority=True),
        Binding("j", "scroll_down", show=False, priority=True),
        Binding("k", "scroll_up", show=False, priority=True),
        Binding("J", "focus_next_section", show=False, priority=True),
        Binding("K", "focus_prev_section", show=False, priority=True),
        Binding("G", "scroll_end", show=False, priority=True),
        Binding("g", "scroll_start", show=False, priority=True),
    ]

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        self._tail_position: int = 0
        self._focused_section: int = 1

    def compose(self) -> ComposeResult:
        with Vertical(id="detail-container"):
            with Horizontal(id="detail-header"):
                yield Label("", id="detail-name")
                yield Label(
                    "[dim]q[/dim] back  [dim]i[/dim] send  [dim]t[/dim] transfer  [dim]w[/dim] journal  [dim]o[/dim] go to  [dim]j/k[/dim] scroll",
                    id="detail-hints",
                )
            yield Static("", id="detail-info")
            with Vertical(id="history-section"):
                yield Label("Prompts", id="history-title")
                yield VerticalScroll(id="history-scroll")
            with Vertical(id="transcript-section"):
                with Horizontal(id="transcript-title-bar"):
                    yield Label("Live Transcript", id="transcript-title")
                    yield Label("", id="transcript-live")
                yield VerticalScroll(id="transcript-scroll")

    def on_mount(self) -> None:
        self._render_session()
        self._load_transcript()
        self._update_section_focus()
        self.set_interval(1.5, self._poll_transcript)

    def _render_session(self) -> None:
        s = self.session
        name = s.name or s.session_id[:16]
        self.query_one("#detail-name", Label).update(f"Session: {name}")

        if s.is_alive and s.is_recently_active:
            status = f"[{GREEN}]● ACTIVE[/{GREEN}]"
        elif s.is_alive:
            status = f"[{YELLOW}]● IDLE[/{YELLOW}]"
        else:
            status = f"[{RED}]● DEAD[/{RED}]"

        tmux = f"[b]Tmux:[/b] {s.tmux_pane}        " if s.tmux_pane else ""
        info = (
            f"[b]Status:[/b]     {status}        "
            f"[b]PID:[/b] {s.pid}        "
            f"{tmux}"
            f"[b]Duration:[/b] {s.duration_display}\n"
            f"[b]Directory:[/b]  {s.cwd}\n"
            f"[b]Session ID:[/b] {s.session_id}\n"
            f"[b]Started:[/b]    {s.started_at.strftime('%Y-%m-%d %H:%M:%S')} ({time_ago(s.started_at)})        "
            f"[b]Messages:[/b] {s.message_count}        "
            f"[b]Tools:[/b] {s.tool_count}"
        )
        self.query_one("#detail-info", Static).update(info)

        history = get_session_history(s.session_id)
        scroll = self.query_one("#history-scroll", VerticalScroll)
        scroll.remove_children()
        if not history:
            scroll.mount(Label(f"[{GRAY}]No prompts recorded[/{GRAY}]"))
        else:
            for entry in history:
                ts = entry.get("timestamp", 0)
                dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None
                time_str = dt.strftime("%H:%M:%S") if dt else "—"
                text = entry.get("display", "")
                row = Horizontal(classes="history-entry")
                row.compose_add_child(Label(f"[{ORANGE}]{time_str}[/{ORANGE}]", classes="history-time"))
                row.compose_add_child(Label(text, classes="history-text"))
                scroll.mount(row)

    def _load_transcript(self) -> None:
        s = self.session
        if not s.transcript_path:
            return
        entries = parse_transcript_entries(s.transcript_path, last_n=80)
        scroll = self.query_one("#transcript-scroll", VerticalScroll)
        scroll.remove_children()
        for entry in entries:
            dt = entry.dt
            time_str = dt.strftime("%H:%M:%S") if dt else ""
            row = Horizontal(classes="transcript-entry")
            row.compose_add_child(Label(f"[{GRAY}]{time_str}[/{GRAY}]", classes="ts-time"))
            row.compose_add_child(Label(entry.display, classes="ts-content"))
            scroll.mount(row)
        try:
            self._tail_position = s.transcript_path.stat().st_size
        except OSError:
            self._tail_position = 0
        if s.is_alive:
            self.query_one("#transcript-live", Label).update(f"[{GREEN}]● LIVE[/{GREEN}]")
        scroll.scroll_end(animate=False)

    def _poll_transcript(self) -> None:
        s = self.session
        if not s.transcript_path:
            return
        new_entries, new_pos = tail_transcript(s.transcript_path, self._tail_position)
        if not new_entries:
            return
        self._tail_position = new_pos
        scroll = self.query_one("#transcript-scroll", VerticalScroll)
        for entry in new_entries:
            dt = entry.dt
            time_str = dt.strftime("%H:%M:%S") if dt else ""
            row = Horizontal(classes="transcript-entry")
            row.compose_add_child(Label(f"[{GRAY}]{time_str}[/{GRAY}]", classes="ts-time"))
            row.compose_add_child(Label(entry.display, classes="ts-content"))
            scroll.mount(row)
        scroll.scroll_end(animate=False)

    # ── Section focus ────────────────────────────────────────────────────

    def _get_active_scroll(self) -> VerticalScroll:
        return self.query_one(
            "#history-scroll" if SECTIONS[self._focused_section] == "history" else "#transcript-scroll",
            VerticalScroll,
        )

    def _update_section_focus(self) -> None:
        for i, name in enumerate(SECTIONS):
            el = self.query_one(f"#{name}-section")
            if i == self._focused_section:
                el.add_class("focused-section")
            else:
                el.remove_class("focused-section")

    def action_focus_next_section(self) -> None:
        self._focused_section = (self._focused_section + 1) % len(SECTIONS)
        self._update_section_focus()

    def action_focus_prev_section(self) -> None:
        self._focused_section = (self._focused_section - 1) % len(SECTIONS)
        self._update_section_focus()

    # ── Actions ──────────────────────────────────────────────────────────

    def action_open_prompt(self) -> None:
        if not self.session.is_alive:
            self.notify("Session is dead", timeout=2)
            return
        if not self.session.tmux_pane:
            self.notify("No tmux pane found", timeout=2)
            return

        def on_result(text: str) -> None:
            if text:
                if send_keys_to_tmux(self.session.tmux_pane, text):
                    self.notify(f"Sent to {self.session.tmux_pane}", timeout=2)
                else:
                    self.notify("Failed to send", timeout=2)

        self.app.push_screen(PromptModal(self.session.tmux_pane), on_result)

    def action_transfer_context(self) -> None:
        if not self.session.transcript_path:
            self.notify("No transcript for this session", timeout=2)
            return
        sessions = scan_sessions(active_only=True)

        def on_pick(target: Session | None) -> None:
            if target is None:
                return
            if transfer_context(self.session, target):
                src = self.session.name or self.session.tmux_pane
                dst = target.name or target.tmux_pane
                self.notify(f"Context sent: {src} → {dst}", timeout=3)
            else:
                self.notify("Failed to transfer context", timeout=2)

        self.app.push_screen(
            SessionPickerModal(sessions, exclude_id=self.session.session_id),
            on_pick,
        )

    def action_write_journal(self) -> None:
        name = self.session.name or self.session.tmux_pane or "this session"

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                self.notify("Generating journal...", timeout=10)
                self._run_journal()

        self.app.push_screen(ConfirmModal(f"Generate journal for [b]{name}[/b]?"), on_confirm)

    @work(thread=True)
    def _run_journal(self) -> None:
        path = generate_journal(self.session)
        if path:
            self.app.call_from_thread(self.notify, f"Journal: {path}", timeout=5)
        else:
            self.app.call_from_thread(self.notify, "Failed to generate journal", timeout=3)

    def action_goto_session(self) -> None:
        pane = self.session.tmux_pane
        if not pane:
            self.notify("No tmux pane found", timeout=2)
            return
        if not switch_to_tmux_pane(pane):
            self.notify("Failed to switch", timeout=2)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_refresh_detail(self) -> None:
        sessions = scan_sessions(active_only=False)
        found = next((s for s in sessions if s.session_id == self.session.session_id), None)
        if found:
            self.session = found
            self._render_session()

    def action_scroll_down(self) -> None:
        self._get_active_scroll().scroll_down()

    def action_scroll_up(self) -> None:
        self._get_active_scroll().scroll_up()

    def action_scroll_end(self) -> None:
        self._get_active_scroll().scroll_end()

    def action_scroll_start(self) -> None:
        self._get_active_scroll().scroll_home()

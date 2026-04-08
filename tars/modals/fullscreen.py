"""Fullscreen overlay modals for transcript and prompt history."""

from __future__ import annotations

from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label

from tars.helpers import time_ago
from tars.scanner import (
    Session,
    get_session_history,
    parse_transcript_entries,
    tail_transcript,
)
from tars.theme import GRAY, GREEN, ORANGE

CSS = """
FullscreenTranscript, FullscreenPrompts {
    background: rgba(40, 40, 40, 0.9);
    align: center middle;
}

#fs-container {
    width: 92%;
    height: 90%;
    background: $panel;
    border: tall $accent;
    padding: 1 2;
}

#fs-header {
    height: auto;
    padding: 0 0 1 0;
}

#fs-title {
    text-style: bold;
    color: $accent;
    width: 1fr;
}

#fs-hints {
    color: $text-muted;
    width: auto;
}

#fs-live {
    color: $success;
    width: auto;
    padding: 0 2;
}

#fs-scroll {
    height: 1fr;
}

.fs-entry {
    height: auto;
    padding: 0;
}

.fs-time {
    color: $accent;
    width: 12;
}

.fs-content {
    width: 1fr;
}
"""


class FullscreenTranscript(ModalScreen[None]):
    """Fullscreen live transcript view."""

    CSS = CSS
    BINDINGS = [
        Binding("escape", "close", priority=True),
        Binding("q", "close", priority=True),
        Binding("j", "scroll_down", show=False, priority=True),
        Binding("k", "scroll_up", show=False, priority=True),
        Binding("G", "scroll_end", show=False, priority=True),
        Binding("g", "scroll_start", show=False, priority=True),
    ]

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        self._tail_position: int = 0

    def compose(self) -> ComposeResult:
        name = self.session.name or self.session.tmux_pane or self.session.session_id[:12]
        with Vertical(id="fs-container"):
            with Horizontal(id="fs-header"):
                yield Label(f"Transcript — {name}", id="fs-title")
                yield Label("", id="fs-live")
                yield Label("[dim]q[/dim] close  [dim]j/k[/dim] scroll  [dim]G[/dim] end", id="fs-hints")
            yield VerticalScroll(id="fs-scroll")

    def on_mount(self) -> None:
        s = self.session
        if not s.transcript_path:
            return

        entries = parse_transcript_entries(s.transcript_path, last_n=200)
        scroll = self.query_one("#fs-scroll", VerticalScroll)
        widgets = []

        for entry in entries:
            dt = entry.dt
            time_str = dt.strftime("%H:%M:%S") if dt else ""
            row = Horizontal(classes="fs-entry")
            row.compose_add_child(Label(f"[{GRAY}]{time_str}[/{GRAY}]", classes="fs-time"))
            row.compose_add_child(Label(entry.display, classes="fs-content"))
            widgets.append(row)

        scroll.mount_all(widgets)

        try:
            self._tail_position = s.transcript_path.stat().st_size
        except OSError:
            self._tail_position = 0

        if s.is_alive:
            self.query_one("#fs-live", Label).update(f"[{GREEN}]● LIVE[/{GREEN}]")

        scroll.scroll_end(animate=False)
        self.set_interval(1.5, self._poll)

    def _poll(self) -> None:
        s = self.session
        if not s.transcript_path:
            return
        new_entries, new_pos = tail_transcript(s.transcript_path, self._tail_position)
        if not new_entries:
            return
        self._tail_position = new_pos
        scroll = self.query_one("#fs-scroll", VerticalScroll)
        for entry in new_entries:
            dt = entry.dt
            time_str = dt.strftime("%H:%M:%S") if dt else ""
            row = Horizontal(classes="fs-entry")
            row.compose_add_child(Label(f"[{GRAY}]{time_str}[/{GRAY}]", classes="fs-time"))
            row.compose_add_child(Label(entry.display, classes="fs-content"))
            scroll.mount(row)
        scroll.scroll_end(animate=False)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_scroll_down(self) -> None:
        self.query_one("#fs-scroll", VerticalScroll).scroll_down()

    def action_scroll_up(self) -> None:
        self.query_one("#fs-scroll", VerticalScroll).scroll_up()

    def action_scroll_end(self) -> None:
        self.query_one("#fs-scroll", VerticalScroll).scroll_end()

    def action_scroll_start(self) -> None:
        self.query_one("#fs-scroll", VerticalScroll).scroll_home()


class FullscreenPrompts(ModalScreen[None]):
    """Fullscreen prompt history view."""

    CSS = CSS
    BINDINGS = [
        Binding("escape", "close", priority=True),
        Binding("q", "close", priority=True),
        Binding("j", "scroll_down", show=False, priority=True),
        Binding("k", "scroll_up", show=False, priority=True),
        Binding("G", "scroll_end", show=False, priority=True),
        Binding("g", "scroll_start", show=False, priority=True),
    ]

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        name = self.session.name or self.session.tmux_pane or self.session.session_id[:12]
        with Vertical(id="fs-container"):
            with Horizontal(id="fs-header"):
                yield Label(f"Prompts — {name}", id="fs-title")
                yield Label("[dim]q[/dim] close  [dim]j/k[/dim] scroll", id="fs-hints")
            yield VerticalScroll(id="fs-scroll")

    def on_mount(self) -> None:
        history = get_session_history(self.session.session_id, limit=100)
        scroll = self.query_one("#fs-scroll", VerticalScroll)

        if not history:
            scroll.mount(Label(f"[{GRAY}]No prompts recorded[/{GRAY}]"))
            return

        widgets = []
        for entry in history:
            ts = entry.get("timestamp", 0)
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None
            time_str = dt.strftime("%H:%M:%S") if dt else "—"
            text = entry.get("display", "")
            row = Horizontal(classes="fs-entry")
            row.compose_add_child(Label(f"[{ORANGE}]{time_str}[/{ORANGE}]", classes="fs-time"))
            row.compose_add_child(Label(text, classes="fs-content"))
            widgets.append(row)

        scroll.mount_all(widgets)
        scroll.scroll_end(animate=False)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_scroll_down(self) -> None:
        self.query_one("#fs-scroll", VerticalScroll).scroll_down()

    def action_scroll_up(self) -> None:
        self.query_one("#fs-scroll", VerticalScroll).scroll_up()

    def action_scroll_end(self) -> None:
        self.query_one("#fs-scroll", VerticalScroll).scroll_end()

    def action_scroll_start(self) -> None:
        self.query_one("#fs-scroll", VerticalScroll).scroll_home()

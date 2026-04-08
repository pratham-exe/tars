"""Session picker modal — pick a target session for context transfer."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label

from tars.helpers import time_ago
from tars.scanner.models import Session
from tars.theme import GRAY, GREEN

CSS = """
SessionPickerModal {
    background: rgba(40, 40, 40, 0.85);
    align: center middle;
}

#picker-dialog {
    width: 60%;
    max-width: 60;
    height: 18;
    background: $panel;
    border: tall $accent;
    padding: 1 2;
}

#picker-title {
    text-style: bold;
    color: $accent;
    text-align: center;
    padding-bottom: 1;
}

#picker-scroll {
    height: 1fr;
}

.picker-item {
    height: 1;
    padding: 0 1;
}

.picker-item.selected {
    background: $boost;
    color: $accent;
    text-style: bold;
}

#picker-hint {
    text-align: center;
    color: $text-muted;
    padding-top: 1;
}
"""


class SessionPickerModal(ModalScreen[Session | None]):
    CSS = CSS
    BINDINGS = [
        Binding("j", "down", priority=True),
        Binding("k", "up", priority=True),
        Binding("enter", "select", priority=True),
        Binding("escape", "cancel", priority=True),
    ]

    def __init__(self, sessions: list[Session], exclude_id: str = "") -> None:
        super().__init__()
        self._sessions = [s for s in sessions if s.session_id != exclude_id and s.is_alive and s.tmux_pane]
        self._cursor = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Label("Transfer context to:", id="picker-title")
            yield VerticalScroll(id="picker-scroll")
            yield Label("[dim]j/k[/dim] navigate  [dim]enter[/dim] select  [dim]esc[/dim] cancel", id="picker-hint")

    def on_mount(self) -> None:
        scroll = self.query_one("#picker-scroll", VerticalScroll)
        if not self._sessions:
            scroll.mount(Label(f"[{GRAY}]No other active sessions[/{GRAY}]"))
            return
        widgets = []
        for i, s in enumerate(self._sessions):
            name = s.name or s.project_name
            label = f"{s.tmux_pane}  {name}  ({s.duration_display})"
            item = Label(label, classes="picker-item")
            if i == 0:
                item.add_class("selected")
            widgets.append(item)
        scroll.mount_all(widgets)

    def _update_cursor(self) -> None:
        items = list(self.query(".picker-item"))
        for i, item in enumerate(items):
            if i == self._cursor:
                item.add_class("selected")
            else:
                item.remove_class("selected")
        if 0 <= self._cursor < len(items):
            items[self._cursor].scroll_visible()

    def action_down(self) -> None:
        if self._cursor < len(self._sessions) - 1:
            self._cursor += 1
            self._update_cursor()

    def action_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._update_cursor()

    def action_select(self) -> None:
        if self._sessions and 0 <= self._cursor < len(self._sessions):
            self.dismiss(self._sessions[self._cursor])
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

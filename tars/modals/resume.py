"""Resume picker modal — browse and resume historical sessions."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label

from tars.helpers import escape_markup, time_ago
from tars.scanner.models import HistoricalSession
from tars.theme import GRAY, GREEN, ORANGE

CSS = """
ResumePickerModal {
    background: rgba(40, 40, 40, 0.85);
    align: center middle;
}

#resume-dialog {
    width: 80%;
    max-width: 80;
    height: 22;
    background: $panel;
    border: tall $accent;
    padding: 1 2;
}

#resume-title {
    text-style: bold;
    color: $accent;
    text-align: center;
    padding-bottom: 1;
}

#resume-scroll {
    height: 1fr;
}

.resume-item {
    height: auto;
    padding: 0 1;
}

.resume-item.selected {
    background: $boost;
    color: $accent;
    text-style: bold;
}

.resume-item.is-alive {
    color: $text-muted;
}

#resume-hint {
    text-align: center;
    color: $text-muted;
    padding-top: 1;
}
"""


class ResumePickerModal(ModalScreen[HistoricalSession | None]):
    CSS = CSS
    BINDINGS = [
        Binding("j", "down", priority=True),
        Binding("k", "up", priority=True),
        Binding("enter", "select", priority=True),
        Binding("escape", "cancel", priority=True),
    ]

    def __init__(self, sessions: list[HistoricalSession]) -> None:
        super().__init__()
        self._sessions = sessions
        self._cursor = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="resume-dialog"):
            yield Label(f"[{ORANGE}][b]Resume Session[/b][/{ORANGE}]", id="resume-title")
            yield VerticalScroll(id="resume-scroll")
            yield Label("[dim]j/k[/dim] navigate  [dim]enter[/dim] resume  [dim]esc[/dim] cancel", id="resume-hint")

    def on_mount(self) -> None:
        scroll = self.query_one("#resume-scroll", VerticalScroll)
        if not self._sessions:
            scroll.mount(Label(f"[{GRAY}]No sessions found[/{GRAY}]"))
            return
        widgets = []
        for i, s in enumerate(self._sessions):
            name = s.name or s.first_prompt or s.session_id[:12]
            name = escape_markup(name.replace("\n", " "))[:50]
            if s.is_alive:
                label = f"[{GREEN}]●[/{GREEN}] {name}  [{GRAY}](alive)[/{GRAY}]"
            else:
                ago = time_ago(s.modified)
                label = f"[{GRAY}]●[/{GRAY}] {name}  [{GRAY}]{ago} · {s.line_count} lines[/{GRAY}]"
            item = Label(label, classes="resume-item")
            if s.is_alive:
                item.add_class("is-alive")
            if i == 0:
                item.add_class("selected")
            widgets.append(item)
        scroll.mount_all(widgets)

    def _update_cursor(self) -> None:
        items = list(self.query(".resume-item"))
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

"""Rename session modal."""

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label

from tars.theme import ORANGE

CSS = """
RenameModal {
    background: rgba(40, 40, 40, 0.85);
    align: center middle;
}

#rename-dialog {
    width: 60%;
    max-width: 54;
    height: 9;
    background: $panel;
    border: solid $accent;
    padding: 1 2;
}

#rename-title {
    text-style: bold;
    color: $accent;
    text-align: center;
    padding-bottom: 1;
}

#rename-input {
    width: 1fr;
}

#rename-hint {
    text-align: center;
    color: $text-muted;
    padding-top: 1;
}
"""


class RenameModal(ModalScreen[str | None]):
    CSS = CSS
    BINDINGS = [
        Binding("escape", "cancel", priority=True),
    ]

    def __init__(self, current_name: str = "") -> None:
        super().__init__()
        self._current_name = current_name

    def compose(self) -> ComposeResult:
        with Vertical(id="rename-dialog"):
            yield Label(f"[{ORANGE}][b]Rename Session[/b][/{ORANGE}]", id="rename-title")
            yield Input(value=self._current_name, placeholder="New name", id="rename-input")
            yield Label("[dim]enter[/dim] rename  [dim]esc[/dim] cancel", id="rename-hint")

    def on_mount(self) -> None:
        inp = self.query_one("#rename-input", Input)
        inp.focus()
        # Select all text so you can just type to replace
        inp.action_select_all()

    @on(Input.Submitted, "#rename-input")
    def on_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)

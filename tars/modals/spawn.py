"""Spawn session modal — names a new Jarvis session."""

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label

CSS = """
SpawnModal {
    background: rgba(40, 40, 40, 0.85);
    align: center middle;
}

#spawn-dialog {
    width: 60%;
    max-width: 54;
    height: 9;
    background: $panel;
    border: solid $accent;
    padding: 1 2;
}

#spawn-title {
    text-style: bold;
    color: $accent;
    text-align: center;
    padding-bottom: 1;
}

#spawn-input {
    width: 1fr;
}

#spawn-hint {
    text-align: center;
    color: $text-muted;
    padding-top: 1;
}
"""


class SpawnModal(ModalScreen[str | None]):
    CSS = CSS
    BINDINGS = [
        Binding("escape", "cancel", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="spawn-dialog"):
            yield Label("Spawn new [b]Jarvis[/b] session", id="spawn-title")
            yield Input(placeholder="Session name (optional)", id="spawn-input")
            yield Label("[dim]enter[/dim] spawn  [dim]esc[/dim] cancel", id="spawn-hint")

    def on_mount(self) -> None:
        self.query_one("#spawn-input", Input).focus()

    @on(Input.Submitted, "#spawn-input")
    def on_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)

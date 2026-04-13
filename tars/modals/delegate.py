"""Task delegation modal — describe a task for TARS to break down and spawn agents."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, TextArea

from tars.theme import ORANGE

CSS = """
DelegateModal {
    background: rgba(40, 40, 40, 0.85);
    align: center middle;
}

#delegate-dialog {
    width: 70%;
    max-width: 80;
    height: 16;
    background: $panel;
    border: tall $accent;
    padding: 1 2;
}

#delegate-title {
    text-style: bold;
    color: $accent;
    text-align: center;
    padding-bottom: 1;
}

#delegate-editor {
    height: 1fr;
    width: 1fr;
    background: $surface;
}

#delegate-editor .text-area--cursor-line {
    background: $surface;
}

#delegate-hint {
    text-align: center;
    color: $text-muted;
    padding-top: 1;
}
"""


class DelegateModal(ModalScreen[str | None]):
    CSS = CSS
    BINDINGS = [
        Binding("escape", "cancel", priority=True),
        Binding("ctrl+d", "send", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="delegate-dialog"):
            yield Label(f"[{ORANGE}][b]Delegate Task[/b][/{ORANGE}]", id="delegate-title")
            yield TextArea(id="delegate-editor")
            yield Label("[dim]ctrl+d[/dim] delegate  [dim]esc[/dim] cancel", id="delegate-hint")

    def on_mount(self) -> None:
        self.query_one("#delegate-editor", TextArea).focus()

    def action_send(self) -> None:
        self.dismiss(self.query_one("#delegate-editor", TextArea).text.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)

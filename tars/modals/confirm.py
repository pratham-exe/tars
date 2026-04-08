"""Generic confirmation modal."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label

CSS = """
ConfirmModal {
    background: rgba(40, 40, 40, 0.85);
    align: center middle;
}

#modal-dialog {
    width: 60%;
    max-width: 54;
    height: 7;
    background: $panel;
    border: solid $accent;
    padding: 1 2;
}

#modal-text {
    text-align: center;
    padding-bottom: 1;
}

#modal-hint {
    text-align: center;
    color: $text-muted;
}
"""


class ConfirmModal(ModalScreen[bool]):
    CSS = CSS
    BINDINGS = [
        Binding("y", "confirm", priority=True),
        Binding("n", "cancel", priority=True),
        Binding("escape", "cancel", priority=True),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Label(self._message, id="modal-text")
            yield Label("[b]y[/b] yes  [b]n[/b] no", id="modal-hint")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

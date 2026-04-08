"""Prompt modal for sending messages to sessions via tmux send-keys."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, TextArea

from tars.theme import ORANGE

CSS = """
PromptModal {
    background: rgba(40, 40, 40, 0.85);
    align: center middle;
}

#prompt-dialog {
    width: 70%;
    max-width: 80;
    height: 18;
    background: $panel;
    border: tall $accent;
    padding: 1 2;
}

#prompt-title {
    text-style: bold;
    color: $accent;
    padding-bottom: 1;
    text-align: center;
}

#prompt-editor {
    height: 1fr;
    width: 1fr;
    background: $surface;
}

#prompt-editor .text-area--cursor-line {
    background: $surface;
}

#prompt-hint {
    color: $text-muted;
    text-align: center;
    padding-top: 1;
}
"""


class PromptModal(ModalScreen[str]):
    CSS = CSS
    BINDINGS = [
        Binding("escape", "cancel", priority=True),
        Binding("ctrl+s", "send", priority=True),
    ]

    def __init__(self, pane: str) -> None:
        super().__init__()
        self._pane = pane

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-dialog"):
            yield Label(f"Send to [{ORANGE}][b]{self._pane}[/b][/{ORANGE}]", id="prompt-title")
            yield TextArea(id="prompt-editor")
            yield Label("[dim]ctrl+s[/dim] send  [dim]esc[/dim] cancel", id="prompt-hint")

    def on_mount(self) -> None:
        self.query_one("#prompt-editor", TextArea).focus()

    def action_send(self) -> None:
        self.dismiss(self.query_one("#prompt-editor", TextArea).text.strip())

    def action_cancel(self) -> None:
        self.dismiss("")

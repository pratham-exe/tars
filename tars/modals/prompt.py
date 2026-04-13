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


# Per-session draft storage
_drafts: dict[str, str] = {}


class PromptModal(ModalScreen[str]):
    """Prompt modal with per-session draft persistence.
    Returns the text to send, or None if cancelled (draft saved)."""

    CSS = CSS
    BINDINGS = [
        Binding("escape", "cancel", priority=True),
        Binding("ctrl+d", "send", priority=True),
    ]

    def __init__(self, pane: str) -> None:
        super().__init__()
        self._pane = pane

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-dialog"):
            yield Label(f"Send to [{ORANGE}][b]{self._pane}[/b][/{ORANGE}]", id="prompt-title")
            yield TextArea(id="prompt-editor")
            yield Label("[dim]ctrl+d[/dim] send  [dim]esc[/dim] cancel (draft saved)", id="prompt-hint")

    def on_mount(self) -> None:
        editor = self.query_one("#prompt-editor", TextArea)
        # Restore draft if one exists
        draft = _drafts.get(self._pane, "")
        if draft:
            editor.load_text(draft)
        editor.focus()

    def action_send(self) -> None:
        text = self.query_one("#prompt-editor", TextArea).text.strip()
        # Clear draft on send
        _drafts.pop(self._pane, None)
        self.dismiss(text)

    def action_cancel(self) -> None:
        text = self.query_one("#prompt-editor", TextArea).text
        if text.strip():
            _drafts[self._pane] = text
        else:
            # Cleared the text — remove the draft too
            _drafts.pop(self._pane, None)
        self.dismiss("")

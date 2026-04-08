"""TARS — Terminal Agent Runtime Scanner. A Textual TUI for monitoring Claude Code sessions."""

from __future__ import annotations

import signal
from datetime import datetime, timezone
from itertools import groupby

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.design import ColorSystem
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Footer,
    Input,
    Label,
    Static,
    TextArea,
)

from tars.scanner import (
    HistoricalSession,
    Session,
    delegate_task,
    generate_journal,
    get_session_history,
    list_resumable_sessions,
    parse_transcript_entries,
    resume_session_in_tmux,
    scan_sessions,
    send_keys_to_tmux,
    spawn_session_in_tmux,
    tail_transcript,
    transfer_context,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _time_ago(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    now = datetime.now(timezone.utc)
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _format_started(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    if dt.date() == now.date():
        return f"today {dt.strftime('%H:%M')}"
    diff = (now.date() - dt.date()).days
    if diff == 1:
        return f"yesterday {dt.strftime('%H:%M')}"
    return dt.strftime("%b %d %H:%M")


def _truncate(text: str, length: int = 60) -> str:
    text = text.replace("\n", " ").strip()
    return text[:length] + "…" if len(text) > length else text


def _escape(text: str) -> str:
    """Escape square brackets for rich markup."""
    return text.replace("[", "\\[").replace("]", "\\]")


TARS_ASCII = r"""
 ████████╗ █████╗ ██████╗ ███████╗
 ╚══██╔══╝██╔══██╗██╔══██╗██╔════╝
    ██║   ███████║██████╔╝███████╗
    ██║   ██╔══██║██╔══██╗╚════██║
    ██║   ██║  ██║██║  ██║███████║
    ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝
"""

# ── Gruvbox ──────────────────────────────────────────────────────────────────

GRUVBOX = {
    "dark": ColorSystem(
        primary="#d79921",
        secondary="#458588",
        accent="#fe8019",
        warning="#d79921",
        error="#cc241d",
        success="#98971a",
        background="#282828",
        surface="#3c3836",
        panel="#504945",
        boost="#665c54",
        dark=True,
    ),
    "light": ColorSystem(
        primary="#d79921",
        secondary="#458588",
        accent="#fe8019",
        warning="#d79921",
        error="#cc241d",
        success="#98971a",
        background="#fbf1c7",
        surface="#ebdbb2",
        panel="#d5c4a1",
        boost="#bdae93",
        dark=False,
    ),
}

GRV_GREEN = "#b8bb26"
GRV_RED = "#fb4934"
GRV_YELLOW = "#fabd2f"
GRV_BLUE = "#83a598"
GRV_ORANGE = "#fe8019"
GRV_AQUA = "#8ec07c"
GRV_PURPLE = "#d3869b"
GRV_FG = "#ebdbb2"
GRV_GRAY = "#928374"


# ═════════════════════════════════════════════════════════════════════════════
# Confirm Modal (reusable)
# ═════════════════════════════════════════════════════════════════════════════

MODAL_CSS = """
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
    CSS = MODAL_CSS
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


PROMPT_CSS = """
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
    """Floating modal with multiline editor — background stays visible."""

    CSS = PROMPT_CSS
    BINDINGS = [
        Binding("escape", "cancel", priority=True),
        Binding("ctrl+s", "send", priority=True),
    ]

    def __init__(self, pane: str) -> None:
        super().__init__()
        self._pane = pane

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-dialog"):
            yield Label(f"Send to [{GRV_ORANGE}][b]{self._pane}[/b][/{GRV_ORANGE}]", id="prompt-title")
            yield TextArea(id="prompt-editor")
            yield Label("[dim]ctrl+s[/dim] send  [dim]esc[/dim] cancel", id="prompt-hint")

    def on_mount(self) -> None:
        self.query_one("#prompt-editor", TextArea).focus()

    def action_send(self) -> None:
        text = self.query_one("#prompt-editor", TextArea).text.strip()
        self.dismiss(text)

    def action_cancel(self) -> None:
        self.dismiss("")


SPAWN_CSS = """
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
    """Modal to name and spawn a new Jarvis session."""

    CSS = SPAWN_CSS
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


# ═════════════════════════════════════════════════════════════════════════════
# Session Picker Modal (for context transfer)
# ═════════════════════════════════════════════════════════════════════════════

PICKER_CSS = """
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
    """Modal to pick a target session."""

    CSS = PICKER_CSS
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
            scroll.mount(Label(f"[{GRV_GRAY}]No other active sessions[/{GRV_GRAY}]"))
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


# ═════════════════════════════════════════════════════════════════════════════
# Resume Picker Modal
# ═════════════════════════════════════════════════════════════════════════════

RESUME_CSS = """
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
    """Modal to pick a session to resume."""

    CSS = RESUME_CSS
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
            yield Label(f"[{GRV_ORANGE}][b]Resume Session[/b][/{GRV_ORANGE}]", id="resume-title")
            yield VerticalScroll(id="resume-scroll")
            yield Label("[dim]j/k[/dim] navigate  [dim]enter[/dim] resume  [dim]esc[/dim] cancel", id="resume-hint")

    def on_mount(self) -> None:
        scroll = self.query_one("#resume-scroll", VerticalScroll)
        if not self._sessions:
            scroll.mount(Label(f"[{GRV_GRAY}]No sessions found[/{GRV_GRAY}]"))
            return
        widgets = []
        for i, s in enumerate(self._sessions):
            name = s.name or s.first_prompt or s.session_id[:12]
            name = _escape(name.replace("\n", " "))[:50]
            if s.is_alive:
                label = f"[{GRV_GREEN}]●[/{GRV_GREEN}] {name}  [{GRV_GRAY}](alive)[/{GRV_GRAY}]"
            else:
                ago = _time_ago(s.modified)
                label = f"[{GRV_GRAY}]●[/{GRV_GRAY}] {name}  [{GRV_GRAY}]{ago} · {s.line_count} lines[/{GRV_GRAY}]"
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


# ═════════════════════════════════════════════════════════════════════════════
# Task Delegation Modal
# ═════════════════════════════════════════════════════════════════════════════

DELEGATE_CSS = """
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
    """Modal to describe a task for delegation to multiple sessions."""

    CSS = DELEGATE_CSS
    BINDINGS = [
        Binding("escape", "cancel", priority=True),
        Binding("ctrl+s", "send", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="delegate-dialog"):
            yield Label(f"[{GRV_ORANGE}][b]Delegate Task[/b][/{GRV_ORANGE}]", id="delegate-title")
            yield TextArea(id="delegate-editor")
            yield Label("[dim]ctrl+s[/dim] delegate  [dim]esc[/dim] cancel", id="delegate-hint")

    def on_mount(self) -> None:
        self.query_one("#delegate-editor", TextArea).focus()

    def action_send(self) -> None:
        text = self.query_one("#delegate-editor", TextArea).text.strip()
        self.dismiss(text)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ═════════════════════════════════════════════════════════════════════════════
# Detail Screen — live transcript + tmux send-keys interaction
# ═════════════════════════════════════════════════════════════════════════════

DETAIL_CSS = """
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

DETAIL_SECTIONS = ["history", "transcript"]


class DetailScreen(Screen):
    """Full-screen detail view with live transcript tail and tmux interaction."""

    CSS = DETAIL_CSS

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
        self._focused_section: int = 1  # 0=history, 1=transcript

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
            status = f"[{GRV_GREEN}]● ACTIVE[/{GRV_GREEN}]"
        elif s.is_alive:
            status = f"[{GRV_YELLOW}]● IDLE[/{GRV_YELLOW}]"
        else:
            status = f"[{GRV_RED}]● DEAD[/{GRV_RED}]"

        tmux = f"[b]Tmux:[/b] {s.tmux_pane}        " if s.tmux_pane else ""

        info = (
            f"[b]Status:[/b]     {status}        "
            f"[b]PID:[/b] {s.pid}        "
            f"{tmux}"
            f"[b]Duration:[/b] {s.duration_display}\n"
            f"[b]Directory:[/b]  {s.cwd}\n"
            f"[b]Session ID:[/b] {s.session_id}\n"
            f"[b]Started:[/b]    {s.started_at.strftime('%Y-%m-%d %H:%M:%S')} ({_time_ago(s.started_at)})        "
            f"[b]Messages:[/b] {s.message_count}        "
            f"[b]Tools:[/b] {s.tool_count}"
        )
        self.query_one("#detail-info", Static).update(info)

        history = get_session_history(s.session_id)
        scroll = self.query_one("#history-scroll", VerticalScroll)
        scroll.remove_children()
        if not history:
            scroll.mount(Label(f"[{GRV_GRAY}]No prompts recorded[/{GRV_GRAY}]"))
        else:
            for entry in history:
                ts = entry.get("timestamp", 0)
                dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None
                time_str = dt.strftime("%H:%M:%S") if dt else "—"
                text = entry.get("display", "")
                row = Horizontal(classes="history-entry")
                row.compose_add_child(Label(f"[{GRV_ORANGE}]{time_str}[/{GRV_ORANGE}]", classes="history-time"))
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
            row.compose_add_child(Label(f"[{GRV_GRAY}]{time_str}[/{GRV_GRAY}]", classes="ts-time"))
            row.compose_add_child(Label(entry.display, classes="ts-content"))
            scroll.mount(row)

        try:
            self._tail_position = s.transcript_path.stat().st_size
        except OSError:
            self._tail_position = 0

        if s.is_alive:
            self.query_one("#transcript-live", Label).update(f"[{GRV_GREEN}]● LIVE[/{GRV_GREEN}]")

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
            row.compose_add_child(Label(f"[{GRV_GRAY}]{time_str}[/{GRV_GRAY}]", classes="ts-time"))
            row.compose_add_child(Label(entry.display, classes="ts-content"))
            scroll.mount(row)

        scroll.scroll_end(animate=False)

    # ── Section focus ────────────────────────────────────────────────────

    def _get_active_scroll(self) -> VerticalScroll:
        section = DETAIL_SECTIONS[self._focused_section]
        if section == "history":
            return self.query_one("#history-scroll", VerticalScroll)
        return self.query_one("#transcript-scroll", VerticalScroll)

    def _update_section_focus(self) -> None:
        for i, name in enumerate(DETAIL_SECTIONS):
            el = self.query_one(f"#{name}-section")
            if i == self._focused_section:
                el.add_class("focused-section")
            else:
                el.remove_class("focused-section")

    def action_focus_next_section(self) -> None:
        self._focused_section = (self._focused_section + 1) % len(DETAIL_SECTIONS)
        self._update_section_focus()

    def action_focus_prev_section(self) -> None:
        self._focused_section = (self._focused_section - 1) % len(DETAIL_SECTIONS)
        self._update_section_focus()

    # ── Prompt (tmux send-keys via modal) ────────────────────────────────

    def action_open_prompt(self) -> None:
        if not self.session.is_alive:
            self.notify("Session is dead", timeout=2)
            return
        if not self.session.tmux_pane:
            self.notify("No tmux pane found", timeout=2)
            return

        def on_result(text: str) -> None:
            if text:
                pane = self.session.tmux_pane
                if send_keys_to_tmux(pane, text):
                    self.notify(f"Sent to {pane}", timeout=2)
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
                source_name = self.session.name or self.session.tmux_pane
                target_name = target.name or target.tmux_pane
                self.notify(f"Context sent: {source_name} → {target_name}", timeout=3)
            else:
                self.notify("Failed to transfer context", timeout=2)

        self.app.push_screen(
            SessionPickerModal(sessions, exclude_id=self.session.session_id),
            on_pick,
        )

    def action_write_journal(self) -> None:
        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                self.notify("Generating journal...", timeout=10)
                self._run_journal()

        name = self.session.name or self.session.tmux_pane or "this session"
        self.app.push_screen(
            ConfirmModal(f"Generate journal for [b]{name}[/b]?"),
            on_confirm,
        )

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
        from tars.scanner import switch_to_tmux_pane
        if not switch_to_tmux_pane(pane):
            self.notify("Failed to switch", timeout=2)

    # ── Scroll & nav ─────────────────────────────────────────────────────

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_refresh_detail(self) -> None:
        sessions = scan_sessions(active_only=False)
        found = next(
            (s for s in sessions if s.session_id == self.session.session_id), None
        )
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


# ═════════════════════════════════════════════════════════════════════════════
# Home Screen
# ═════════════════════════════════════════════════════════════════════════════

HOME_CSS = """
Screen {
    background: $surface;
    overflow: hidden;
}

#main-container {
    height: 1fr;
}

#top-section {
    height: auto;
}

#banner {
    color: $accent;
    text-align: center;
    padding: 0 1;
    height: auto;
    text-style: bold;
}

#subtitle {
    color: $text-muted;
    text-align: center;
    padding: 0 1 0 1;
    height: auto;
}

#session-scroll {
    height: 1fr;
    padding: 0 1;
}

.session-card {
    height: auto;
    padding: 1 2;
    margin: 0 0 1 0;
    background: $panel;
}

.session-card.selected {
    background: $boost;
    border-left: tall $accent;
}

.session-card.alive-active {
    border-left: tall $success;
}

.session-card.alive-active.selected {
    border-left: tall $accent;
}

.group-header {
    height: 1;
    padding: 0 1;
    margin: 1 0 0 0;
    color: $accent;
    text-style: bold;
}

#filter-bar {
    display: none;
    dock: bottom;
    height: 3;
    padding: 0 1;
    background: $panel;
}

#filter-bar.visible {
    display: block;
}

#filter-input {
    width: 1fr;
}
"""


class HomeScreen(Screen):
    """Main screen with card-based session list."""

    CSS = HOME_CSS

    BINDINGS = [
        Binding("q", "exit_app", "Quit", priority=True),
        Binding("r", "refresh", "Refresh", priority=True),
        Binding("n", "spawn_session", "New", priority=True),
        Binding("a", "resume_session", "Resume", priority=True),
        Binding("d", "delegate_task", "Delegate", priority=True),
        Binding("x", "kill_session", "Kill", priority=True),
        Binding("slash", "open_filter", "/Search", priority=True),
        Binding("j", "cursor_down", show=False, priority=True),
        Binding("k", "cursor_up", show=False, priority=True),
        Binding("enter", "open_detail", show=False, priority=True),
        Binding("escape", "close_filter", show=False, priority=True),
    ]

    sessions: list[Session] = []
    filter_text: reactive[str] = reactive("")

    def __init__(self) -> None:
        super().__init__()
        self._cursor: int = 0
        self._visible_session_ids: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="main-container"):
            with Vertical(id="top-section"):
                yield Static(TARS_ASCII, id="banner")
                yield Label(
                    '"Everybody good? Plenty of slaves for my robot colony?" — TARS, Interstellar',
                    id="subtitle",
                )
            yield VerticalScroll(id="session-scroll")
            with Horizontal(id="filter-bar"):
                yield Input(placeholder="Filter sessions...", id="filter-input")
        yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        self._do_refresh()
        self.set_interval(3, self._do_refresh)

    # ── Cursor / selection ───────────────────────────────────────────────

    def _get_selected_session(self) -> Session | None:
        if not self._visible_session_ids or self._cursor >= len(self._visible_session_ids):
            return None
        sid = self._visible_session_ids[self._cursor]
        return next((s for s in self.sessions if s.session_id == sid), None)

    def action_cursor_down(self) -> None:
        if self._cursor < len(self._visible_session_ids) - 1:
            self._cursor += 1
            self._update_selection()

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._update_selection()

    def _update_selection(self) -> None:
        scroll = self.query_one("#session-scroll", VerticalScroll)
        cards = list(scroll.query(".session-card"))
        for i, card in enumerate(cards):
            if i == self._cursor:
                card.add_class("selected")
            else:
                card.remove_class("selected")
        if 0 <= self._cursor < len(cards):
            cards[self._cursor].scroll_visible()

    def action_open_detail(self) -> None:
        session = self._get_selected_session()
        if session:
            self.app.push_screen(DetailScreen(session))

    # ── Data refresh ─────────────────────────────────────────────────────

    @work(thread=True)
    def _do_refresh(self) -> None:
        sessions = scan_sessions(active_only=False)
        self.app.call_from_thread(self._apply_refresh, sessions)

    def _apply_refresh(self, sessions: list[Session]) -> None:
        self.sessions = sessions
        self._render_cards()

    def _render_cards(self) -> None:
        scroll = self.query_one("#session-scroll", VerticalScroll)
        scroll.remove_children()

        filtered = self._get_filtered_sorted_sessions()
        self._visible_session_ids = [s.session_id for s in filtered]

        if self._cursor >= len(self._visible_session_ids):
            self._cursor = max(0, len(self._visible_session_ids) - 1)

        if not filtered:
            scroll.mount(Label(f"\n  [{GRV_GRAY}]No sessions found[/{GRV_GRAY}]"))
            return

        def tmux_key(s: Session) -> str:
            return s.tmux_pane.split(":")[0] if s.tmux_pane else "other"

        grouped = groupby(filtered, key=tmux_key)
        card_idx = 0
        widgets = []

        for group_name, group_sessions in grouped:
            sessions_list = list(group_sessions)

            widgets.append(
                Label(
                    f"[{GRV_AQUA}]▎[/{GRV_AQUA}] [{GRV_AQUA}]{group_name.upper()}[/{GRV_AQUA}]  [{GRV_GRAY}]({len(sessions_list)})[/{GRV_GRAY}]",
                    classes="group-header",
                )
            )

            for session in sessions_list:
                is_selected = card_idx == self._cursor

                if session.is_alive and session.is_recently_active:
                    dot = f"[{GRV_GREEN}]⦿ [/{GRV_GREEN}]"
                elif session.is_alive:
                    dot = f"[{GRV_YELLOW}]● [/{GRV_YELLOW}]"
                else:
                    dot = f"[{GRV_RED}]● [/{GRV_RED}]"

                tmux = session.tmux_pane or "—"
                activity = _escape(_truncate(session.last_activity, 50)) if session.last_activity else f"[{GRV_GRAY}]no recent activity[/{GRV_GRAY}]"
                active_ago = _time_ago(session.last_activity_time)

                name_part = f"  [{GRV_FG}][b]{_escape(session.name)}[/b][/{GRV_FG}]" if session.name else ""

                # Status label
                if session.is_alive and session.is_recently_active:
                    status_label = f"[{GRV_GREEN}]ACTIVE[/{GRV_GREEN}]"
                elif session.is_alive:
                    status_label = f"[{GRV_YELLOW}]IDLE[/{GRV_YELLOW}]"
                else:
                    status_label = f"[{GRV_RED}]DEAD[/{GRV_RED}]"

                line1 = (
                    f"{dot}"
                    f"[{GRV_ORANGE}][b]{tmux}[/b][/{GRV_ORANGE}]"
                    f"{name_part}  "
                    f"{status_label}  "
                    f"[{GRV_FG}]{session.duration_display}[/{GRV_FG}]  "
                    f"[{GRV_GRAY}]{_format_started(session.started_at)}[/{GRV_GRAY}]"
                )
                line2 = (
                    f"  [{GRV_BLUE}]{session.message_count} msgs[/{GRV_BLUE}]  "
                    f"[{GRV_PURPLE}]{session.tool_count} tools[/{GRV_PURPLE}]  "
                    f"[{GRV_GRAY}]active {active_ago}[/{GRV_GRAY}]"
                )
                line3 = (
                    f"  [{GRV_GRAY}]{activity}[/{GRV_GRAY}]"
                )

                card = Static(f"{line1}\n{line2}\n{line3}", classes="session-card")
                if is_selected:
                    card.add_class("selected")
                if session.is_alive and session.is_recently_active:
                    card.add_class("alive-active")

                widgets.append(card)
                card_idx += 1

        scroll.mount_all(widgets)

    def _get_filtered_sorted_sessions(self) -> list[Session]:
        # Only show sessions with a tmux pane (interactable)
        sessions = [s for s in self.sessions if s.tmux_pane]

        if self.filter_text:
            ft = self.filter_text.lower()
            sessions = [
                s for s in sessions
                if ft in s.project_name.lower()
                or ft in s.name.lower()
                or ft in s.tmux_pane.lower()
                or ft in s.session_id.lower()
                or ft in str(s.pid)
            ]

        sessions.sort(key=lambda s: s.started_at, reverse=True)
        return sessions

    # ── Actions ──────────────────────────────────────────────────────────

    def action_exit_app(self) -> None:
        self.app.exit()

    def action_refresh(self) -> None:
        self._do_refresh()

    def action_open_filter(self) -> None:
        self.query_one("#filter-bar").add_class("visible")
        self.query_one("#filter-input", Input).focus()

    def action_close_filter(self) -> None:
        bar = self.query_one("#filter-bar")
        if bar.has_class("visible"):
            bar.remove_class("visible")
            self.filter_text = ""
            self.query_one("#filter-input", Input).value = ""
            self._render_cards()

    @on(Input.Changed, "#filter-input")
    def on_filter_changed(self, event: Input.Changed) -> None:
        self.filter_text = event.value
        self._render_cards()

    @on(Input.Submitted, "#filter-input")
    def on_filter_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#filter-bar").remove_class("visible")

    def action_kill_session(self) -> None:
        session = self._get_selected_session()
        if not session:
            return
        if not session.is_alive:
            self.notify("Session already dead", timeout=2)
            return
        name = session.name or session.tmux_pane or str(session.pid)

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                try:
                    import os
                    os.kill(session.pid, signal.SIGTERM)
                    # Also close the tmux window
                    if session.tmux_pane:
                        import subprocess
                        subprocess.run(
                            ["tmux", "kill-window", "-t", session.tmux_pane],
                            capture_output=True, timeout=3,
                        )
                    self.notify(f"Killed: {name}", timeout=2)
                    self._do_refresh()
                except (ProcessLookupError, PermissionError):
                    self.notify("Failed to kill", timeout=2)

        self.app.push_screen(
            ConfirmModal(f"Kill session [b]{name}[/b] (PID {session.pid})?"),
            on_confirm,
        )

    def action_spawn_session(self) -> None:
        cwd = self.sessions[0].cwd if self.sessions else None

        def on_result(name: str | None) -> None:
            if name is None:
                return
            session_name = name.strip() or None
            if spawn_session_in_tmux(cwd, name=session_name):
                label = f"'{session_name}'" if session_name else ""
                self.notify(f"Spawned new Jarvis session {label}", timeout=2)
                self.set_timer(2, self._do_refresh)
            else:
                self.notify("Failed to spawn", timeout=2)

        self.app.push_screen(SpawnModal(), on_result)

    def action_delegate_task(self) -> None:
        cwd = self.sessions[0].cwd if self.sessions else None

        def on_result(task: str | None) -> None:
            if not task:
                return
            self.notify("Delegating task — breaking down and spawning agents...", timeout=5)
            self._run_delegation(task, cwd)

        self.app.push_screen(DelegateModal(), on_result)

    @work(thread=True)
    def _run_delegation(self, task: str, cwd: str | None) -> None:
        spawned = delegate_task(task, cwd)
        if spawned:
            msg = f"Spawned {len(spawned)} agents: {', '.join(spawned)}"
        else:
            msg = "Failed to delegate — check that claude CLI is available"
        self.app.call_from_thread(self.notify, msg, timeout=5)
        self.app.call_from_thread(self._apply_refresh, scan_sessions(active_only=False))

    def action_resume_session(self) -> None:
        self.notify("Loading sessions...", timeout=2)
        self._load_resume_list()

    @work(thread=True)
    def _load_resume_list(self) -> None:
        sessions = list_resumable_sessions(limit=30)
        self.app.call_from_thread(self._show_resume_picker, sessions)

    def _show_resume_picker(self, sessions: list[HistoricalSession]) -> None:
        if not sessions:
            self.notify("No sessions found", timeout=2)
            return

        cwd = self.sessions[0].cwd if self.sessions else None

        def on_pick(picked: HistoricalSession | None) -> None:
            if picked is None:
                return
            if picked.is_alive:
                self.notify("Session is already alive", timeout=2)
                return
            if resume_session_in_tmux(picked.session_id, cwd=cwd):
                name = picked.name or picked.first_prompt[:30] or picked.session_id[:12]
                self.notify(f"Resumed: {name}", timeout=2)
                self.set_timer(3, self._do_refresh)
            else:
                self.notify("Failed to resume", timeout=2)

        self.app.push_screen(ResumePickerModal(sessions), on_pick)


# ═════════════════════════════════════════════════════════════════════════════
# App
# ═════════════════════════════════════════════════════════════════════════════

class TarsApp(App):
    """TARS — Terminal Agent Runtime Scanner."""

    TITLE = "TARS"
    SUB_TITLE = "Terminal Agent Runtime Scanner"
    COMMANDS = set()
    ENABLE_COMMAND_PALETTE = False

    def get_css_variables(self) -> dict[str, str]:
        return GRUVBOX["dark"].generate()

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())


def main() -> None:
    app = TarsApp()
    app.run()


if __name__ == "__main__":
    main()

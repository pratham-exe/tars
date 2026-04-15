"""Scratchpad viewer — shows worker reports, status, and orchestrator notes."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from tars.helpers import escape_markup
from tars.scanner import read_scratchpad
from tars.theme import AQUA, BLUE, GRAY, GREEN, ORANGE, PURPLE, RED, YELLOW

CSS = """
ScratchpadModal {
    background: rgba(40, 40, 40, 0.9);
    align: center middle;
}

#scratch-dialog {
    width: 90%;
    height: 85%;
    background: $panel;
    border: tall $accent;
    padding: 1 2;
}

#scratch-header {
    height: auto;
    padding-bottom: 1;
}

#scratch-title {
    text-style: bold;
    color: $accent;
    width: 1fr;
}

#scratch-hints {
    color: $text-muted;
    width: auto;
}

#scratch-scroll {
    height: 1fr;
}

.scratch-section {
    height: auto;
    padding: 1 1;
    margin-bottom: 1;
    background: $boost;
}

.scratch-section-title {
    text-style: bold;
    padding-bottom: 1;
}

.status-line {
    height: auto;
    padding: 0 1;
}

.worker-report {
    height: auto;
    padding: 1 1;
    margin-bottom: 1;
    background: $surface;
}

.report-title {
    text-style: bold;
    color: $secondary;
    padding-bottom: 1;
}

.report-content {
    height: auto;
}
"""

STATUS_COLORS = {
    "done": GREEN,
    "in-progress": YELLOW,
    "pending": GRAY,
    "failed": RED,
    "blocked": RED,
}


class ScratchpadModal(ModalScreen[None]):
    """Shows the shared scratchpad — worker reports, status, orchestrator notes."""

    CSS = CSS
    BINDINGS = [
        Binding("escape", "close", priority=True),
        Binding("q", "close", priority=True),
        Binding("r", "refresh_pad", priority=True),
        Binding("j", "scroll_down", show=False, priority=True),
        Binding("k", "scroll_up", show=False, priority=True),
        Binding("G", "scroll_end", show=False, priority=True),
        Binding("g", "scroll_start", show=False, priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="scratch-dialog"):
            with Horizontal(id="scratch-header"):
                yield Label(f"[{ORANGE}][b]Scratchpad[/b][/{ORANGE}]  [{GRAY}]/tmp/tars-delegation/[/{GRAY}]", id="scratch-title")
                yield Label("[dim]q[/dim] close  [dim]r[/dim] refresh  [dim]j/k[/dim] scroll", id="scratch-hints")
            yield VerticalScroll(id="scratch-scroll")

    def on_mount(self) -> None:
        self._render_scratchpad()

    def _render_scratchpad(self) -> None:
        scroll = self.query_one("#scratch-scroll", VerticalScroll)
        scroll.remove_children()
        widgets = []

        pad = read_scratchpad()

        # Status section
        if pad["status"]:
            status_lines = []
            for worker, status in pad["status"].items():
                color = STATUS_COLORS.get(status, GRAY)
                status_lines.append(f"  [{color}]●[/{color}] [{AQUA}]{escape_markup(worker)}[/{AQUA}]  [{color}]{status}[/{color}]")
            status_text = "\n".join(status_lines)
            widgets.append(Static(
                f"[{ORANGE}][b]Worker Status[/b][/{ORANGE}]\n\n{status_text}",
                classes="scratch-section",
            ))
        else:
            widgets.append(Static(
                f"[{ORANGE}][b]Worker Status[/b][/{ORANGE}]\n\n  [{GRAY}]No status updates yet[/{GRAY}]",
                classes="scratch-section",
            ))

        # Orchestrator notes
        orch_notes = pad["orchestrator_notes"].strip()
        if orch_notes and orch_notes != "# Orchestrator Notes":
            widgets.append(Static(
                f"[{PURPLE}][b]Orchestrator Notes[/b][/{PURPLE}]\n\n{escape_markup(orch_notes)}",
                classes="scratch-section",
            ))

        # Worker reports
        if pad["worker_reports"]:
            for report in pad["worker_reports"]:
                name = escape_markup(report["name"])
                content = report["content"].strip()
                if content:
                    widgets.append(Static(
                        f"[{BLUE}][b]{name}[/b][/{BLUE}]\n\n{escape_markup(content)}",
                        classes="worker-report",
                    ))
        else:
            widgets.append(Static(
                f"[{BLUE}][b]Worker Reports[/b][/{BLUE}]\n\n  [{GRAY}]No worker reports yet — workers will write to /tmp/tars-delegation/worker-<name>.md[/{GRAY}]",
                classes="scratch-section",
            ))

        scroll.mount_all(widgets)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_refresh_pad(self) -> None:
        self._render_scratchpad()

    def action_scroll_down(self) -> None:
        self.query_one("#scratch-scroll", VerticalScroll).scroll_down()

    def action_scroll_up(self) -> None:
        self.query_one("#scratch-scroll", VerticalScroll).scroll_up()

    def action_scroll_end(self) -> None:
        self.query_one("#scratch-scroll", VerticalScroll).scroll_end()

    def action_scroll_start(self) -> None:
        self.query_one("#scratch-scroll", VerticalScroll).scroll_home()

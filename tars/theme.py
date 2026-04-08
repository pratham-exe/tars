"""Gruvbox color scheme and shared constants for TARS."""

from textual.design import ColorSystem

TARS_ASCII = r"""
 ████████╗ █████╗ ██████╗ ███████╗
 ╚══██╔══╝██╔══██╗██╔══██╗██╔════╝
    ██║   ███████║██████╔╝███████╗
    ██║   ██╔══██║██╔══██╗╚════██║
    ██║   ██║  ██║██║  ██║███████║
    ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝
"""

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

# Named colors for rich markup
GREEN = "#b8bb26"
RED = "#fb4934"
YELLOW = "#fabd2f"
BLUE = "#83a598"
ORANGE = "#fe8019"
AQUA = "#8ec07c"
PURPLE = "#d3869b"
FG = "#ebdbb2"
GRAY = "#928374"

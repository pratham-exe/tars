"""TARS — Terminal Agent Runtime Scanner."""

from textual.app import App

from tars.screens import HomeScreen
from tars.theme import GRUVBOX


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
    TarsApp().run()


if __name__ == "__main__":
    main()

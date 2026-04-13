"""Modal dialogs for TARS."""

from tars.modals.confirm import ConfirmModal
from tars.modals.delegate import DelegateModal
from tars.modals.fullscreen import FullscreenPrompts, FullscreenTranscript
from tars.modals.prompt import PromptModal
from tars.modals.rename import RenameModal
from tars.modals.resume import ResumePickerModal
from tars.modals.session_picker import SessionPickerModal
from tars.modals.spawn import SpawnModal

__all__ = [
    "ConfirmModal",
    "DelegateModal",
    "FullscreenPrompts",
    "FullscreenTranscript",
    "PromptModal",
    "RenameModal",
    "ResumePickerModal",
    "SessionPickerModal",
    "SpawnModal",
]

"""Input intent carried from a physical shortcut into the recording runtime."""

from enum import Enum


class InputIntent(str, Enum):
    """How the model should interpret the user's speech."""

    DICTATION = "dictation"
    COMMAND = "command"


__all__ = ["InputIntent"]

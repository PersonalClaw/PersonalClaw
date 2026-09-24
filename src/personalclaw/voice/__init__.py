"""Voice-loop helpers shared by the STT and TTS surfaces.

``duplex`` holds the pure decision functions for the hands-free (duplex) loop:
confirmation/exit gating, TTS-echo detection, and pre-speech text cleaning.
Nothing in here performs I/O or touches a model — the endpoints in
``dashboard/`` own that and call these to decide.
"""

from personalclaw.voice.duplex import (
    DEFAULT_CONFIRMATION_PHRASES,
    DEFAULT_EXIT_PHRASES,
    DEFAULT_PUSH_TO_TALK_CHORD,
    ECHO_MIN_RUN,
    TAIL_WINDOW_WORDS,
    VOICE_DISCLAIMER,
    clean_for_speech,
    is_confirmation,
    is_echo,
    is_exit,
)

__all__ = [
    "DEFAULT_CONFIRMATION_PHRASES",
    "DEFAULT_EXIT_PHRASES",
    "DEFAULT_PUSH_TO_TALK_CHORD",
    "ECHO_MIN_RUN",
    "TAIL_WINDOW_WORDS",
    "VOICE_DISCLAIMER",
    "clean_for_speech",
    "is_confirmation",
    "is_echo",
    "is_exit",
]

"""SDK: the speech-to-text (STT) provider ABC + the shared helper an STT app needs.

Stable re-export of the ``SttProvider`` ABC + ``SttModel`` — an STT app implements
these and its factory returns a provider that core's stt registry resolves the ``stt``
use-case to. ``ensure_ffmpeg_in_path`` is the one cross-cutting helper a local STT
backend needs (audio decoding), exposed here so the app doesn't reach into core
internals.
"""

from personalclaw.stt.provider import (  # noqa: F401
    SttModel,
    SttProvider,
    TranscriptResult,
    TranscriptSegment,
    TranscriptWord,
)
from personalclaw.transcribe import ensure_ffmpeg_in_path  # noqa: F401

__all__ = [
    "SttProvider",
    "SttModel",
    "TranscriptResult",
    "TranscriptSegment",
    "TranscriptWord",
    "ensure_ffmpeg_in_path",
]

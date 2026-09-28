"""SDK: the speech-to-text (STT) provider ABC + the shared helper an STT app needs.

Stable re-export of the ``SttProvider`` ABC + ``SttModel`` — an STT app implements
these and its factory returns a provider that core's stt registry resolves the ``stt``
use-case to. ``ensure_ffmpeg_in_path`` is the one cross-cutting helper a local STT
backend needs (audio decoding), exposed here so the app doesn't reach into core
internals.

``SttError`` is how a provider says why it could not transcribe (raised from
``transcribe``; its message is shown as written), and ``SttProvider.unavailable_reason()``
why it is not available — so a failure is never read as a recording with no speech, and
"unavailable" names its cause.
"""

from personalclaw.stt.provider import (  # noqa: F401
    SttError,
    SttModel,
    SttProvider,
    TranscriptResult,
    TranscriptSegment,
    TranscriptWord,
)
from personalclaw.transcribe import ensure_ffmpeg_in_path  # noqa: F401

__all__ = [
    "SttProvider",
    "SttError",
    "SttModel",
    "TranscriptResult",
    "TranscriptSegment",
    "TranscriptWord",
    "ensure_ffmpeg_in_path",
]

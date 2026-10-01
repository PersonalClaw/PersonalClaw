"""SDK: the speech-to-text (STT) provider ABC + the shared helper an STT app needs.

Stable re-export of the ``SttProvider`` ABC + ``SttModel`` — an STT app implements
these and its factory returns a provider that core's stt registry resolves the ``stt``
use-case to. ``find_ffmpeg`` is the one cross-cutting helper a local STT backend that
runs ffmpeg needs: the absolute path of the ffmpeg PersonalClaw runs, found where core finds
its own, to hand to what runs it (never by changing the process's ``PATH``, which is every
child's). ``ffmpeg_not_found`` is the sentence for when there is none.

``SttError`` is how a provider says why it could not transcribe (raised from
``transcribe``; its message is shown as written), and ``SttProvider.unavailable_reason()``
why it is not available — so a failure is never read as a recording with no speech, and
"unavailable" names its cause.
"""

from personalclaw.ffmpeg_binary import ffmpeg_not_found, find_ffmpeg  # noqa: F401
from personalclaw.stt.provider import (  # noqa: F401
    SttError,
    SttModel,
    SttProvider,
    TranscriptResult,
    TranscriptSegment,
    TranscriptWord,
)

__all__ = [
    "SttProvider",
    "SttError",
    "SttModel",
    "TranscriptResult",
    "TranscriptSegment",
    "TranscriptWord",
    "find_ffmpeg",
    "ffmpeg_not_found",
]

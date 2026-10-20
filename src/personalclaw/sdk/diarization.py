"""SDK: the diarization provider ABC + result types.

Stable re-export of ``DiarizationProvider`` (INFERENCE: ``diarize``) / ``DiarizationModel``
/ ``SpeakerTurn`` + ``LocalModelProvider`` (MANAGEMENT). A LOCAL diarization app subclasses
BOTH (inference + local-model management); a hypothetical remote one would subclass only
``DiarizationProvider``. ``find_ffmpeg`` is shared for audio decoding: the absolute path of the
ffmpeg PersonalClaw runs, to hand to what runs it, and ``ffmpeg_not_found`` the sentence for when
there is none.

``DiarizationError`` is how a provider says why it could not tell the speakers apart (raised
from ``diarize``; its message is shown as written), so a failure is never read as a recording
with one speaker.
"""

from personalclaw.diarization.provider import (  # noqa: F401
    DiarizationError,
    DiarizationModel,
    DiarizationProvider,
    SpeakerTurn,
)
from personalclaw.ffmpeg_binary import ffmpeg_not_found, find_ffmpeg  # noqa: F401
from personalclaw.local_models.provider import LocalModelProvider  # noqa: F401

__all__ = [
    "DiarizationProvider",
    "DiarizationError",
    "DiarizationModel",
    "SpeakerTurn",
    "LocalModelProvider",
    "find_ffmpeg",
    "ffmpeg_not_found",
]

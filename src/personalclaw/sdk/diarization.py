"""SDK: the diarization provider ABC + result types (core L1).

Stable re-export of ``DiarizationProvider`` (INFERENCE: ``diarize``) / ``DiarizationModel``
/ ``SpeakerTurn`` + ``LocalModelProvider`` (MANAGEMENT). A LOCAL diarization app subclasses
BOTH (inference + local-model management); a hypothetical remote one would subclass only
``DiarizationProvider``. ``ensure_ffmpeg_in_path`` is shared for audio decoding.

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
from personalclaw.local_models.provider import LocalModelProvider  # noqa: F401
from personalclaw.transcribe import ensure_ffmpeg_in_path  # noqa: F401

__all__ = [
    "DiarizationProvider",
    "DiarizationError",
    "DiarizationModel",
    "SpeakerTurn",
    "LocalModelProvider",
    "ensure_ffmpeg_in_path",
]

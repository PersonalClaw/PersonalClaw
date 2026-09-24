"""Diarization (core L1) — the "who spoke when" capability + provider registry.

Its own first-class use-case (parallel to ``stt``), served by a separate diarization
provider app (ONNX default + optional pyannote). Core owns the seam + registry + the
deterministic speaker-fusion node; the heavy models live in the app.
"""

from personalclaw.diarization.provider import (  # noqa: F401
    DiarizationModel,
    DiarizationProvider,
    SpeakerTurn,
)
from personalclaw.diarization.registry import (  # noqa: F401
    active_diarization,
    get_provider,
    register_provider,
)

__all__ = [
    "DiarizationProvider",
    "DiarizationModel",
    "SpeakerTurn",
    "active_diarization",
    "register_provider",
    "get_provider",
]

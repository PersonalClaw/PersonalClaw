"""Speaker diarization entry point (core L1) — parallel to ``transcribe.py``.

Resolves the active ``diarization`` provider + model and returns speaker turns, with the
same sensitive-path guard ``transcribe.py`` applies. Returns ``None`` (feature off) when no
diarization model is bound — so the audio graph's diarization + fusion nodes skip
gracefully and rich transcripts still work without a diarization model installed.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def is_available() -> bool:
    """Whether diarization is bound + its provider usable."""
    from personalclaw.diarization.registry import active_diarization

    resolved = active_diarization()
    if resolved is None:
        return False
    provider, _model = resolved
    try:
        return await provider.is_available()
    except Exception:
        return False


async def diarize_audio(
    audio_path: str,
    *,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
):
    """Diarize an audio file via the active provider. Returns ``list[SpeakerTurn]`` or
    ``None`` (no model bound / not available / failure — always graceful, never raises)."""
    from personalclaw.diarization.registry import active_diarization
    from personalclaw.security import is_sensitive_path

    if is_sensitive_path(audio_path):
        logger.error("Refusing to read sensitive path for diarization: %s", audio_path)
        return None
    resolved = active_diarization()
    if resolved is None:
        return None
    provider, model_id = resolved
    try:
        return await provider.diarize(
            audio_path,
            model=model_id,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
    except Exception:
        logger.warning("diarization failed for %s", audio_path, exc_info=True)
        return None

"""Speaker diarization entry point — parallel to ``transcribe.py``.

Resolves the active ``diarization`` provider + model and returns speaker turns, with the
same sensitive-path guard ``transcribe.py`` applies. The knowledge pipeline skips its
diarization step before calling here when no diarization model is bound, so rich
transcripts still work without one installed.

**One answer per call**, as for speech-to-text: :func:`diarize_audio` returns the turns (``[]``
when no one spoke) or raises :class:`~personalclaw.diarization.provider.DiarizationError`
saying why there are none. It used to return ``None`` for every failure, and the pipeline read
that as a recording with no speakers: a two-voice clip whose audio the provider could not read
landed "Diarization done" with no speaker labels, and not one log line said why.
"""

from __future__ import annotations

import logging

from personalclaw.diarization.provider import DiarizationError, SpeakerTurn

logger = logging.getLogger(__name__)

NO_DIARIZATION_MODEL = (
    "No speaker diarization model is chosen, so the speakers can't be told apart. Choose one "
    "under Speaker diarization in Settings → Models."
)
SENSITIVE_DIARIZATION_PATH = (
    "This file is in a folder PersonalClaw never reads, so its speakers were not told apart."
)
#: A provider that answered ``None``: it could not diarize and did not say why.
NO_TURNS_NO_REASON = (
    "The speaker diarization model gave back nothing and its provider doesn't say why, so the "
    "transcript has no speaker labels. Check the gateway log, or choose another model under "
    "Speaker diarization in Settings → Models."
)
#: The sentence ahead of an unexpected error's own words.
DIARIZATION_FAILED = (
    "The speaker diarization model could not tell the speakers apart, so the transcript has no "
    "speaker labels."
)


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
) -> list[SpeakerTurn]:
    """Diarize an audio file via the active provider: its speaker turns, ``[]`` when no one
    spoke. Raises :class:`DiarizationError` when there are none to give: the provider's own
    reason unchanged, or the sentence for what stopped it."""
    from personalclaw.diarization.registry import active_diarization
    from personalclaw.security import is_sensitive_path

    if is_sensitive_path(audio_path):
        logger.error("Refusing to read sensitive path for diarization: %s", audio_path)
        raise DiarizationError(SENSITIVE_DIARIZATION_PATH)
    resolved = active_diarization()
    if resolved is None:
        raise DiarizationError(NO_DIARIZATION_MODEL)
    provider, model_id = resolved
    try:
        turns = await provider.diarize(
            audio_path,
            model=model_id,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
    except DiarizationError:
        raise
    except Exception as exc:  # noqa: BLE001 — said, with the provider's own words
        from personalclaw.providers.failure_copy import sentence_with_detail

        logger.warning("diarization failed for %s", audio_path, exc_info=True)
        raise DiarizationError(sentence_with_detail(DIARIZATION_FAILED, exc)) from exc
    if turns is None:
        raise DiarizationError(NO_TURNS_NO_REASON)
    return list(turns)

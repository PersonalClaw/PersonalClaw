"""Local speech-to-text.

Transcription resolves through the typed STT registry: the active model is the
``stt`` selection in ``active_models.json`` (Settings → Models) and behavior
(enabled, language) lives in ``use_case_settings/stt.json``. A recording above the segment
threshold is cut into parts with ffmpeg (found by :func:`personalclaw.ffmpeg_binary.find_ffmpeg`,
and handed to it by its absolute path); without ffmpeg it is sent whole, and a failure of that one
call says so (:data:`SENT_WHOLE`).

**Counted by the minute.** Each transcription goes through the metering seam every call billed
by its unit goes through (``guardrails.media_call``), as long as the recording is
(:func:`audio_seconds`): an unattended one is weighed against the dollar caps before it runs, and
every one is counted in Usage by its minutes.

**One answer per call.** :func:`transcribe_audio` and :func:`transcribe_audio_detailed` return
the transcript, whose text is empty when the recording holds no speech, or raise
:class:`~personalclaw.stt.provider.SttError` with the sentence saying why there is none:
speech-to-text is off, no model is chosen, the provider said why, or the provider gave back
nothing and did not say. They used to return ``None`` for all of those, and every caller read
``None`` as a recording with no speech in it: a knowledge video whose narration was never
transcribed said "Transcription done".
"""

import asyncio
import logging
import os
import re

from personalclaw.ffmpeg_binary import ffmpeg_not_found, find_ffmpeg
from personalclaw.stt.provider import SttError

logger = logging.getLogger(__name__)

#: Why a transcription cannot start, in the words :func:`unavailable_sentence` also uses.
NO_STT_MODEL = (
    "No speech-to-text model is chosen, so nothing can be transcribed. Choose one under "
    "Speech-to-text in Settings → Models."
)
STT_TURNED_OFF = (
    "Speech-to-text is turned off. Turn on Enable speech-to-text in Settings → Speech & "
    "Transcription."
)
#: The sensitive-path guard refused the file (a credential or system location).
SENSITIVE_AUDIO_PATH = (
    "This file is in a folder PersonalClaw never reads, so it was not transcribed."
)
#: A provider that answered ``None``: it could not transcribe and did not say why. Not "no
#: speech": a provider that heard none answers with empty text.
NO_TRANSCRIPT_NO_REASON = (
    "The speech-to-text model gave back no transcript and its provider doesn't say why. Check "
    "the gateway log, or choose another model under Speech-to-text in Settings → Models."
)
#: After the reason a long recording sent whole could not be transcribed: it went in one piece
#: only because there was no ffmpeg to cut it (:func:`ffmpeg_not_found` follows).
SENT_WHOLE = (
    "A recording this long is cut into parts with ffmpeg before it is transcribed, and this one "
    "was sent whole."
)

# Above this size a single audio file is segmented (via ffmpeg) into fixed-length
# chunks that are transcribed sequentially and stitched — so a 1 GB audio doesn't
# depend on the active STT provider tolerating the whole file in one call. Mirrors
# the composer mic cap so anything a naive/remote provider can handle in one shot
# stays a single call. Tunable via PERSONALCLAW_STT_SEGMENT_THRESHOLD (bytes).
_STT_SEGMENT_THRESHOLD = 25 * 1024 * 1024
# Each segment's wall-clock length in seconds (ffmpeg -segment_time). 600s ≈ a
# comfortable Whisper chunk; small enough that any provider handles one segment.
_STT_SEGMENT_SECONDS = 600


def _bound_provider():
    """The active STT provider, when STT is enabled and a model is bound; else ``None``."""
    from personalclaw.providers.use_cases import load_use_case_settings, use_case_enabled
    from personalclaw.stt.registry import active_stt

    settings = load_use_case_settings("stt")
    if not use_case_enabled("stt", settings):
        return None
    resolved = active_stt()
    return resolved[0] if resolved is not None else None


async def is_available() -> bool:
    """Whether STT is enabled (use_case_settings) and the active provider is usable.

    Resolves the active STT provider and asks it — local backends check their
    in-process deps, remote backends check a credential — so the readiness gate
    is the same one transcription will use, regardless of provider.
    """
    provider = _bound_provider()
    return provider is not None and await provider.is_available()


async def unavailable_reason() -> str:
    """Why speech-to-text is unavailable, in the bound provider's own words: its
    :meth:`~personalclaw.stt.provider.SttProvider.unavailable_reason`.

    ``""`` when STT is off, nothing is bound, the provider is available, or it cannot say —
    and the surface then uses its own words. Asked by a surface once :func:`is_available`
    said False, so "not available" can name its cause.
    """
    provider = _bound_provider()
    if provider is None or await provider.is_available():
        return ""
    return (await provider.unavailable_reason()).strip()


async def unavailable_sentence() -> str:
    """Why speech-to-text cannot run, as the sentence a surface shows once :func:`is_available`
    said False: the bound provider's own reason when it gives one, else what is missing (no
    model chosen, or speech-to-text turned off), else that its provider cannot say.

    A surface sends it with the code ``stt_unavailable``, so a client keys on the code and never
    on the words: the chat composer matched ``/not available/i`` and replaced a provider's own
    reason that happened to say so with advice to set up a model that was already bound.
    """
    reason = await unavailable_reason()
    if reason:
        return reason
    from personalclaw.providers.use_cases import load_use_case_settings, use_case_enabled
    from personalclaw.stt.registry import active_stt

    if active_stt() is None:
        return NO_STT_MODEL
    if not use_case_enabled("stt", load_use_case_settings("stt")):
        return STT_TURNED_OFF
    return (
        "The speech-to-text model can't run right now, and its provider doesn't say why. Check "
        "the gateway log, or choose another model under Speech-to-text in Settings → Models."
    )


def _resolve(audio_path: str):
    """``(provider, model_id, language)`` for transcribing *audio_path*, or :class:`SttError`
    saying why nothing can: speech-to-text is off, the file is somewhere PersonalClaw never
    reads, or no model is chosen."""
    from personalclaw.providers.use_cases import load_use_case_settings, use_case_enabled
    from personalclaw.security import is_sensitive_path
    from personalclaw.stt.registry import active_stt

    settings = load_use_case_settings("stt")
    if not use_case_enabled("stt", settings):
        raise SttError(STT_TURNED_OFF)
    if is_sensitive_path(audio_path):
        logger.error("Refusing to read sensitive path: %s", audio_path)
        raise SttError(SENSITIVE_AUDIO_PATH)
    resolved = active_stt()
    if resolved is None:
        raise SttError(NO_STT_MODEL)
    provider, model_id = resolved
    return provider, model_id, str(settings.get("language_code", "") or "")


_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def audio_seconds(audio_path: str) -> float | None:
    """How long the recording at *audio_path* is, in seconds, or None when it cannot be read.

    Read from the header ffmpeg prints for the file (``Duration: 00:01:23.45``), which every
    format speech-to-text takes has; a WAV file is read directly when ffmpeg is not there.
    """
    import subprocess

    ffmpeg = find_ffmpeg()
    if ffmpeg:
        try:
            probe = subprocess.run(
                [ffmpeg, "-hide_banner", "-nostdin", "-i", audio_path],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            logger.debug("could not read the length of %s", audio_path, exc_info=True)
        else:
            found = _DURATION.search(probe.stderr or "")
            if found:
                hours, minutes, seconds = found.groups()
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    try:
        import wave

        with wave.open(audio_path, "rb") as clip:
            rate = clip.getframerate()
            return clip.getnframes() / float(rate) if rate else None
    except (OSError, EOFError, wave.Error):
        return None


async def _metered_transcription(provider, model_id: str, audio_path: str, run, *, unattended):
    """*run* (the transcription of *audio_path*) through the metering seam, by its minutes."""
    from personalclaw.guardrails.media_call import MediaCall, metered_media_call
    from personalclaw.providers.engines import binding_name

    seconds = await asyncio.to_thread(audio_seconds, audio_path)
    call = MediaCall(
        provider=binding_name(provider),
        model=model_id,
        unit="minute",
        quantity=round(seconds / 60.0, 4) if seconds is not None else None,
    )

    def _billed(result) -> float | None:
        heard = float(getattr(result, "duration", 0.0) or 0.0)
        return round(heard / 60.0, 4) if heard > 0 else None

    from personalclaw.guardrails.budgets import BudgetConfigUnreadable
    from personalclaw.guardrails.failure import BudgetExceededError

    try:
        return await metered_media_call(call, run, unattended=unattended, billed=_billed)
    except BudgetExceededError as refused:
        # Said as every reason there is no transcript is: an SttError with its sentence.
        raise SttError(refused.sentence()) from refused
    except BudgetConfigUnreadable as unknown:
        raise SttError(f"{unknown}, so nothing was transcribed.") from unknown


def _long(audio_path: str) -> bool:
    """Whether *audio_path* is above the segment threshold, and so transcribed in parts when there
    is ffmpeg to cut it. A large recording must not depend on the provider taking the whole file in
    one call; anything a naive or remote provider handles in one shot stays one call."""
    try:
        return os.path.getsize(audio_path) > _stt_segment_threshold()
    except OSError:
        return False


async def _sent_whole(call, *, long: bool):
    """The one call that transcribes a whole recording, or :class:`SttError` saying why it gave
    nothing. A *long* recording is sent whole only because there was no ffmpeg to cut it, so the
    sentence ends by saying so and where ffmpeg was looked for."""
    try:
        result = await call()
    except SttError as exc:
        if not long:
            raise
        raise SttError(f"{exc} {SENT_WHOLE} {ffmpeg_not_found()}") from exc
    if result is None:
        if long:
            raise SttError(f"{NO_TRANSCRIPT_NO_REASON} {SENT_WHOLE} {ffmpeg_not_found()}")
        raise SttError(NO_TRANSCRIPT_NO_REASON)
    return result


async def transcribe_audio(audio_path: str) -> str:
    """Transcribe an audio file via the active STT provider: its text, empty when there was
    no speech.

    Raises :class:`~personalclaw.stt.provider.SttError` when there is no transcript: the
    provider's own reason unchanged, or the sentence for what stopped it (see the module
    docstring). A caller shows it; it never reads a failure as a recording with no speech.
    """
    provider, model_id, language = _resolve(audio_path)
    long = _long(audio_path)
    ffmpeg = find_ffmpeg() if long else None

    async def _run() -> str:
        if ffmpeg:
            return await _transcribe_segmented(
                provider, model_id, language, audio_path, ffmpeg=ffmpeg
            )
        return await _sent_whole(
            lambda: provider.transcribe(audio_path, model=model_id, language=language), long=long
        )

    text = await _metered_transcription(provider, model_id, audio_path, _run, unattended=None)
    if text:
        from personalclaw.security import redact_credentials, redact_exfiltration_urls

        text, _ = redact_exfiltration_urls(text)
        text, _ = redact_credentials(text)
    return text


async def transcribe_audio_detailed(
    audio_path: str,
    *,
    bias_terms: list[str] | None = None,
    unattended: bool | None = None,
):
    """Rich transcription via the active STT provider (core L0): a ``TranscriptResult`` (flat
    text + segments + word timestamps), whose text is empty when there was no speech.

    Mirrors :func:`transcribe_audio` (same active-STT resolution, sensitive-path guard,
    credential/exfil redaction of the flat text, the same :class:`SttError` when there is no
    transcript) but preserves structure. For large files the segmented path OFFSETS each
    chunk's segment/word times by the chunk's start so the merged timeline is continuous.
    ``bias_terms`` is the Lexicon pre-decode hint (L2). ``unattended`` says the transcription
    is unattended work whatever session asks for it (a knowledge import), and so is held to the
    dollar caps (``guardrails.media_call``)."""
    provider, model_id, language = _resolve(audio_path)
    long = _long(audio_path)
    ffmpeg = find_ffmpeg() if long else None

    async def _run():
        if ffmpeg:
            return await _transcribe_segmented_detailed(
                provider, model_id, language, audio_path, bias_terms, ffmpeg=ffmpeg
            )
        return await _sent_whole(
            lambda: provider.transcribe_detailed(
                audio_path, model=model_id, language=language, bias_terms=bias_terms
            ),
            long=long,
        )

    result = await _metered_transcription(
        provider, model_id, audio_path, _run, unattended=unattended
    )

    # Redact the flat text (the same guard transcribe_audio applies). Segment text mirrors
    # the flat text span-for-span; redacting the flat surface is what feeds FTS/embeddings.
    if result.text:
        from personalclaw.security import redact_credentials, redact_exfiltration_urls

        result.text, _ = redact_exfiltration_urls(result.text)
        result.text, _ = redact_credentials(result.text)
    return result


def _clock(seconds: float) -> str:
    """``m:ss`` (or ``h:mm:ss``) for a position in a recording."""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _part_failed(index: int, total: int) -> str:
    start = index * float(_STT_SEGMENT_SECONDS)
    return (
        f"This recording is transcribed in {total} parts, and part {index + 1} (from "
        f"{_clock(start)}) could not be transcribed, so there is no transcript."
    )


async def _transcribe_part(call, index: int, total: int):
    """One part's transcription, or :class:`SttError` saying which part had none.

    A part used to be logged and SKIPPED, so a recording whose middle failed came back as the
    rest of its words with nothing to say a stretch was missing. The provider's own
    :class:`SttError` is raised unchanged: every part would fail the same way."""
    try:
        part = await call()
    except SttError:
        raise
    except Exception as exc:  # noqa: BLE001 — said, with the provider's words, not swallowed
        from personalclaw.providers.failure_copy import sentence_with_detail

        logger.warning("STT part %d of %d failed", index + 1, total, exc_info=True)
        raise SttError(sentence_with_detail(_part_failed(index, total), exc)) from exc
    if part is None:
        raise SttError(f"{_part_failed(index, total)} {NO_TRANSCRIPT_NO_REASON}")
    return part


async def _segment(ffmpeg: str, audio_path: str, work: str) -> list[str]:
    """Re-encode *audio_path* into uniform mono 16 kHz WAV parts of ``_STT_SEGMENT_SECONDS``
    (what Whisper wants) under *work*; the part files in order, or ``[]`` when ffmpeg failed."""
    import asyncio

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        audio_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "segment",
        "-segment_time",
        str(_STT_SEGMENT_SECONDS),
        os.path.join(work, "seg_%05d.wav"),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    rc = await proc.wait()
    parts = sorted(os.path.join(work, f) for f in os.listdir(work) if f.startswith("seg_"))
    if rc != 0:
        logger.warning("STT segmentation failed (rc=%s); single call", rc)
        return []
    return parts


async def _transcribe_segmented_detailed(
    provider,
    model_id: str,
    language: str,
    audio_path: str,
    bias_terms: list[str] | None,
    *,
    ffmpeg: str,
):
    """Detailed variant of :func:`_transcribe_segmented`. Transcribes each chunk *ffmpeg* cuts
    with ``transcribe_detailed`` and merges, OFFSETTING every segment/word time by the
    chunk's start offset (chunk N starts at N * _STT_SEGMENT_SECONDS) so the merged
    timeline is continuous. Falls back to a single detailed call on any ffmpeg failure.
    A part with no transcript raises :class:`SttError` naming it (:func:`_transcribe_part`)."""
    import shutil
    import tempfile

    from personalclaw.stt.provider import TranscriptResult, TranscriptSegment, TranscriptWord

    async def _whole():
        result = await provider.transcribe_detailed(
            audio_path, model=model_id, language=language, bias_terms=bias_terms
        )
        if result is None:
            raise SttError(NO_TRANSCRIPT_NO_REASON)
        return result

    work = tempfile.mkdtemp(prefix="stt_seg_")
    try:
        chunks = await _segment(ffmpeg, audio_path, work)
        if not chunks:
            return await _whole()

        merged_segments: list[TranscriptSegment] = []
        text_parts: list[str] = []
        lang_out = ""
        total_duration = 0.0
        for idx, chunk in enumerate(chunks):
            offset = idx * float(_STT_SEGMENT_SECONDS)
            part = await _transcribe_part(
                lambda chunk=chunk: provider.transcribe_detailed(
                    chunk, model=model_id, language=language, bias_terms=bias_terms
                ),
                idx,
                len(chunks),
            )
            lang_out = lang_out or part.language
            for seg in part.segments:
                merged_segments.append(
                    TranscriptSegment(
                        start=seg.start + offset,
                        end=seg.end + offset,
                        text=seg.text,
                        speaker=seg.speaker,
                        words=[
                            TranscriptWord(w.start + offset, w.end + offset, w.word, w.prob)
                            for w in seg.words
                        ],
                    )
                )
            if part.text:
                text_parts.append(part.text.strip())
            total_duration = offset + (part.duration or 0.0)
        return TranscriptResult(
            text=" ".join(t for t in text_parts if t).strip(),
            language=lang_out,
            duration=total_duration,
            segments=merged_segments,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _stt_segment_threshold() -> int:
    raw = os.environ.get("PERSONALCLAW_STT_SEGMENT_THRESHOLD")
    if raw:
        try:
            val = int(raw)
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass
    return _STT_SEGMENT_THRESHOLD


async def _transcribe_segmented(
    provider, model_id: str, language: str, audio_path: str, *, ffmpeg: str
) -> str:
    """Split a large audio file into fixed-length segments (with *ffmpeg*), transcribe each
    sequentially, and stitch the transcripts. Keeps peak memory + per-call size
    bounded regardless of the provider. Falls back to a single call on any ffmpeg
    failure so a segmentation problem never silently drops the transcription. A part with no
    transcript raises :class:`SttError` naming it (:func:`_transcribe_part`)."""
    import shutil
    import tempfile

    async def _whole():
        text = await provider.transcribe(audio_path, model=model_id, language=language)
        if text is None:
            raise SttError(NO_TRANSCRIPT_NO_REASON)
        return text

    work = tempfile.mkdtemp(prefix="stt_seg_")
    try:
        segments = await _segment(ffmpeg, audio_path, work)
        if not segments:
            return await _whole()

        logger.info(
            "STT: transcribing %d segments of %s", len(segments), os.path.basename(audio_path)
        )
        parts: list[str] = []
        for idx, seg in enumerate(segments):
            text = await _transcribe_part(
                lambda seg=seg: provider.transcribe(seg, model=model_id, language=language),
                idx,
                len(segments),
            )
            if text:
                parts.append(text.strip())
        return " ".join(p for p in parts if p)
    finally:
        shutil.rmtree(work, ignore_errors=True)

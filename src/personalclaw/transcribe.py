"""Local speech-to-text.

Transcription resolves through the typed STT registry: the active model is the
``stt`` selection in ``active_models.json`` (Settings → Models) and behavior
(enabled, language) lives in ``use_case_settings/stt.json``. faster-whisper (the
in-process CTranslate2 Whisper) is the sole bundled backend; it depends on
``ffmpeg`` for ``.webm`` decoding.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Above this size a single audio file is segmented (via ffmpeg) into fixed-length
# chunks that are transcribed sequentially and stitched — so a 1 GB audio doesn't
# depend on the active STT provider tolerating the whole file in one call. Mirrors
# the composer mic cap so anything a naive/remote provider can handle in one shot
# stays a single call. Tunable via PERSONALCLAW_STT_SEGMENT_THRESHOLD (bytes).
_STT_SEGMENT_THRESHOLD = 25 * 1024 * 1024
# Each segment's wall-clock length in seconds (ffmpeg -segment_time). 600s ≈ a
# comfortable Whisper chunk; small enough that any provider handles one segment.
_STT_SEGMENT_SECONDS = 600

_FFMPEG_CANDIDATE_DIRS = [
    os.path.expanduser("~/ffmpeg"),
    os.path.expanduser("~/.local/bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
]


def ensure_ffmpeg_in_path() -> None:
    """Add known ffmpeg directories to PATH if they contain an ffmpeg binary."""
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    for d in reversed(_FFMPEG_CANDIDATE_DIRS):
        if d not in path_parts and os.path.isfile(os.path.join(d, "ffmpeg")):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            path_parts.insert(0, d)


async def is_available() -> bool:
    """Whether STT is enabled (use_case_settings) and the active provider is usable.

    Resolves the active STT provider and asks it — local backends check their
    in-process deps, remote backends check a credential — so the readiness gate
    is the same one transcription will use, regardless of provider.
    """
    from personalclaw.providers.use_cases import load_use_case_settings
    from personalclaw.stt.registry import active_stt

    settings = load_use_case_settings("stt")
    if not settings.get("enabled", True):
        return False
    resolved = active_stt()
    if resolved is None:
        return False
    provider, _model = resolved
    if not await provider.is_available():
        return False
    ensure_ffmpeg_in_path()
    if not _ffmpeg_present():
        logger.warning("ffmpeg not found; .webm transcription will be unavailable")
    return True


def _ffmpeg_present() -> bool:
    import shutil

    return shutil.which("ffmpeg") is not None


async def transcribe_audio(audio_path: str) -> str | None:
    """Transcribe an audio file via the active STT provider. Returns text or None."""
    from personalclaw.providers.use_cases import load_use_case_settings
    from personalclaw.stt.registry import active_stt

    settings = load_use_case_settings("stt")
    if not settings.get("enabled", True):
        logger.debug("STT disabled in settings")
        return None

    from personalclaw.security import is_sensitive_path

    if is_sensitive_path(audio_path):
        logger.error("Refusing to read sensitive path: %s", audio_path)
        return None

    resolved = active_stt()
    if resolved is None:
        logger.debug("No active STT model selected")
        return None
    provider, model_id = resolved

    ensure_ffmpeg_in_path()
    language = str(settings.get("language_code", "") or "")

    # Large audio → ffmpeg-segment + transcribe each chunk, so a 1 GB recording
    # doesn't depend on the provider tolerating the whole file in one call. Falls
    # back to a single call when the file is small or ffmpeg isn't available.
    try:
        big = os.path.getsize(audio_path) > _stt_segment_threshold()
    except OSError:
        big = False
    if big and _ffmpeg_present():
        result = await _transcribe_segmented(provider, model_id, language, audio_path)
    else:
        result = await provider.transcribe(audio_path, model=model_id, language=language)

    if result:
        from personalclaw.security import redact_credentials, redact_exfiltration_urls

        result, _ = redact_exfiltration_urls(result)
        result, _ = redact_credentials(result)
    return result


async def transcribe_audio_detailed(audio_path: str, *, bias_terms: list[str] | None = None):
    """Rich transcription via the active STT provider (core L0). Returns a
    ``TranscriptResult`` (flat text + segments + word timestamps) or ``None``.

    Mirrors :func:`transcribe_audio` (same active-STT resolution, sensitive-path guard,
    credential/exfil redaction of the flat text) but preserves structure. For large files
    the segmented path OFFSETS each chunk's segment/word times by the chunk's start so the
    merged timeline is continuous. ``bias_terms`` is the Lexicon pre-decode hint (L2)."""
    from personalclaw.providers.use_cases import load_use_case_settings
    from personalclaw.stt.registry import active_stt

    settings = load_use_case_settings("stt")
    if not settings.get("enabled", True):
        logger.debug("STT disabled in settings")
        return None

    from personalclaw.security import is_sensitive_path

    if is_sensitive_path(audio_path):
        logger.error("Refusing to read sensitive path: %s", audio_path)
        return None

    resolved = active_stt()
    if resolved is None:
        logger.debug("No active STT model selected")
        return None
    provider, model_id = resolved

    ensure_ffmpeg_in_path()
    language = str(settings.get("language_code", "") or "")

    try:
        big = os.path.getsize(audio_path) > _stt_segment_threshold()
    except OSError:
        big = False
    if big and _ffmpeg_present():
        result = await _transcribe_segmented_detailed(
            provider, model_id, language, audio_path, bias_terms
        )
    else:
        result = await provider.transcribe_detailed(
            audio_path, model=model_id, language=language, bias_terms=bias_terms
        )

    # Redact the flat text (the same guard transcribe_audio applies). Segment text mirrors
    # the flat text span-for-span; redacting the flat surface is what feeds FTS/embeddings.
    if result is not None and result.text:
        from personalclaw.security import redact_credentials, redact_exfiltration_urls

        result.text, _ = redact_exfiltration_urls(result.text)
        result.text, _ = redact_credentials(result.text)
    return result


async def _transcribe_segmented_detailed(
    provider, model_id: str, language: str, audio_path: str, bias_terms: list[str] | None
):
    """Detailed variant of :func:`_transcribe_segmented`. Transcribes each ffmpeg chunk
    with ``transcribe_detailed`` and merges, OFFSETTING every segment/word time by the
    chunk's start offset (chunk N starts at N * _STT_SEGMENT_SECONDS) so the merged
    timeline is continuous. Falls back to a single detailed call on any ffmpeg failure."""
    import asyncio
    import shutil
    import tempfile

    from personalclaw.stt.provider import TranscriptResult, TranscriptSegment, TranscriptWord

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return await provider.transcribe_detailed(
            audio_path, model=model_id, language=language, bias_terms=bias_terms
        )

    work = tempfile.mkdtemp(prefix="stt_seg_")
    try:
        pattern = os.path.join(work, "seg_%05d.wav")
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
            pattern,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rc = await proc.wait()
        chunks = sorted(os.path.join(work, f) for f in os.listdir(work) if f.startswith("seg_"))
        if rc != 0 or not chunks:
            logger.warning("STT segmentation failed (rc=%s); single detailed call", rc)
            return await provider.transcribe_detailed(
                audio_path, model=model_id, language=language, bias_terms=bias_terms
            )

        merged_segments: list[TranscriptSegment] = []
        text_parts: list[str] = []
        lang_out = ""
        total_duration = 0.0
        for idx, chunk in enumerate(chunks):
            offset = idx * float(_STT_SEGMENT_SECONDS)
            try:
                part = await provider.transcribe_detailed(
                    chunk, model=model_id, language=language, bias_terms=bias_terms
                )
            except Exception:
                logger.warning("STT segment failed: %s", os.path.basename(chunk), exc_info=True)
                part = None
            if part is None:
                continue
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
        flat = " ".join(t for t in text_parts if t).strip()
        if not flat and not merged_segments:
            return None
        return TranscriptResult(
            text=flat,
            language=lang_out,
            duration=total_duration,
            segments=merged_segments,
        )
    finally:
        import shutil as _sh

        _sh.rmtree(work, ignore_errors=True)


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
    provider, model_id: str, language: str, audio_path: str
) -> str | None:
    """Split a large audio file into fixed-length segments (ffmpeg), transcribe each
    sequentially, and stitch the transcripts. Keeps peak memory + per-call size
    bounded regardless of the provider. Falls back to a single call on any ffmpeg
    failure so a segmentation problem never silently drops the transcription."""
    import asyncio
    import shutil
    import tempfile

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return await provider.transcribe(audio_path, model=model_id, language=language)

    work = tempfile.mkdtemp(prefix="stt_seg_")
    try:
        # Re-encode to a uniform segmented WAV (mono 16k — what Whisper wants), so
        # the segmenter works regardless of the source container/codec.
        pattern = os.path.join(work, "seg_%05d.wav")
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
            pattern,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rc = await proc.wait()
        segments = sorted(os.path.join(work, f) for f in os.listdir(work) if f.startswith("seg_"))
        if rc != 0 or not segments:
            logger.warning("STT segmentation failed (rc=%s); falling back to single call", rc)
            return await provider.transcribe(audio_path, model=model_id, language=language)

        logger.info(
            "STT: transcribing %d segments of %s", len(segments), os.path.basename(audio_path)
        )
        parts: list[str] = []
        for seg in segments:
            try:
                text = await provider.transcribe(seg, model=model_id, language=language)
            except Exception:
                logger.warning("STT segment failed: %s", os.path.basename(seg), exc_info=True)
                text = None
            if text:
                parts.append(text.strip())
        return " ".join(p for p in parts if p) or ""
    finally:
        import shutil as _sh

        _sh.rmtree(work, ignore_errors=True)

"""PersonalClaw voice reply — generate TTS audio and upload to the channel.

Post-response hook: strips markdown, splits into sentences, synthesizes each via
the active TTS provider (a local backend like the piper-tts app, or a remote
OpenAI-compatible endpoint), uploads to the same channel thread. Fire-and-forget —
never blocks the text response. This module is provider-AGNOSTIC: synthesis is
delegated to ``TtsProvider.synthesize`` (each backend owns its own synthesis — the
piper subprocess lives in the piper-tts app, not here).
"""

import asyncio
import logging
import os
import re
import shutil
import tempfile
from typing import Any

from personalclaw.cancellation import kill_timed_out
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.tts.provider import TtsProvider
from personalclaw.tts.registry import TtsNotReady, can_speak

# The voice upload path takes a duck-typed channel client (anything exposing
# ``upload_file``); the concrete client lives in the channel provider app, so
# core does not import it — the annotation is structural (Any).

logger = logging.getLogger(__name__)


# ── Config defaults ──
DEFAULT_RATE = "100%"
MAX_CHARS = 2900

# The wav-stitch ffmpeg deadline, named so a test can inject it instead of sleeping on it.
_STITCH_TIMEOUT = 30.0

_RATE_RE = re.compile(r"^\d{1,3}%$")


def _validate_rate(rate: str) -> str:
    """Return *rate* if it looks like ``'95%'``, else the default."""
    return rate if _RATE_RE.match(rate) else DEFAULT_RATE


def strip_markdown(text: str) -> str:
    """Strip channel mrkdwn / markdown to plain speakable text."""
    t = text
    # Replace fenced code blocks with spoken placeholder

    def _code_block(m):
        content = m.group(0)
        if content.startswith("```diff"):
            return " (diff block) "
        return " (code block) "

    t = re.sub(r"```[\s\S]*?```", _code_block, t)
    # Replace markdown tables with spoken placeholder

    def _table(m):
        rows = (
            len([ln for ln in m.group(0).splitlines() if ln.strip().startswith("|")]) - 1
        )  # exclude header separator
        return f" (table with {rows} rows) "

    t = re.sub(r"(?:^\|.+\|$\n?){2,}", _table, t, flags=re.MULTILINE)
    # Remove inline code: keep short non-path text, strip long or path-like

    def _inline_code(m):
        inner = m.group(1)
        if len(inner) > 30 or "/" in inner:
            return " (file path) "
        return inner

    t = re.sub(r"`([^`]+)`", _inline_code, t)
    # Channel deep links (mrkdwn): <url|label> → label, bare <url> → ""
    t = re.sub(r"<([^|>]+)\|([^>]+)>", r"\2", t)
    t = re.sub(r"<https?://[^>]+>", " (link) ", t)
    # Markdown links: [label](url) → label
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    # Bold / italic / strikethrough markers
    t = re.sub(r"[*_~]+", "", t)
    # Emoji shortcodes
    t = re.sub(r":[a-z0-9_+-]+:", "", t)
    # Unicode emoji
    t = re.sub(
        r"[\U0001f300-\U0001faff\U00002702-\U000027b0\U0000fe00-\U0000fe0f\U0000200d]+",
        "",
        t,
    )
    # Bare URLs
    t = re.sub(r"https?://\S+", " (link) ", t)
    # OPTIONS buttons line
    t = re.sub(r"\[OPTIONS:.*?\]", "", t)
    # Diff blocks: lines starting with +/- only inside fenced blocks are
    # already removed above; catch stray unified-diff hunks.
    t = re.sub(r"^@@[^@]+@@.*$", "", t, flags=re.MULTILINE)
    # Collapse whitespace
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"  +", " ", t)
    return t.strip()


async def _synthesize_chunk(
    provider: TtsProvider,
    text: str,
    *,
    voice: str,
    speed: float,
    speech_voice: str,
) -> str | None:
    """Synthesize one chunk via *provider*. Returns an audio file path or None.

    ``speech_voice`` (the persona) is only meaningful to providers that accept
    one; Piper ignores the extra kwarg.

    Metered by the characters it speaks (``guardrails.media_call``): held to the dollar caps
    first when it is unattended work, and counted in Usage either way. A chunk a cap refuses is
    not spoken, and the refusal is raised for the caller to say:
    :class:`~personalclaw.guardrails.failure.BudgetExceededError`, or
    :class:`~personalclaw.guardrails.budgets.BudgetConfigUnreadable` when the caps cannot be read.
    """
    from personalclaw.guardrails.media_call import MediaCall, metered_media_call
    from personalclaw.providers.engines import binding_name

    return await metered_media_call(
        MediaCall(
            provider=binding_name(provider),
            model=voice,
            unit="character",
            quantity=len(text),
        ),
        lambda: provider.synthesize(
            text,
            voice=voice,
            speed=speed,
            speech_voice=speech_voice,
        ),
    )


async def synthesize_speech(
    provider: TtsProvider,
    text: str,
    *,
    voice: str = "",
    speed: float = 1.0,
    speech_voice: str = "",
) -> str | None:
    """Generate TTS audio from *text* via the active provider.

    Returns the path to a temp audio file, or None on failure. Caller is
    responsible for deleting the file.

    LLM output is redacted for credentials and exfiltration URLs before
    synthesis — audio files uploaded to a channel bypass the usual text-path
    redaction, so we apply both filters here to prevent secrets or
    suspicious URLs from being spoken and persisted in the channel.

    A voice the provider says it cannot speak with now (``can_synthesize``) is not asked for, and
    speech a spend cap refuses is not spoken: the reason is logged and None returned, the answer
    this helper gives for any failure.
    """
    from personalclaw.guardrails.budgets import BudgetConfigUnreadable
    from personalclaw.guardrails.failure import BudgetExceededError

    if not await can_speak(provider, voice):
        logger.warning("voice_reply: %s", TtsNotReady(provider, voice).message)
        return None
    text, cred_warns = redact_credentials(text)
    text, url_warns = redact_exfiltration_urls(text)
    if cred_warns:
        logger.warning("voice_reply: redacted %d credential pattern(s) before TTS", len(cred_warns))
    if url_warns:
        logger.warning("voice_reply: redacted %d suspicious URL(s) before TTS", len(url_warns))

    plain = strip_markdown(text).strip()
    if not plain:
        return None
    try:
        return await _synthesize_chunk(
            provider,
            plain,
            voice=voice,
            speed=speed,
            speech_voice=speech_voice,
        )
    except BudgetExceededError as refused:
        logger.warning("voice_reply: not spoken: %s", refused.sentence())
    except BudgetConfigUnreadable as unknown:
        logger.warning("voice_reply: not spoken: %s", unknown)
    return None


async def upload_voice_to_channel(
    channel_client: "Any",
    channel: str,
    thread_ts: str,
    audio_path: str,
) -> bool:
    """Upload an audio file to a channel thread as a voice clip.

    The file extension (.mp3 / .wav / .ogg etc.) is preserved so the channel's
    player renders it correctly.
    """
    ext = os.path.splitext(audio_path)[1].lstrip(".") or "wav"
    try:
        await channel_client.upload_file(
            channel=channel,
            thread_ts=thread_ts,
            file=audio_path,
            filename=f"voice-reply.{ext}",
            title="\U0001f50a Voice Reply",
        )
        return True
    except Exception:
        logger.exception("Channel file upload failed")
        return False


def split_sentences(text: str) -> list[str]:
    """Split text into sentences at . ! ? boundaries."""
    clean = strip_markdown(text)
    if not clean:
        return []
    parts = re.split(r"(?<=[.!?])\s+", clean)
    return [s.strip() for s in parts if s.strip()]


async def stitch_wavs(paths: list[str], output: str | None = None) -> str | None:
    """Concatenate WAV files into a single file using ffmpeg (``ffmpeg_binary.find_ffmpeg``).

    With no *output* the stitch writes a temporary file of its own and hands it to the caller to
    remove. A stitch that does not hand it back removes it here, because a caller given ``None``
    cannot: a reply spoken without ``ffmpeg`` (not a dependency) left one per request.
    """
    if not paths:
        return None
    if len(paths) == 1:
        if output:
            await asyncio.to_thread(shutil.copy2, paths[0], output)
            return output
        return paths[0]
    from personalclaw.ffmpeg_binary import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        logger.warning("ffmpeg stitch skipped: ffmpeg is not installed")
        return None
    own_output = output is None
    if output is None:
        fd, output = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
    concat = "|".join(paths)
    proc = None
    stitched: str | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-y",
            "-i",
            f"concat:{concat}",
            "-c",
            "copy",
            output,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=_STITCH_TIMEOUT)
        if proc.returncode == 0 and os.path.exists(output):
            stitched = output
    except asyncio.TimeoutError:
        # This arm did not exist. The bare `except Exception` below swallowed the timeout
        # (since 3.11 asyncio.TimeoutError IS builtins.TimeoutError), logged a traceback
        # and returned — leaving the ffmpeg child running with its deadline already spent.
        # No start_new_session: ffmpeg does not fork here, so the owner's single-pid
        # fallback is the right signal and a session would only widen a group's reach.
        if proc is not None:
            await kill_timed_out(proc)
        logger.warning("ffmpeg stitch timed out after %ss", _STITCH_TIMEOUT)
    except Exception:
        logger.exception("ffmpeg stitch failed")
    finally:
        if own_output and stitched is None:
            try:
                os.unlink(output)
            except OSError:
                pass
    return stitched


async def streaming_voice_reply(
    provider: TtsProvider,
    response_text: str,
    *,
    voice: str = "",
    speed: float = 1.0,
    speech_voice: str = "",
):
    """Async generator: yields (sentence_index, sentence_text, audio_bytes) per sentence.

    Use this for dashboard streaming — play each chunk as it arrives, then call
    ``stitch_wavs`` on the collected paths for a single replay file. Each chunk
    is synthesized via the active TTS *provider*.

    LLM output is redacted for credentials and exfiltration URLs before
    synthesis (same rationale as ``synthesize_speech``) — streaming audio
    to the dashboard bypasses the usual text-path redaction.

    Raises :class:`~personalclaw.tts.registry.TtsNotReady` before the first chunk when the
    provider says it cannot speak with *voice* now (``can_synthesize``), and a spend cap's
    refusal of a sentence (:func:`_synthesize_chunk`) where it stops, so the surface names that
    reason rather than reporting a synthesis that produced no audio.
    """
    if not await can_speak(provider, voice):
        raise TtsNotReady(provider, voice)
    response_text, cred_warns = redact_credentials(response_text)
    response_text, url_warns = redact_exfiltration_urls(response_text)
    if cred_warns:
        logger.warning(
            "stream_voice_chunks: redacted %d credential pattern(s) before TTS", len(cred_warns)
        )
    if url_warns:
        logger.warning(
            "stream_voice_chunks: redacted %d suspicious URL(s) before TTS", len(url_warns)
        )

    sentences = split_sentences(response_text)
    for i, sentence in enumerate(sentences):
        audio_path = await _synthesize_chunk(
            provider,
            sentence,
            voice=voice,
            speed=speed,
            speech_voice=speech_voice,
        )
        if not audio_path:
            continue
        try:
            with open(audio_path, "rb") as f:
                audio_bytes = f.read()
            yield i, sentence, audio_bytes
        finally:
            try:
                os.unlink(audio_path)
            except OSError:
                pass


async def voice_reply(
    channel_client: "Any",
    channel: str,
    thread_ts: str,
    response_text: str,
    *,
    provider: TtsProvider,
    voice: str = "",
    speed: float = 1.0,
    speech_voice: str = "",
) -> bool:
    """Full pipeline: text → active-provider synthesis → channel upload."""
    audio_path = await synthesize_speech(
        provider,
        response_text,
        voice=voice,
        speed=speed,
        speech_voice=speech_voice,
    )
    if not audio_path:
        return False

    try:
        return await upload_voice_to_channel(channel_client, channel, thread_ts, audio_path)
    finally:
        try:
            os.unlink(audio_path)
        except OSError:
            pass

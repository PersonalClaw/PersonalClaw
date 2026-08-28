"""Voice synthesis endpoint — streaming Piper TTS for the dashboard.

The active voice + speaking speed resolve from the unified model store
(``active_models.json`` ``tts`` selection + ``use_case_settings/tts.json``) via
``tts.registry.active_voice_params``.
"""

import base64
import contextlib
import logging
import os
import tempfile

from aiohttp import web

from personalclaw.config import AppConfig
from personalclaw.dashboard.state import DashboardState
from personalclaw.http_errors import json_error
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.tts.registry import active_voice_params
from personalclaw.voice.duplex import clean_for_speech
from personalclaw.voice.profiles import VoiceProfileError, append_history
from personalclaw.voice_reply import stitch_wavs, streaming_voice_reply

logger = logging.getLogger(__name__)


def _record_generation(params: dict, wav_path: str, text: str) -> None:
    """Append this generation to the resolved profile's bounded history (§1.2).

    Only profile-routed syntheses are recorded — the flat built-in path has no entity
    to remember them against. The stored text is a HASH, not the transcript: history
    exists to let the user re-lock a voice they liked, not to accumulate a second copy
    of everything the assistant said.
    """
    profile_id = str(params.get("profile_id") or "")
    if not profile_id:
        return
    import hashlib
    from pathlib import Path

    try:
        append_history(
            profile_id,
            Path(wav_path),
            seed=int(params.get("seed") or 0),
            text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        )
    except Exception:
        logger.debug("voice history append failed for %s", profile_id, exc_info=True)


async def api_voice_synthesize(request: web.Request) -> web.Response:
    """POST /api/voice/synthesize — sentence-chunked Piper TTS.

    Synthesizes each sentence sequentially, broadcasts ``voice_chunk``
    WS events with base64 WAV data for immediate playback, then stitches
    all chunks into a single WAV and broadcasts ``voice_complete``.
    """

    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    text = body.get("text", "")
    if not isinstance(text, str):
        return web.json_response({"error": "text must be a string"}, status=400)
    text = text.strip()
    session_name = body.get("session", "")
    if not isinstance(session_name, str):
        session_name = ""
    if not text:
        return web.json_response({"error": "text required"}, status=400)

    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)

    # MULTIMODAL-IO §4.3 — clean AFTER redaction, on the synthesis path only. The
    # chat transcript keeps the full text; only the audio drops code, URLs, paths
    # and flags. A degenerate message that cleans away to nothing (a lone CLI
    # flag) is spoken as-is rather than turned into a hollow success or an error.
    if AppConfig.load().voice.clean_for_speech_enabled:
        spoken = clean_for_speech(text)
        if spoken:
            text = spoken
        else:
            logger.debug("clean_for_speech emptied the text; speaking it unchanged")

    # MULTIMODAL-IO §3.2 — this endpoint is the dashboard's synthesis path, so its
    # surface is ``channel:webui`` unless the caller names another one; an explicit
    # ``profile_id`` ("speak as X") outranks any binding.
    surface = str(body.get("surface") or "channel:webui")
    try:
        params = active_voice_params(surface=surface, profile_id=str(body.get("profile_id") or ""))
    except VoiceProfileError as exc:
        return web.json_response({"error": exc.message, "reason": exc.reason}, status=exc.status)
    if params is None:
        return web.json_response(
            {"error": "No TTS voice selected — choose one in Settings → Models"},
            status=503,
        )
    # 🔴 HONOR THE TOGGLE. `active_voice_params` has always published `enabled` and nothing
    # read it, so Settings › Speech & Transcription › "Speak replies aloud" persisted, loaded
    # into this dict, and changed nothing: Speak synthesized either way (#651).
    #
    # This is the shape where a grep finds a "reader" that is a pass-through — the registry
    # loads the key into a dict, which looks like consumption until you follow the dict.
    #
    # Refused HERE and not only in the UI, because the endpoint is the boundary: a channel, an
    # app or a saved SOP can POST it directly. 503 matches the sibling refusal directly above —
    # both are "the feature is not available in this configuration", and the message names the
    # switch so the answer is actionable rather than a bare unavailability.
    if not params.get("enabled", False):
        return json_error(
            "tts_disabled",
            message=(
                "Text-to-speech is switched off. Turn on “Speak replies aloud” in "
                "Settings → Speech & Transcription."
            ),
            status=503,
        )

    # §4.2 — record what we are about to say so a hands-free transcription can be recognized
    # as our own speaker bleed (see api_stt_transcribe). AFTER the refusals, not before: this
    # marks the text as ours, and marking something we then decline to speak would make the
    # echo filter drop a phrase the USER said that happened to match.
    state.record_spoken(session_name, text)

    chunk_paths: list[str] = []
    final_path: str | None = None
    try:
        async for idx, sentence, wav_bytes in streaming_voice_reply(
            params["provider"],
            text,
            voice=params["voice"],
            speed=params["speed"],
            speech_voice=params["speech_voice"],
        ):
            # Save chunk for stitching

            fd, chunk_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            with open(chunk_path, "wb") as f:
                f.write(wav_bytes)
            chunk_paths.append(chunk_path)

            # Broadcast to dashboard for immediate playback
            state.broadcast_ws(
                "voice_chunk",
                {
                    "session": session_name,
                    "index": idx,
                    "sentence": sentence,
                    "audio": base64.b64encode(wav_bytes).decode(),
                },
            )

        # Stitch all chunks into single WAV
        if chunk_paths:
            final_path = await stitch_wavs(chunk_paths)
            if final_path:
                _record_generation(params, final_path, text)
                with open(final_path, "rb") as f:
                    final_bytes = f.read()
                state.broadcast_ws(
                    "voice_complete",
                    {
                        "session": session_name,
                        "audio": base64.b64encode(final_bytes).decode(),
                        "chunks": len(chunk_paths),
                    },
                )

        # Zero chunks means synthesis produced no audio (e.g. the runtime is
        # missing or every sentence failed). Report it as an error rather than a
        # hollow success so the UI can tell the user instead of going silent.
        if not chunk_paths:
            return web.json_response(
                {
                    "error": "Speech synthesis produced no audio — check the TTS runtime in Settings → AI & Models"  # noqa: E501
                },
                status=502,
            )
        return web.json_response({"ok": True, "chunks": len(chunk_paths)})
    finally:
        if final_path:
            with contextlib.suppress(OSError):
                os.unlink(final_path)
        for p in chunk_paths:
            with contextlib.suppress(OSError):
                os.unlink(p)

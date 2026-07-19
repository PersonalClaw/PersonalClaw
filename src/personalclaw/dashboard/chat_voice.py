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

from personalclaw.dashboard.state import DashboardState
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.tts.registry import active_voice_params
from personalclaw.voice_reply import stitch_wavs, streaming_voice_reply

logger = logging.getLogger(__name__)


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

    params = active_voice_params()
    if params is None:
        return web.json_response(
            {"error": "No TTS voice selected — choose one in Settings → Models"},
            status=503,
        )

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
                {"error": "Speech synthesis produced no audio — check the TTS runtime in Settings → AI & Models"},
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

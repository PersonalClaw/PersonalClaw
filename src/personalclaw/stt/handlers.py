"""HTTP handlers for /api/stt/*.

STT provider discovery + model management is served by the unified model surfaces:
- discovery/binding: ``GET /api/models/available`` (per-provider catalog, tags stt models)
- download/delete: ``POST /api/models/downloads`` + ``/api/models/local/{provider}/…``

The old ``/api/stt/providers`` + ``/api/stt/providers/{name}/models`` routes were
STT-specific duplicates of those surfaces with no FE consumer; removed as part of the
management/inference decoupling (they conflated inference-provider info with local-model
management). ``/api/stt/transcribe`` (the transcription action) lives in
``dashboard/handlers/core.py``.

``GET /api/stt/ffmpeg`` is what the Speech settings show about ffmpeg, which cuts a long
recording into parts and takes the sound out of a video before either is transcribed.
"""

from aiohttp import web


async def api_stt_ffmpeg(request: web.Request) -> web.Response:
    """GET /api/stt/ffmpeg — the ffmpeg transcription runs, or why there is none.

    ``{"path": "/opt/homebrew/bin/ffmpeg", "message": ""}`` when one is found
    (``ffmpeg_binary.find_ffmpeg``), else ``{"path": null, "message": <sentence>}``: where it was
    looked for and what to do, in the words the Speech settings show as they are.
    """
    from personalclaw.ffmpeg_binary import ffmpeg_not_found, find_ffmpeg

    path = find_ffmpeg()
    return web.json_response({"path": path, "message": "" if path else ffmpeg_not_found()})


def register_stt_routes(app) -> None:
    """The STT-only routes; the transcription action is registered beside the dashboard's own."""
    app.router.add_get("/api/stt/ffmpeg", api_stt_ffmpeg)

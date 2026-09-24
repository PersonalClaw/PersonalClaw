"""HTTP handlers for /api/stt/* — (currently none).

STT provider discovery + model management is served by the unified model surfaces:
- discovery/binding: ``GET /api/models/available`` (per-provider catalog, tags stt models)
- download/delete: ``POST /api/models/downloads`` + ``/api/models/local/{provider}/…``

The old ``/api/stt/providers`` + ``/api/stt/providers/{name}/models`` routes were
STT-specific duplicates of those surfaces with no FE consumer; removed as part of the
management/inference decoupling (they conflated inference-provider info with local-model
management). ``/api/stt/transcribe`` (the transcription action) lives in
``dashboard/handlers/core.py``.
"""


def register_stt_routes(app) -> None:
    """No STT-specific routes today (see module docstring). Kept as the registration
    seam so a future STT-only endpoint has an obvious home + one call site."""
    return

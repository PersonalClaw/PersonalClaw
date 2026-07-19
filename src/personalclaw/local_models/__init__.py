"""Unified local-model management — the ONE contract every locally-downloadable
model provider speaks, and the registry that holds them.

PersonalClaw serves many model *use-cases* (chat, embedding, stt, tts, diarization,
image-gen, …). A use-case is served by one or more *providers*, and a provider may
own **local, downloadable** models the user manages on their machine (faster-whisper's
whisper weights, piper's voices, sentence-transformers' embedders, the diarization
backends, ollama's pulled models). Historically each kind grew its own catalog route +
download path + a hardcoded name→kind map in the FE — so a new local provider (or a
use-case with two providers, like diarization) didn't fit, and a provider couldn't just
"register the models it wants to show".

This package is that missing seam. A provider implements :class:`LocalModelProvider`
(list / download / delete, optionally search); the app loader registers it here
structurally (no per-provider knowledge in core); and one management surface —
per-provider download cards, ``/api/models/available`` surfacing, and the download-job
runner — is driven entirely off this registry. Inference resolution stays in each
use-case's own registry (stt/tts/…); this is purely the *management + availability* axis.
"""

from personalclaw.local_models.provider import LocalModel, LocalModelProvider
from personalclaw.local_models.registry import (
    get_provider,
    list_providers,
    register_provider,
    unregister_provider,
)

__all__ = [
    "LocalModel",
    "LocalModelProvider",
    "register_provider",
    "unregister_provider",
    "get_provider",
    "list_providers",
]

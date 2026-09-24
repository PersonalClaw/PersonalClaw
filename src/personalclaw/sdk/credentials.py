"""SDK: the credential store + the HuggingFace token cascade.

Stable re-export of ``personalclaw.llm.credentials`` — the generic, provider-
agnostic secret store an app uses to resolve an API key/token by name (the same
store core uses; despite the ``llm`` package location it's not LLM-specific). An
app imports this, not the core module, so the core path can move.

Also re-exports the shared HuggingFace token cascade (LOCAL-MODEL-MANAGER-V2 §5):
an HF-touching app (``diarization-pyannote``, …) delegates its own token lookup to
:func:`resolve_token` instead of rolling a private two-source read, so every provider
resolves the token the same way — credential store → env → ``huggingface-cli`` file,
first whoami-valid source wins. :func:`resolve_valid_token` is the async, validated view.
"""

from personalclaw.llm.credentials import Credential, CredentialStore  # noqa: F401
from personalclaw.local_models.hf_token import (  # noqa: F401
    HfTokenResolution,
    mask_token,
    resolve_token,
    resolve_valid_token,
)

__all__ = [
    "CredentialStore",
    "Credential",
    "HfTokenResolution",
    "mask_token",
    "resolve_token",
    "resolve_valid_token",
]

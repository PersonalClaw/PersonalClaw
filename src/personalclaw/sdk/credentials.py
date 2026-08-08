"""SDK: the credential store + the HuggingFace token cascade.

Stable re-export of ``personalclaw.llm.credentials`` — the generic, provider-
agnostic secret store an app uses to resolve an API key/token by name (the same
store core uses; despite the ``llm`` package location it's not LLM-specific). An
app imports this, not the core module, so the core path can move.

Also re-exports the shared **HuggingFace token cascade** (LMMV §5): an
HF-touching provider app (diarization-pyannote and any future one) resolves the
gated-repo token through ``resolve_hf_token()`` — three sources, whoami-validated,
first valid wins — instead of rolling its own two-source lookup. ``hf_token_status``
backs the Settings surface (masked previews only; a token value never leaves the
server).
"""

from personalclaw.llm.credentials import Credential, CredentialStore  # noqa: F401
from personalclaw.local_models.hf_token import (  # noqa: F401
    hf_token_present,
    hf_token_status,
    resolve_hf_token,
)

__all__ = [
    "CredentialStore",
    "Credential",
    "resolve_hf_token",
    "hf_token_status",
    "hf_token_present",
]

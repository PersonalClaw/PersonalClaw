"""SDK: read a credential by name + the HuggingFace token cascade.

Stable re-export of ``personalclaw.llm.credentials``. ``CredentialStore(config_dir())
.resolve(NAME)`` reads a secret stored under NAME in the credential store Settings → Secrets
writes (the OS keychain when it is on, else ``<home>/.env``), the one core reads too; despite the
``llm`` package location it's not LLM-specific. An app runs in no project, so it reads the global
secret of that name. It raises ``KeyError`` for a name nothing stored, for a ``PCSECRET_…`` key,
which only the setting that owns it reads, and for a project's stored ``PCPROJ_…`` key, which only
that project's runs read (each error's message says so). An app imports this, not the core module,
so the core path can move.

Also re-exports the shared HuggingFace token cascade:
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

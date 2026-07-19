"""Diarization provider registry — resolves the active diarization backend (core L1).

Parallel to ``stt/registry.py``: which model serves ``diarization`` is the active
selection in ``active_models.json`` (ref ``provider_name:model_id``). No remote adapters
here — diarization runs locally via the diarization app; core just resolves what's bound.
"""

from __future__ import annotations

from typing import Any

from personalclaw.diarization.provider import DiarizationProvider

_providers: dict[str, DiarizationProvider] = {}


def register_provider(provider: DiarizationProvider) -> None:
    _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)


def get_provider(name: str) -> DiarizationProvider | None:
    return _providers.get(name)


def list_providers() -> list[DiarizationProvider]:
    return list(_providers.values())


def get_active_provider() -> DiarizationProvider | None:
    """The active diarization provider (bound model's provider), or — when nothing is
    bound yet — the sole registered provider, so the Settings→Models download UI can list
    + fetch candidate models BEFORE one is active (mirrors how STT downloads pre-binding).
    Returns None only when no diarization app is installed."""
    resolved = active_diarization()
    if resolved is not None:
        return resolved[0]
    provs = list(_providers.values())
    return provs[0] if len(provs) == 1 else None


def active_diarization() -> tuple[DiarizationProvider, str] | None:
    """Resolve the active diarization provider + model id from ``active_models.json``,
    or None when nothing is bound (feature simply off — no fallback, per L1.1)."""
    from personalclaw.providers.use_cases import active_model_refs, split_ref

    refs = active_model_refs("diarization")
    if not refs:
        return None
    parsed = split_ref(refs[0])
    if not parsed:
        return None
    provider_name, model_id = parsed
    prov = _providers.get(provider_name)
    if prov is None:
        return None
    return (prov, model_id)


async def list_all_providers_info() -> list[dict[str, Any]]:
    resolved = active_diarization()
    active_name = resolved[0].name if resolved else ""
    result = []
    for prov in _providers.values():
        info = prov.info()
        info["available"] = await prov.is_available()
        info["active"] = prov.name == active_name
        result.append(info)
    return result

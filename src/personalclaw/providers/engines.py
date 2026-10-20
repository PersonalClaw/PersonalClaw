"""Whether a use-case engine an app registered runs its model on this machine.

A speech-to-text, text-to-speech, diarization, embedding or image engine an app registers is no
configured model-provider entry: its binding names the app or the engine (``faster-whisper:turbo``,
``local-image:flux1-schnell``). ``llm.registry.served_on_this_machine`` asks here for a name no
entry has, so an engine is priced at its known $0, ordered local-first and scanned as a prompt that
never leaves the machine by the same rule a model server is, from what the engine DECLARES, never
from its name or its kind:

* an engine that serves its model through a runtime at an address (``endpoint``) runs here only
  when it declares that the runtime runs the model where it is (``hosts_model``, as a model server
  does) and that address is on this machine (``net.guard.reaches_this_machine``, which never
  resolves a name). An address elsewhere is not here; an address here that only passes requests
  on (a proxy for a cloud service) is not either;
* an engine that sends its requests to no address runs here when it is a local-model engine
  (``LocalModelProvider``: its models are downloaded into this home and run here). Core's download
  card for a configured model server (Ollama) is one in form only: its models live wherever that
  server is, which its entry answers for, so the card itself never makes a name local;
* anything else is not local, and a name every registry is silent on is not local either.

A name more than one engine is registered under runs here only when every one of them does: the
binding does not say which of them serves it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


def runs_here(engine: object) -> bool:
    """Whether *engine* runs its model on this machine, by what it declares (module docstring)."""
    from personalclaw.local_models.provider import LocalModelProvider
    from personalclaw.local_models.registry import adapts_an_entry
    from personalclaw.net.guard import reaches_this_machine

    if engine is None or adapts_an_entry(engine):
        return False
    try:
        endpoint = str(getattr(engine, "endpoint", "") or "").strip()
    except Exception:  # noqa: BLE001 — an engine that cannot say where it sends is not here
        logger.debug("engine %r has no readable endpoint", engine, exc_info=True)
        return False
    if endpoint:
        return getattr(engine, "hosts_model", False) is True and reaches_this_machine(endpoint)
    return isinstance(engine, LocalModelProvider)


def binding_name(engine: object) -> str:
    """The name an engine's bindings spell it by, which Model prices and Usage key it under: its
    app's name when the app registered it as a local-model engine (an app's engine may call itself
    something else), else its own name."""
    from personalclaw.local_models.registry import registered

    for key, provider in registered():
        if provider is engine:
            return key
    return str(getattr(engine, "name", "") or "")


def _lookups() -> tuple[Callable[[str], object], ...]:
    """Each registry an engine is registered in, by the name its bindings use: the local-model
    registry by app name, and every use-case registry by the engine's own name. Each is read as it
    stands: none builds an adapter for a config entry to answer (those are entries, which the
    model-provider rule answers for), so no config is read and no credential resolved."""
    from personalclaw.diarization.registry import get_provider as diarization
    from personalclaw.embedding_providers.registry import registered as embedding
    from personalclaw.image_gen.registry import get_provider as image
    from personalclaw.local_models.registry import get_provider as local_model
    from personalclaw.stt.registry import get_provider as stt
    from personalclaw.tts.registry import get_provider as tts
    from personalclaw.video_gen.registry import registered as video

    return (local_model, stt, tts, diarization, embedding, image, video)


def named_runs_here(name: str) -> bool:
    """Whether the engines registered under *name* run their model on this machine."""
    key = str(name or "").strip()
    if not key:
        return False
    engines: list[object] = []
    for lookup in _lookups():
        try:
            engine = lookup(key)
        except Exception:  # noqa: BLE001 — a registry that cannot answer names no engine here
            logger.debug("engine lookup for %r failed", key, exc_info=True)
            continue
        if engine is not None and all(engine is not seen for seen in engines):
            engines.append(engine)
    return bool(engines) and all(runs_here(engine) for engine in engines)


__all__ = ["binding_name", "named_runs_here", "runs_here"]

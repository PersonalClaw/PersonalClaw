"""Per-provider real-inference selftest + health (LMMV §6).

The local-model contract campaign proved the RUN column of the download/bind/RUN
matrix *by hand*. These two primitives automate it:

* :func:`provider_health` — a cheap "can this provider run here" answer built on the
  ABC :meth:`~personalclaw.local_models.provider.LocalModelProvider.availability_detail`.
  It NEVER raises: a provider whose availability check blows up is reported
  ``ok=False`` with the reason, so the health endpoint can't 500.

* :func:`provider_selftest` — a tiny **real inference** per capability the provider
  serves: STT transcribes a bundled 1-second fixture, TTS synthesizes a fixed phrase,
  embedding encodes one sentence and checks the vector dim, diarization runs the
  fixture through the pipeline, chat runs a short completion. Because it exercises the
  actual inference API (not just file presence), a runtime-contract break — the
  pyannote 3.x→4.x ``itertracks``/``DiarizeOutput`` change — fails the selftest with a
  typed ``runtime_contract`` reason instead of silently "passing" because the weights
  are on disk (Success Criterion 5).

The selftest is **user-click only** (it can page a model into RAM and costs compute —
never run it from a scheduler), **serialized behind a cross-process ``single_flight``
lock** so two clicks can't double-load, and **bounded by ``selftest_timeout_s``** per
capability. Every per-capability result carries a typed ``reason`` from a small closed
vocabulary the FE translates.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from personalclaw.concurrency import single_flight

logger = logging.getLogger(__name__)

#: The bundled selftest audio fixture (a quiet 1-second 220 Hz tone, 16 kHz mono) STT
#: and diarization run through their pipelines. Shipped in the wheel via
#: ``tests_fixtures/*/*`` package-data.
_FIXTURE_WAV = (
    Path(__file__).resolve().parent.parent / "tests_fixtures" / "selftest" / "tone_1s_16k_mono.wav"
)

#: The one short phrase the TTS selftest synthesizes and the chat selftest prompts with.
_SELFTEST_PHRASE = "PersonalClaw model selftest."

_DEFAULT_TIMEOUT_S = 90

#: The use-cases a selftest can drive a real inference for. A provider capability
#: outside this set (or with no bound/reachable model) is reported ``skipped``.
_TESTABLE = ("stt", "tts", "embedding", "diarization", "chat")


def _timeout_s() -> float:
    """Per-capability selftest timeout from config (best-effort; default 90s)."""
    try:
        from personalclaw.config.loader import AppConfig

        return float(max(1, int(AppConfig.load().local_models.selftest_timeout_s)))
    except Exception:
        return float(_DEFAULT_TIMEOUT_S)


async def provider_health(provider_key: str) -> dict[str, Any]:
    """A cheap health probe for one local provider — NEVER 500s (LMMV §6).

    Resolves the provider from the local-model registry and reads the ABC
    :meth:`~personalclaw.local_models.provider.LocalModelProvider.availability_detail`
    (default-wrapped over ``is_available`` for unmigrated providers). Any exception
    becomes ``{ok: false, message}`` — token values are never surfaced. Returns
    ``{provider, ok, message, latency_ms}``.
    """
    from personalclaw.local_models.registry import get_provider

    started = time.monotonic()
    provider = get_provider(provider_key)
    if provider is None:
        return {
            "provider": provider_key,
            "ok": False,
            "message": f"unknown provider {provider_key!r}",
            "latency_ms": 0,
        }
    try:
        detail = getattr(provider, "availability_detail", None)
        if callable(detail):
            ok, message = await detail()
        else:  # duck-typed provider without the additive method → wrap is_available
            ok = await provider.is_available()
            message = "ready" if ok else "unavailable"
    except Exception as exc:  # noqa: BLE001 — health must NEVER raise into the handler
        logger.debug("health probe raised for %s", provider_key, exc_info=True)
        ok, message = False, _safe_msg(exc)
    return {
        "provider": provider_key,
        "ok": bool(ok),
        "message": str(message)[:300],
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


def _safe_msg(exc: Exception) -> str:
    """A redacted one-line message for an exception — never leaks a token."""
    from personalclaw.security import redact

    return redact(str(exc))[:300] or exc.__class__.__name__


async def provider_selftest(provider_key: str, model: str | None = None) -> dict[str, Any]:
    """Run a real per-capability inference for one local provider (LMMV §6).

    Serialized behind a cross-process ``single_flight`` lock keyed on the provider so a
    double-click (or a second process) never double-loads the model — the loser returns
    a typed ``busy`` result rather than queueing. Each capability the provider serves
    (its declared capabilities, or the named model's, ∩ :data:`_TESTABLE`) runs its
    inference bounded by ``selftest_timeout_s``; a failure is isolated and typed.

    Returns ``{provider, ok, capabilities: {cap: {ok, duration_ms, detail, reason}}}``
    where ``ok`` is True iff every tested capability passed. ``reason`` is one of:
    ``""`` (ok) · ``no_model`` · ``unavailable`` · ``timeout`` · ``runtime_contract``
    (the inference API broke or returned the wrong shape — the pyannote-4 class) ·
    ``error``.
    """
    from personalclaw.local_models.registry import capabilities_for, get_provider

    provider = get_provider(provider_key)
    if provider is None:
        return {
            "provider": provider_key,
            "ok": False,
            "capabilities": {},
            "detail": f"unknown provider {provider_key!r}",
        }

    caps = _capabilities_to_test(provider_key, model, capabilities_for(provider_key))
    if not caps:
        return {
            "provider": provider_key,
            "ok": True,
            "capabilities": {},
            "detail": "no testable capability",
        }

    timeout = _timeout_s()
    with single_flight(f"model_selftest:{provider_key}") as acquired:
        if not acquired:
            return {
                "provider": provider_key,
                "ok": False,
                "capabilities": {},
                "detail": "a selftest for this provider is already running",
                "reason": "busy",
            }
        results: dict[str, dict[str, Any]] = {}
        for cap in caps:
            results[cap] = await _run_one(provider, provider_key, cap, model, timeout)

    return {
        "provider": provider_key,
        "ok": all(r["ok"] for r in results.values()),
        "capabilities": results,
    }


def _capabilities_to_test(provider_key: str, model: str | None, declared: list[str]) -> list[str]:
    """The testable capabilities for this run — the named model's if given, else the
    provider's declared set, intersected with :data:`_TESTABLE` (order preserved)."""
    pool = set(declared)
    return [c for c in _TESTABLE if c in pool]


async def _run_one(
    provider: Any, provider_key: str, capability: str, model: str | None, timeout: float
) -> dict[str, Any]:
    """Run ONE capability's real inference, timed + typed. Never raises."""
    started = time.monotonic()
    try:
        detail = await asyncio.wait_for(
            _dispatch(provider, provider_key, capability, model), timeout=timeout
        )
        ok, reason, text = detail
        return {
            "ok": ok,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "detail": text,
            "reason": reason if not ok else "",
        }
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "detail": f"timed out after {int(timeout)}s",
            "reason": "timeout",
        }
    except Exception as exc:  # noqa: BLE001 — one capability's failure is isolated + typed
        # An inference that RAISES is the pyannote-4 runtime-contract break class: the
        # weights are present but the API surface changed, so the call blows up. Typed
        # as runtime_contract (not error) so the FE can say "this model's runtime is
        # incompatible" rather than a generic failure.
        logger.debug("selftest %s/%s raised", provider_key, capability, exc_info=True)
        return {
            "ok": False,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "detail": _safe_msg(exc),
            "reason": "runtime_contract",
        }


async def _dispatch(
    provider: Any, provider_key: str, capability: str, model: str | None
) -> tuple[bool, str, str]:
    """Dispatch the real inference for one capability → ``(ok, reason, detail_text)``.

    A bundled local provider subclasses BOTH its use-case ABC and ``LocalModelProvider``
    on the same object, so the inference method lives right on the registered provider
    (``transcribe``/``synthesize``/``embed``/``diarize``). Chat is the exception —
    ollama's local provider is a management-only adapter, so chat routes through
    ``one_shot_completion`` pinned to ``provider:model``. A missing method / no model
    yields ``no_model`` (nothing to run), not a failure of the runtime itself.
    """
    if capability == "chat":
        return await _selftest_chat(provider_key, model)

    if capability == "embedding":
        embed = getattr(provider, "embed", None)
        if not callable(embed):
            return False, "no_model", "provider exposes no embedding inference"
        vec = await embed(_SELFTEST_PHRASE, model=model or "")
        if not vec:
            return False, "runtime_contract", "embed returned no vector"
        return True, "", f"{len(vec)} dims"

    if capability == "stt":
        transcribe = getattr(provider, "transcribe", None)
        if not callable(transcribe):
            return False, "no_model", "provider exposes no STT inference"
        text = await transcribe(str(_FIXTURE_WAV), model=model or "")
        if text is None:
            return False, "runtime_contract", "transcribe returned None"
        return True, "", f"transcribed ({len(text)} chars)"

    if capability == "tts":
        synthesize = getattr(provider, "synthesize", None)
        if not callable(synthesize):
            return False, "no_model", "provider exposes no TTS inference"
        out_path = os.path.join(tempfile.gettempdir(), f"pclaw-selftest-{os.getpid()}.wav")
        path: str | None = None
        try:
            path = await synthesize(_SELFTEST_PHRASE, voice=model or "", output_path=out_path)
            if not path or not os.path.exists(path):
                return False, "runtime_contract", "synthesize produced no audio file"
            size = os.path.getsize(path)
            return (size > 0), ("runtime_contract" if size == 0 else ""), f"{size} bytes"
        finally:
            for p in {out_path, path}:
                if isinstance(p, str) and os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

    if capability == "diarization":
        diarize = getattr(provider, "diarize", None)
        if not callable(diarize):
            return False, "no_model", "provider exposes no diarization inference"
        turns = await diarize(str(_FIXTURE_WAV), model=model or "")
        if turns is None:
            return False, "runtime_contract", "diarize returned None"
        return True, "", f"{len(turns)} turn(s)"

    return False, "no_model", f"capability {capability!r} is not selftestable"


async def _selftest_chat(provider_key: str, model: str | None) -> tuple[bool, str, str]:
    """A short chat completion pinned to this provider (ollama's local chat path).

    Uses ``one_shot_completion`` with a ``provider:model`` pin so the completion runs
    on the provider under test, not whatever the active chat chain resolves to. No
    model to pin → ``no_model``.
    """
    if not model:
        return False, "no_model", "chat selftest needs a model to pin"
    from personalclaw.llm_helpers import one_shot_completion

    text = await one_shot_completion(
        _SELFTEST_PHRASE, use_case="background", model=f"{provider_key}:{model}"
    )
    if text is None:
        return False, "runtime_contract", "completion returned None"
    return True, "", f"completion returned ({len(text)} chars)"

"""A model's Test: one small real call showing whether a model works for one use case.

Settings → Models offers a Test on every model row, for the use case the row is listed under. The
Test makes the call that use case makes at runtime, with the smallest input that still proves
something — a one-word reply, one embedded word, a half-second tone, one image at the smallest
size the model offers — and answers in words a person reads: what came back, or why nothing did.

ONE path serves every use case and every provider, hosted or on this machine. The row names its
use case and its ``provider:model`` ref, and the provider is resolved the way that use case
resolves it at runtime: the model bridge for chat, its routing sub-uses and image understanding
(:func:`~personalclaw.providers.provider_bridge.resolve_metered_model`, the model itself behind
the spend guard), and the typed registries for embedding, speech, diarization and images. Nothing
here asks whether a model is local.

A Test that cannot work is never offered (:func:`untestable_reason`, which ``GET
/api/models/available`` puts on each row it applies to, and which ``POST /api/models/test``
refuses with). Three things say so:

* the use case — nothing in PersonalClaw calls a model for audio or video understanding or for
  audio generation yet, and the smallest video a model makes is a whole clip (:data:`_NO_TEST`);
* the provider — each typed provider ABC's ``untestable_reason()``, for one whose smallest real
  call is still too slow or costly to make on a click;
* the provider's record — a chat provider type that does not take images has nothing to look at
  in an image-understanding Test, and a provider that is not set up cannot be called.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any

from personalclaw.media_fixtures import solid_png, write_tone_wav
from personalclaw.providers.failure_copy import failure_detail

logger = logging.getLogger(__name__)

#: The most a chat Test lets a model write. Enough for a reasoning model to think briefly before
#: its one word, small enough that a model which runs on cannot make the Test costly.
TEST_REPLY_TOKENS = 1024

_REPLY_PROMPT = "Reply with the single word OK."
_IMAGE_PROMPT = "What colour is this square? Answer in one word."
_IMAGE_GEN_PROMPT = "A plain red circle on a white background."
_TEST_WORD = "hello"
_SPOKEN_LINE = "Test."
_RED = (220, 40, 40)

#: Use cases with no Test, and why. The first three are use cases nothing in PersonalClaw calls a
#: model for yet; the last has no small call to make.
_NO_TEST: dict[str, str] = {
    "audio_modality": (
        "PersonalClaw doesn't hand a model audio to understand yet, so there is no call a Test "
        "could make."
    ),
    "video_modality": (
        "PersonalClaw doesn't hand a model video to understand yet, so there is no call a Test "
        "could make."
    ),
    "audio_gen": (
        "PersonalClaw doesn't ask a model to make audio yet, so there is no call a Test could make."
    ),
    "video_gen": (
        "A Test would have to make a whole video clip, which is slow and costly, so video models "
        "have no Test."
    ),
}

#: What each typed capability is called in a sentence about a provider that does not serve it.
_SERVES: dict[str, str] = {
    "embedding": "embeddings",
    "stt": "speech-to-text",
    "tts": "text-to-speech",
    "diarization": "speaker diarization",
    "image_gen": "image generation",
}


@dataclass(frozen=True)
class ModelTestResult:
    """What one model's Test found.

    ``detail`` is the sentence Settings → Models shows under the row: what came back, or why
    nothing did. ``reason`` is ``""`` on success and, on failure, a stable code a client may
    branch on (``timeout``, ``empty_reply``, ``unavailable``, a provider's own typed reason such
    as ``sidecar_crashed:<why>``, or ``error:<ExceptionClass>``).
    """

    ok: bool
    detail: str
    reason: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "reason": self.reason,
            "duration_ms": self.duration_ms,
        }


class ModelUntestable(Exception):
    """The use case or the provider has no Test; the message is the sentence saying why."""


class ModelTestRunning(Exception):
    """A Test of one of the same provider's models is already running."""


# ── Which Tests can run ────────────────────────────────────────────────────────────────────


def untestable_reason(use_case: str, provider_name: str) -> str:
    """Why a Test can't run on ``provider_name``'s models for ``use_case``, or ``""`` when it can.

    Answered from what is registered now and never by calling a model, so ``GET
    /api/models/available`` asks it for every row. A provider that cannot be looked up is offered
    its Test (``""``): the click is what says why it fails.
    """
    from personalclaw.providers.use_cases import VALID_USE_CASES, parent_capability

    if use_case not in VALID_USE_CASES:
        return f"“{use_case}” isn't a use case Settings → Models lists."
    capability = parent_capability(use_case)
    if capability in _NO_TEST:
        return _NO_TEST[capability]
    try:
        if capability in ("chat", "image_modality"):
            return _model_entry_refusal(capability, provider_name)
        if capability == "embedding":
            from personalclaw.embedding_providers.registry import direct_provider

            direct = direct_provider(provider_name)
            if direct is None:
                return _model_entry_refusal(capability, provider_name)
            return _declared(direct)
        provider = _typed_provider(capability, provider_name)
    except Exception:  # noqa: BLE001 — a lookup that fails offers the Test; the click says why
        logger.debug("model test: could not look up %r for %s", provider_name, use_case)
        return ""
    if provider is None:
        return _not_set_up(capability, provider_name)
    return _declared(provider)


def mark_untestable(rows: list[dict[str, Any]]) -> None:
    """Put ``untestable`` — ``{use case: why}`` — on each ``/api/models/available`` model that
    has a use case whose Test can't run, so its row says so instead of offering the Test.

    Keyed by use case, not provider: a provider's chat models can be tested while its video
    models cannot. Each ``(use case, provider)`` is looked up once per call.
    """
    from personalclaw.providers.use_cases import VALID_USE_CASES

    seen: dict[tuple[str, str], str] = {}
    for row in rows:
        for model in row.get("models") or []:
            provider_name = str(model.get("provider") or row.get("name") or "")
            refusals: dict[str, str] = {}
            for use_case in model.get("capabilities") or []:
                if use_case not in VALID_USE_CASES:
                    continue
                key = (use_case, provider_name)
                if key not in seen:
                    seen[key] = untestable_reason(use_case, provider_name)
                if seen[key]:
                    refusals[use_case] = seen[key]
            if refusals:
                model["untestable"] = refusals


def _typed_provider(capability: str, name: str) -> Any:
    """The provider the typed registry for ``capability`` resolves ``name`` to, or None."""
    if capability == "stt":
        from personalclaw.stt.registry import provider_named

        return provider_named(name)
    if capability == "tts":
        from personalclaw.tts.registry import provider_named as tts_provider_named

        return tts_provider_named(name)
    if capability == "diarization":
        from personalclaw.diarization.registry import get_provider

        return get_provider(name)
    if capability == "image_gen":
        from personalclaw.image_gen.registry import provider_named as image_provider_named

        return image_provider_named(name)
    return None


def _declared(provider: object) -> str:
    """The provider's own ``untestable_reason()``, on one line. A provider that does not carry the
    method, or whose answer fails, has declared nothing."""
    declare = getattr(provider, "untestable_reason", None)
    if not callable(declare):
        return ""
    try:
        return " ".join(str(declare() or "").split())
    except Exception:  # noqa: BLE001 — a declaration that fails has declared nothing
        logger.debug("model test: %r could not say whether it can be tested", provider)
        return ""


def _model_entry_refusal(capability: str, provider_name: str) -> str:
    """Why a configured model provider's models can't be tested for ``capability``, or ``""``.

    The model bridge builds a model for chat only on an entry that declares chat, and for image
    understanding only on one that declares vision, whose wire also carries an image
    (``supports_vision``, which ``providers.image_input`` reads): what the entry declares is the
    provider saying whether its models can be called that way at all. Embedding through a model
    provider asks only that the entry exists, as a binding to it does.
    """
    from personalclaw.llm.capabilities import Capability
    from personalclaw.llm.registry import ProviderResolutionError, get_default_registry

    registry = get_default_registry()
    try:
        entry = registry.get_entry(provider_name)
    except ProviderResolutionError:
        return (
            f"No model provider named “{provider_name}” is set up, so its models can't be called."
        )
    if capability == "embedding":
        return ""
    try:
        declared = registry.capability_of(entry.type)
    except ProviderResolutionError:
        return ""  # its app is not loaded: the click says what building it answers
    serves = entry.declared_capabilities or declared.capabilities
    if capability == "chat":
        if Capability.CHAT in serves:
            return ""
        return f"{provider_name} doesn't serve chat, so its models can't be called for it."
    if Capability.VISION in serves and declared.supports_vision:
        return ""
    return f"{provider_name} can't be handed an image, so an image Test has nothing to show it."


def _not_set_up(capability: str, provider_name: str) -> str:
    return (
        f"{provider_name} isn't set up for {_SERVES.get(capability, capability)} here, so its "
        "models can't be called."
    )


# ── Running one ────────────────────────────────────────────────────────────────────────────


async def run_model_test(use_case: str, provider_name: str, model: str) -> ModelTestResult:
    """Test ``provider_name:model`` for ``use_case`` with one small real call.

    Raises :class:`ModelUntestable` when :func:`untestable_reason` says no Test can run, and
    :class:`ModelTestRunning` while another Test of one of this provider's models runs — a Test
    can page a model into memory, so two at once are refused rather than queued. Every other
    outcome, a failure included, is a :class:`ModelTestResult`, bounded by
    ``local_models.selftest_timeout_s``.
    """
    from personalclaw.concurrency import single_flight
    from personalclaw.providers.use_cases import parent_capability

    refusal = untestable_reason(use_case, provider_name)
    if refusal:
        raise ModelUntestable(refusal)
    probe = _PROBES[parent_capability(use_case)]
    timeout = _timeout_s()
    with single_flight(f"model-test:{provider_name}") as acquired:
        if not acquired:
            raise ModelTestRunning(provider_name)
        started = time.monotonic()
        # A task, not `wait_for`: a provider's OWN timeout raises TimeoutError too, and it is the
        # provider's failure, said in its words — only the Test's bound running out is "no answer".
        task = asyncio.ensure_future(probe(use_case, provider_name, model))
        try:
            done, _ = await asyncio.wait({task}, timeout=timeout)
        except asyncio.CancelledError:
            task.cancel()  # the request went away: so does its call
            task.add_done_callback(_settled)
            raise
        if not done:
            task.cancel()
            task.add_done_callback(_settled)
            result = ModelTestResult(False, f"No answer within {timeout:g} seconds.", "timeout")
        elif task.cancelled():
            result = ModelTestResult(False, "The Test was stopped before it finished.", "stopped")
        elif (failure := task.exception()) is not None:
            result = _failed_by(failure)
        else:
            result = task.result()
        return replace(result, duration_ms=round((time.monotonic() - started) * 1000))


def _settled(task: asyncio.Future) -> None:
    """Read a cancelled probe's outcome when it ends, so nothing is reported as never retrieved."""
    if not task.cancelled():
        task.exception()


def _timeout_s() -> float:
    """``local_models.selftest_timeout_s`` (the Settings → Models "Model Test timeout")."""
    try:
        from personalclaw.config.loader import AppConfig

        return float(AppConfig.load().local_models.selftest_timeout_s)
    except Exception:  # noqa: BLE001 — an unreadable config bounds the Test by the default
        return 90.0


def _failed_by(exc: BaseException) -> ModelTestResult:
    """The failure an exception is: the provider's own sentence, masked and on one line, and its
    typed reason when it carries one (a sidecar that died says ``sidecar_crashed:<why>``).

    A Test is a check someone asked for, so the failure's own words are the answer they asked to
    see — :func:`~personalclaw.providers.failure_copy.failure_detail` masks them first and
    withholds a text it cannot mask.
    """
    typed = str(getattr(exc, "typed_reason", "") or "")
    detail = _unreachable(exc) or failure_detail(str(exc)) or failure_detail(typed)
    return ModelTestResult(
        False,
        detail or f"It failed with {type(exc).__name__}.",
        typed or f"error:{type(exc).__name__}",
    )


def _unreachable(exc: BaseException) -> str:
    """The guidance sentence for a provider that could not be reached at all — refused, a host
    that does not resolve, a TLS handshake that failed — found anywhere in the failure's cause
    chain, or ``""``. An HTTP client's own words for it ("All connection attempts failed") say
    neither what happened nor what to check; the sentence every connection check uses says both.
    A timeout is not one of these: a provider that timed out says so in its own words."""
    import socket
    import ssl

    from personalclaw.providers.failure_copy import connectivity_guidance

    seen: set[int] = set()
    link: BaseException | None = exc
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        if isinstance(link, (ConnectionRefusedError, socket.gaierror, ssl.SSLError)):
            return connectivity_guidance(link) or ""
        link = link.__cause__ or link.__context__
    return ""


def _passed(detail: str) -> ModelTestResult:
    return ModelTestResult(True, detail)


def _failed(detail: str, reason: str) -> ModelTestResult:
    return ModelTestResult(False, detail, reason)


def _ending_in(said: str, text: str, limit: int = 80) -> str:
    """``said`` and then ``text`` in quotes, as one sentence: what came back, on one line and cut at
    ``limit``, with the sentence's full stop left to the quote when the quote already ends one."""
    words = " ".join(str(text).split())
    if len(words) > limit:
        words = words[:limit].rstrip() + "…"
    return f"{said} “{words}”" + ("" if words.endswith((".", "!", "?", "…")) else ".")


@contextmanager
def _scratch(suffix: str) -> Iterator[str]:
    """A temporary file path for one Test, removed when the Test is done with it."""
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="pc-model-test-")
    os.close(fd)
    try:
        yield path
    finally:
        _remove(path)


def _remove(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


async def _said(provider: object, method: str) -> str:
    """What ``provider.<method>()`` says (its ``unavailable_reason``), masked, or ``""``."""
    ask = getattr(provider, method, None)
    if not callable(ask):
        return ""
    try:
        return failure_detail(str(await ask() or ""))
    except Exception:  # noqa: BLE001 — a reason is best-effort; the Test has its own words
        return ""


async def _why_unavailable(provider: Any) -> str:
    """``""`` when ``provider`` says it can run now, else the sentence why: its own words
    (``availability_detail()`` for a provider that manages models on this machine,
    ``unavailable_reason()`` otherwise), or a plain one when it says nothing."""
    shown = str(getattr(provider, "display_name", "") or getattr(provider, "name", "") or "")
    plain = f"{shown or 'This provider'} can't run right now."
    detail = getattr(provider, "availability_detail", None)
    if callable(detail):
        ok, message = await detail()
        return "" if ok else (failure_detail(str(message or "")) or plain)
    if await provider.is_available():
        return ""
    return await _said(provider, "unavailable_reason") or plain


# ── The probes, one per capability ─────────────────────────────────────────────────────────


async def _reply_budget(ref: str) -> int:
    """The model's own output budget, capped at :data:`TEST_REPLY_TOKENS`."""
    try:
        from personalclaw.local_models.budgets import output_budget

        budget = int(await output_budget(ref))
    except Exception:  # noqa: BLE001 — an unknown budget takes the cap
        return TEST_REPLY_TOKENS
    return min(TEST_REPLY_TOKENS, budget) if budget > 0 else TEST_REPLY_TOKENS


async def _reply(use_case: str, ref: str, messages: list[dict[str, Any]]) -> str:
    """One completion of ``messages`` on the model ``ref`` names, resolved as ``use_case``
    resolves it — the model itself, pinned (no fallback chain), behind the spend guard — with its
    usage row written as an evaluation's."""
    from personalclaw.guardrails.local_queue import Attended, attending
    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK
    from personalclaw.providers.provider_bridge import resolve_metered_model
    from personalclaw.usage_ledger import Attribution, recorder

    provider = resolve_metered_model(
        use_case, model_override=ref, max_tokens=await _reply_budget(ref)
    )
    record = recorder(provider, Attribution(source="eval"))
    parts: list[str] = []
    await provider.start()
    try:
        # The row's Test waits on this, so on a local model it goes ahead of background work.
        with attending(Attended("Testing the model")):
            async for event in provider.complete(messages):
                if event.kind == EVENT_TEXT_CHUNK:
                    parts.append(getattr(event, "text", "") or "")
                elif event.kind == EVENT_COMPLETE:
                    record(event)
    finally:
        try:
            await provider.shutdown()
        except Exception:  # noqa: BLE001 — the answer is in; a shutdown fault does not change it
            logger.debug("model test: shutdown after the reply failed", exc_info=True)
    return "".join(parts)


async def _test_reply(use_case: str, provider_name: str, model: str) -> ModelTestResult:
    """Chat and its routing sub-uses: one short message, a one-word answer expected."""
    text = await _reply(
        use_case, f"{provider_name}:{model}", [{"role": "user", "content": _REPLY_PROMPT}]
    )
    if text.strip():
        return _passed(_ending_in("Replied", text))
    return _failed("Answered with an empty reply.", "empty_reply")


async def _test_image_reading(use_case: str, provider_name: str, model: str) -> ModelTestResult:
    """Image understanding: a small red square, and the question what colour it is."""
    from personalclaw.providers.image_input import image_input

    ref = f"{provider_name}:{model}"
    takes = await image_input(ref)
    if not takes.accepted:
        return _failed(takes.reason, "takes_no_images")
    picture = base64.b64encode(solid_png(_RED)).decode("ascii")
    content = [
        {"type": "text", "text": _IMAGE_PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{picture}"}},
    ]
    text = await _reply(use_case, ref, [{"role": "user", "content": content}])
    if text.strip():
        return _passed(_ending_in("Looked at a small red square and replied", text))
    return _failed("Looked at a small red square and answered with an empty reply.", "empty_reply")


async def _test_embedding(use_case: str, provider_name: str, model: str) -> ModelTestResult:
    """Embedding: one word, through the provider the binding would embed with."""
    from personalclaw.embedding_providers.registry import direct_provider

    direct = direct_provider(provider_name)
    if direct is None:
        return await _embed_through_model_provider(provider_name, model)
    why = await _why_unavailable(direct)
    if why:
        return _failed(why, "unavailable")
    vector = await direct.embed(_TEST_WORD, model=model)
    if not vector:
        # The contract answers a failed embedding with None; the provider says why here.
        said = await _said(direct, "unavailable_reason")
        return _failed(said or "Returned no vector.", "no_vector")
    return _embedded(vector)


async def _embed_through_model_provider(provider_name: str, model: str) -> ModelTestResult:
    """A configured model provider (Ollama, an OpenAI-compatible endpoint) embeds through its own
    ``embed()``, built with the model as its ``embedding_model``, as a binding to it is."""
    from personalclaw.llm.registry import get_default_registry

    provider = get_default_registry().build(provider_name, embedding_model=model)
    embed: Callable[[list[str]], Awaitable[list[list[float]]]] | None = getattr(
        provider, "embed", None
    )
    if not callable(embed):
        return _failed(f"{provider_name} doesn't embed.", "does_not_embed")
    await provider.start()
    try:
        vectors = await embed([_TEST_WORD])
    finally:
        try:
            await provider.shutdown()
        except Exception:  # noqa: BLE001 — the answer is in; a shutdown fault does not change it
            logger.debug("model test: shutdown after the embedding failed", exc_info=True)
    vector = list(vectors[0]) if vectors else []
    if not vector:
        return _failed("Returned no vector.", "no_vector")
    return _embedded(vector)


def _embedded(vector: list[float]) -> ModelTestResult:
    return _passed(f"Embedded a test word into {len(vector):,} dimensions.")


async def _test_transcription(use_case: str, provider_name: str, model: str) -> ModelTestResult:
    """Speech-to-text: a half-second tone, which holds no speech."""
    from personalclaw.stt.registry import provider_named

    provider = provider_named(provider_name)
    if provider is None:
        return _failed(_not_set_up("stt", provider_name), "not_set_up")
    why = await _why_unavailable(provider)
    if why:
        return _failed(why, "unavailable")
    with _scratch(".wav") as clip:
        write_tone_wav(clip)
        text = await provider.transcribe(clip, model=model)
    if text is None:
        return _failed("Returned no transcript.", "no_transcript")
    heard = " ".join(text.split())
    if heard:
        return _passed(_ending_in("Transcribed a half-second test tone as", heard))
    return _passed(
        "Transcribed a half-second test tone. It holds no speech, so no words came back."
    )


async def _test_speech(use_case: str, provider_name: str, model: str) -> ModelTestResult:
    """Text-to-speech: one word, through the chokepoint every synthesis takes
    (``tts.registry.route_synthesis``) — conditioned on a generated reference clip when the
    provider clones, so the clone path is what runs."""
    from personalclaw.providers.use_cases import load_use_case_settings
    from personalclaw.tts.registry import provider_named

    provider = provider_named(provider_name)
    if provider is None:
        return _failed(_not_set_up("tts", provider_name), "not_set_up")
    why = await _why_unavailable(provider)
    if why:
        return _failed(why, "unavailable")
    settings = load_use_case_settings("tts")
    try:
        speed = float(settings.get("speed", 1.0) or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    params: dict[str, Any] = {
        "provider": provider,
        "voice": model,
        "speed": speed,
        "speech_voice": str(settings.get("speech_voice", "") or ""),
    }
    if not getattr(provider, "supports_cloning", False):
        if await _spoke(params):
            return _passed("Spoke a one-word test line.")
        return _failed("Made no audio.", "no_audio")
    with _scratch(".wav") as reference:
        write_tone_wav(reference)
        cloned = await _spoke({**params, "ref_audio": reference, "ref_text": "reference clip"})
    if cloned:
        return _passed("Spoke a one-word test line in a voice cloned from a test clip.")
    return _failed("Made no audio.", "no_audio")


async def _spoke(params: dict[str, Any]) -> bool:
    """Whether one synthesis of :data:`_SPOKEN_LINE` wrote audio. The output is a path made here,
    so a path coming back proves nothing: the file has to hold audio. It is removed afterwards,
    with any other path the provider chose to return."""
    from personalclaw.tts.provider import wrote_audio
    from personalclaw.tts.registry import route_synthesis

    result: object = None
    with _scratch(".wav") as out:
        try:
            result = await route_synthesis(params, _SPOKEN_LINE, output_path=out)
            return wrote_audio(result)
        finally:
            if isinstance(result, str) and result and result != out:
                _remove(result)


async def _test_diarization(use_case: str, provider_name: str, model: str) -> ModelTestResult:
    """Speaker diarization: a half-second tone, through the model's own pipeline."""
    from personalclaw.diarization.registry import get_provider

    provider = get_provider(provider_name)
    if provider is None:
        return _failed(_not_set_up("diarization", provider_name), "not_set_up")
    why = await _why_unavailable(provider)
    if why:
        return _failed(why, "unavailable")
    with _scratch(".wav") as clip:
        write_tone_wav(clip)
        turns = await provider.diarize(clip, model=model)
    if turns is None:
        return _failed("Returned nothing.", "no_turns")
    count = len(turns)
    return _passed(
        f"Ran on a half-second test tone and found {count} speaker turn{'' if count == 1 else 's'}."
    )


async def _test_image_generation(use_case: str, provider_name: str, model: str) -> ModelTestResult:
    """Image generation: one image, at the smallest size the model says it makes."""
    from personalclaw.image_gen.registry import provider_named

    provider = provider_named(provider_name)
    if provider is None:
        return _failed(_not_set_up("image_gen", provider_name), "not_set_up")
    why = await _why_unavailable(provider)
    if why:
        return _failed(why, "unavailable")
    size = await _smallest_size(provider, model)
    images = await provider.generate(_IMAGE_GEN_PROMPT, model=model, size=size, n=1)
    if not any(i.b64 or i.url or i.local_path for i in images or []):
        return _failed("Made no image.", "no_image")
    if size:
        return _passed(f"Made one {size.replace('x', '×')} image.")
    return _passed("Made one image at the model's default size.")


async def _smallest_size(provider: Any, model: str) -> str:
    """The smallest of ``model``'s declared ``sizes`` (``"512x512"``) by area, or ``""`` — the
    provider's default — when it declares none that read as a width and a height."""
    try:
        listed = await provider.list_models()
    except Exception:  # noqa: BLE001 — an unlisted model takes the provider's default size
        return ""
    sizes = next((list(m.sizes or []) for m in listed if m.name == model), [])
    areas: list[tuple[int, str]] = []
    for size in sizes:
        width, _, height = str(size).lower().partition("x")
        if width.isdigit() and height.isdigit():
            areas.append((int(width) * int(height), str(size)))
    return min(areas)[1] if areas else ""


_PROBES: dict[str, Callable[[str, str, str], Awaitable[ModelTestResult]]] = {
    "chat": _test_reply,
    "image_modality": _test_image_reading,
    "embedding": _test_embedding,
    "stt": _test_transcription,
    "tts": _test_speech,
    "diarization": _test_diarization,
    "image_gen": _test_image_generation,
}

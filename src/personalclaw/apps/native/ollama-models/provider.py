"""Ollama provider — local Ollama server via the ``httpx`` HTTP client.

The ``httpx`` SDK is imported lazily inside :meth:`OllamaProvider.__init__`
to satisfy Requirement R6.5 / Property 11 (Provider SDK Lazy Import). The
module file itself is safe to import without ``httpx`` installed: only
constructing an :class:`OllamaProvider` instance triggers the SDK import.

A factory is registered with the default :class:`ProviderRegistry` on
module import — the app loader imports this module when the app is enabled,
wiring the ``ollama`` provider type without pulling the SDK into
``sys.modules``.

Ollama exposes a local HTTP server (default ``http://localhost:11434``)
with two relevant endpoints:

* ``POST /api/chat`` with ``stream=true`` returns newline-delimited JSON
  (NDJSON). Each line is one of:

  - a streaming chunk: ``{"message": {"role": "assistant", "content": "..."},
    "done": false}``
  - a final summary: ``{"done": true, "prompt_eval_count": N,
    "eval_count": M, ...}``

* ``POST /api/embed`` with ``{"model": ..., "input": [...]}`` returns
  ``{"embeddings": [[...], ...]}``.

The endpoint is overridable via ``ProviderEntry.options.endpoint``.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

# Re-exported here for the ``ModelManager`` axis' quoted annotations. The RUNTIME import
# stays inside :meth:`OllamaProvider._to_local` — ``sdk.local_model`` is only needed when
# the download surface actually asks for a LocalModel, and this module must stay importable
# without touching that path. Under TYPE_CHECKING the name exists for the annotations and
# for the type checker, and at runtime it costs nothing.
if TYPE_CHECKING:
    from personalclaw.sdk.local_model import LocalModel

# Every name below is part of the app's own surface: some are used directly, the rest are
# re-exported so the app's tests and any sibling module import them from here rather than
# reaching around the SDK facade. F401 is therefore blanket for this block, not per-name.
from personalclaw.sdk.model import (  # noqa: F401
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    KIND_OUTSIDE,
    CancelOutcome,
    Capability,
    ConnectionResult,
    ContextGauge,
    Credential,
    LLMEvent,
    ModelInfo,
    ModelManager,
    ModelProvider,
    PromptCache,
    ProviderCapability,
    ProviderEntry,
    ProviderResolutionError,
    PullProgress,
    StructuredOutput,
    declared_context_window,
    get_default_registry,
    infer_capabilities,
    make_think_splitter,
    prompt_text_chars,
)

logger = logging.getLogger(__name__)

# Default endpoint when no override is supplied via entry ``options.endpoint``.
_DEFAULT_ENDPOINT = "http://localhost:11434"

# Max conversation history entries before trimming oldest. Mirrors the
# convention used by ``providers/openai.py`` and ``providers/anthropic.py``.
_MAX_HISTORY = 50

# Default request timeout (seconds). Streaming bodies are read on the
# pooled connection so the timeout applies to connect/write/pool, not to
# the total streaming duration.
_DEFAULT_TIMEOUT = 60.0


def _timeout_or_default(raw: object) -> float:
    """A configured timeout, accepting the STRING the config actually stores.

    Settings persists provider options as strings — a real dev home carries
    ``"timeout_secs": "120"`` — so the previous ``isinstance(raw, (int, float))``
    test was False for every value a user had ever typed, and every configured
    timeout was silently replaced by :data:`_DEFAULT_TIMEOUT`. Measured: a chat
    turn against a local 12B reasoning model died on ``httpx.ReadTimeout`` at
    ~60 s with ``timeout_secs`` set to 900.

    Garbage still falls back to the default rather than raising — a malformed
    option must not make the provider unbuildable — and a non-positive value is
    treated as garbage, since a 0 s timeout would fail every request.
    """
    if isinstance(raw, bool) or raw is None:  # bool is an int subclass; not a timeout
        return _DEFAULT_TIMEOUT
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT
    if value != value or value <= 0:  # NaN or non-positive
        return _DEFAULT_TIMEOUT
    return value


# ── Native structured output (AUTONOMY-GUARDRAILS §2.4) ────────────────────
#
# Ollama enforces a JSON Schema server-side via a top-level ``format`` field on
# ``/api/chat``: pass a schema OBJECT and the sampler is constrained to emit a
# conforming document; pass the string ``"json"`` for unschema'd JSON mode. That is
# native enforcement — the parser IS the generator — as opposed to asking a model in
# prose for JSON and repairing whatever comes back.
#
# The graded capability this provider advertises (see OLLAMA_CAPABILITY) is core's
# ``StructuredOutput`` enum, imported from the SDK facade with the rest of the model
# contract. It used to be reconstructed here as
# ``type(ProviderCapability.__dataclass_fields__["structured_output"].default)``, because
# the enum was not re-exported and an app may import core ONLY through
# ``personalclaw.sdk.*``. That yielded the right object but was unreadable and untypeable —
# mypy sees ``type[Any] | type[Literal[_MISSING_TYPE.MISSING]]`` and cannot resolve
# ``.JSON_SCHEMA`` on it. The native capability contract's own remedy for a symbol an app
# legitimately needs is to PROMOTE it to the SDK, which is what happened.

#: Ollama's own wire spelling of the constraint, and the build-option key a caller uses
#: to request one. ``format`` is the wire field; ``output_type`` is the platform's name
#: for the request (``one_shot_completion(output_type=…)``), accepted so the seam is
#: spelled the same on both sides of the bridge.
_FORMAT_FIELD = "format"
_OUTPUT_TYPE_KEY = "output_type"

#: The unschema'd JSON mode ollama accepts in place of a schema object.
_JSON_MODE = "json"

#: The request-body object ollama reads its sampling parameters from (`temperature`, `top_p`,
#: `num_ctx`, …). A sampling parameter placed at the top level of the body is ignored.
_WIRE_OPTIONS = "options"


def native_format(requested: object) -> object | None:
    """Normalize a requested output shape into ollama's native ``format`` value.

    Returns the value to put on the wire, or ``None`` when the request carries no
    usable constraint (the caller then sends no ``format`` at all and the universal
    parse-with-retry path in core stays in charge — the correct, safe fallback).

    Accepted:

    * a mapping — treated as a JSON Schema and passed through verbatim, because a
      caller that already built a schema knows more about its shape than this
      function does;
    * the ``dict`` / ``list`` TYPES — what ``one_shot_completion(output_type=…)``
      actually passes — mapped to the minimal schema that says "a JSON object" /
      "a JSON array". Minimal is deliberate: it is the strongest claim that is
      TRUE about ``output_type=dict``, and inventing properties would constrain
      the model to a schema the caller never asked for;
    * ``"json"`` — ollama's unschema'd JSON mode.

    Everything else (a Pydantic class, a stray sentinel, ``None``) returns ``None``.
    Rejecting here rather than forwarding matters: an unserializable value put on the
    wire fails inside the JSON encoder, which surfaces as an opaque ``TypeError``
    from the HTTP client rather than "that constraint is not supported".
    """
    if requested is None:
        return None
    if isinstance(requested, dict):
        return requested if requested else None
    if isinstance(requested, str):
        return _JSON_MODE if requested.strip().lower() == _JSON_MODE else None
    if requested is dict:
        return {"type": "object"}
    if requested is list:
        return {"type": "array"}
    logger.debug("Ollama: unsupported structured-output request %r — sending no format", requested)
    return None


def resolve_output_format(options: dict[str, object]) -> object | None:
    """POP the structured-output keys out of ``options`` and return the wire value.

    Popping, not reading, is the point. Every other key in ``extra_options`` is
    ``setdefault``-ed straight onto the request body, so a ``format`` left in there
    would reach the wire UNNORMALIZED — which is how a Python type ends up in the JSON
    encoder and dies as an opaque ``TypeError`` instead of being converted or refused.

    ``format`` (ollama's own spelling) wins over ``output_type`` when both are present:
    an explicit wire-level value is a deliberate override, not a duplicate. Both are
    consumed either way, so a rejected value can't fall through to the passthrough.
    """
    raw = [options.pop(key, None) for key in (_FORMAT_FIELD, _OUTPUT_TYPE_KEY)]
    for value in raw:
        resolved = native_format(value)
        if resolved is not None:
            return resolved
    return None


def _ollama_error_reason(body: str) -> str:
    """Ollama's own sentence for a refused request, or its raw body when it sent no sentence.

    Ollama answers a refusal with ``{"error": "..."}`` — e.g. ``model "x" not found, try pulling
    it first`` — and that sentence is the only place the actual reason exists.
    """
    text = body.strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        reason = parsed.get("error")
        if isinstance(reason, str) and reason.strip():
            text = reason.strip()
    return text[:300] or "it gave no reason"


def _raise_refused(response: Any, body: str) -> None:
    """Raise a refused ``/api/chat`` as ``httpx.HTTPStatusError`` carrying OLLAMA's sentence.

    ``response.raise_for_status()`` says "Client error '404 Not Found' for url
    'http://localhost:11434/api/chat'" plus a link to MDN, and that is what a chat turn and an
    Agent Rooms member's failed turn both showed for a model that is not pulled — measured in a
    live room, where the next member read it as a connectivity problem. Ollama's body, already
    read and logged by the caller, said exactly what was wrong. Same exception type with the
    same response attached, so nothing that classifies or retries on it changes; only the
    sentence a person reads does.
    """
    import httpx

    raise httpx.HTTPStatusError(
        f"Ollama answered {response.status_code}: {_ollama_error_reason(body)}",
        request=response.request,
        response=response,
    )


def _is_tools_unsupported_error(status_code: int, body: str) -> bool:
    """Heuristic: does this 4xx mean the model can't accept a ``tools`` schema?

    Ollama returns 400 with a message mentioning tools/function support for
    models that lack it. We match conservatively so an unrelated 400 (bad
    request, OOM) is NOT mistaken for a tools-capability problem — that would
    silently strip tools from a turn that should have kept them.
    """
    if status_code != 400:
        return False
    lowered = body.lower()
    return "tool" in lowered and (
        "support" in lowered or "does not" in lowered or "not supported" in lowered
    )


def _to_ollama_messages(messages: list[dict]) -> list[dict]:
    """Translate the loop's OpenAI-shaped history into Ollama ``/api/chat`` shape.

    The native loop emits a single canonical (OpenAI) message shape across all
    providers. Ollama's wire format differs in two ways this fixes up:

    * assistant ``tool_calls`` carry ``function.arguments`` as a JSON **object**,
      not the OpenAI JSON **string** the loop records;
    * ``role:"tool"`` results carry a ``tool_name`` (Ollama matches results to
      calls by name), which we recover from the preceding assistant turn's calls.

    Plain user / assistant text messages pass through unchanged.
    """
    id_to_name: dict[str, str] = {}
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls: list[dict] = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                name = fn.get("name", "")
                tc_id = tc.get("id", "") if isinstance(tc, dict) else ""
                if tc_id and name:
                    id_to_name[tc_id] = name
                calls.append(
                    {
                        "function": {
                            "name": name,
                            "arguments": _as_arguments_object(fn.get("arguments")),
                        }
                    }
                )
            entry: dict[str, Any] = {"role": "assistant", "content": m.get("content") or ""}
            entry["tool_calls"] = calls
            out.append(entry)
        elif role == "tool":
            entry = {"role": "tool", "content": str(m.get("content", ""))}
            name = id_to_name.get(m.get("tool_call_id", ""))
            if name:
                entry["tool_name"] = name
            out.append(entry)
        elif isinstance(m.get("content"), list):
            # Multimodal content blocks (text + image_url data-urls), emitted by the
            # knowledge vision nodes. Ollama's /api/chat takes text in `content` and
            # images as a separate `images: [<base64>]` array (raw base64, no data-url
            # prefix) — NOT inline content blocks. Without this the list str()'s into
            # garbage and a vision model sees no image.
            text_parts, images = _split_content_blocks(m["content"])
            entry = {"role": role, "content": "\n".join(text_parts)}
            if images:
                entry["images"] = images
            out.append(entry)
        else:
            out.append(m)
    return out


def _split_content_blocks(blocks: list) -> tuple[list[str], list[str]]:
    """Split OpenAI multimodal content blocks into (text_parts, base64_images) for
    Ollama. image_url data-urls (``data:<mime>;base64,<b64>``) yield the bare base64;
    a non-data URL or unparseable block is skipped."""
    text_parts: list[str] = []
    images: list[str] = []
    for b in blocks:
        if not isinstance(b, dict):
            if b:
                text_parts.append(str(b))
            continue
        if b.get("type") == "text":
            text_parts.append(str(b.get("text", "")))
        elif b.get("type") == "image_url":
            url = ((b.get("image_url") or {}) if isinstance(b.get("image_url"), dict) else {}).get(
                "url", ""
            )
            if isinstance(url, str) and url.startswith("data:") and "," in url:
                images.append(url.split(",", 1)[1])  # bare base64 (Ollama wants no prefix)
    return text_parts, images


def _as_arguments_object(raw: Any) -> dict:
    """Coerce tool-call ``arguments`` (a JSON string, per OpenAI shape) to a dict.

    Ollama expects the object form. Already-dict input passes through; malformed
    JSON degrades to ``{}`` so a single bad call never breaks the whole request.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _accumulate_ollama_tool_call(acc: dict[int, dict[str, Any]], idx: int, tc: dict) -> None:
    """Fold one streamed Ollama ``tool_calls`` entry into the accumulator.

    Ollama may deliver a tool call whole (in the final message) or — like the
    OpenAI delta protocol — across chunks (name first, argument fragments
    after). Object-shaped ``arguments`` replace; string fragments append, so a
    fragmented call reassembles correctly either way.
    """
    bucket = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
    if tc.get("id"):
        bucket["id"] = tc["id"]
    fn = tc.get("function", {}) or {}
    if fn.get("name"):
        bucket["name"] = fn["name"]
    args = fn.get("arguments")
    if isinstance(args, dict):
        bucket["arguments"] = json.dumps(args)
    elif isinstance(args, str) and args:
        if isinstance(bucket["arguments"], str):
            bucket["arguments"] += args
        else:
            bucket["arguments"] = args


class OllamaProvider(ModelProvider):
    """ModelProvider backed by a local Ollama HTTP server.

    The ``httpx`` SDK is imported inside ``__init__`` so the package
    ``personalclaw.providers`` can be imported without pulling the SDK into
    ``sys.modules`` (R6.5 / Property 11).
    """

    # Ollama's native ``/api/chat`` accepts a ``tools`` schema and streams back
    # ``message.tool_calls``, so the native loop can drive a tool-enabled turn
    # via complete(). Tool support is ultimately model-dependent: a model that
    # can't use tools makes Ollama 400 the request, which complete() catches and
    # transparently retries tool-less (graceful degradation), caching the result
    # on ``_tools_unsupported`` so later turns skip the failed first request.
    supports_tools: bool = True

    def __init__(
        self,
        *,
        model: str,
        credential: Credential | None = None,
        endpoint: str = _DEFAULT_ENDPOINT,
        timeout: float = _DEFAULT_TIMEOUT,
        extra_options: dict[str, object] | None = None,
    ) -> None:
        # Lazy import per R6.5 / Property 11. Do NOT lift to module top.
        import httpx  # noqa: WPS433

        # Ollama is typically unauth'd on localhost; ``credential`` is
        # accepted for parity with other providers but is not required.
        # Store it so subclasses / future remote-Ollama deployments could
        # forward a bearer token; today we ignore the secret.
        del credential

        self._httpx_module = httpx
        self._model = model
        self._endpoint = endpoint.rstrip("/")
        self._timeout = timeout
        self._extra_options: dict[str, object] = dict(extra_options or {})
        self._embedding_model = str(self._extra_options.pop("embedding_model", model))
        # Native structured output (§2.4): resolved ONCE here and held apart from
        # ``_extra_options`` so the generic passthrough can never put an unnormalized
        # constraint on the wire. ``None`` means "no constraint" — the request goes out
        # exactly as it does today and core's parse-with-targeted-retry stays in charge.
        self._output_format: object | None = resolve_output_format(self._extra_options)
        # The served window this binding DECLARES (the entry's ``context_window`` option).
        # POPPED like ``embedding_model``: whatever is left in ``_extra_options`` is forwarded
        # into Ollama's ``options`` bag, and ``context_window`` is not a wire parameter — it
        # describes the window, it does not set it. ``None`` = undeclared, resolve it below.
        self.context_window: int | None = declared_context_window(
            self._extra_options.pop("context_window", None)
        )
        self._client: Any = httpx.AsyncClient(base_url=self._endpoint, timeout=timeout)
        self._history: list[dict[str, Any]] = []
        # ``None`` until the first token report, NOT 0.0 — see the llm/base contract and
        # :meth:`_context_pct`. 0.0 is a FABRICATED measurement and it silently disabled
        # compaction for every Ollama session (#2364): the native loop stores this on
        # ``_last_context_pct``, and 0.0 is not ``None``, so the threshold gate read 0.0
        # against 70.0 and returned, while the char-based backstop — which fires only when
        # the gauge is ``None`` — was never reachable. History then grew unbounded until
        # Ollama silently truncated the prompt.
        self._last_context_pct: float | None = None
        # The context gauge for THIS binding. Stateful because truncation detection is
        # ordinal — Ollama truncates silently and reports the truncated count, and the only
        # reference that catches it is the largest prompt this binding already measured.
        self._gauge = ContextGauge()
        # Served window per model id, memoized from ``/api/ps``. Ollama publishes the
        # window it ACTUALLY serves, so this provider never has to guess; the probe is
        # vendor-specific, which is why it lives in this app and not in core.
        self._served_windows: dict[str, int] = {}
        # Flipped True the first time the server rejects a tools request for
        # this model, so subsequent complete() turns skip the doomed first try.
        self._tools_unsupported: bool = False

    @property
    def sampling_temperature(self) -> float | None:
        """The ``options.temperature`` every request from this instance carries, if any."""
        wire = self._extra_options.get(_WIRE_OPTIONS)
        value = wire.get("temperature") if isinstance(wire, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    # ── Local-model management (the uniform download-surface contract) ─────────
    #
    # Ollama is a LOCAL downloadable model provider like faster-whisper/piper — its
    # models are pulled + managed on the user's machine. It satisfies the same contract
    # (list/download/delete + search) so the ONE download card + `/api/models/available`
    # surface it identically; the only difference is ``searchable=True`` (its catalog is
    # the ollama.com library, discovered by a search term, not a fixed list). Management
    # delegates to :class:`OllamaCatalog` (the ModelManager); download drains the
    # streaming ``pull`` into a bool for the shared byte-progress job runner.
    searchable: bool = True

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def display_name(self) -> str:
        return "Ollama"

    def _catalog(self) -> "OllamaCatalog":
        return OllamaCatalog(endpoint=self._endpoint)

    async def is_available(self) -> bool:
        return (await self._catalog().test_connection()).ok

    @staticmethod
    def _to_local(mi: "ModelInfo") -> "LocalModel":
        from personalclaw.sdk.local_model import LocalModel

        return LocalModel(
            name=mi.name,
            size_mb=round((mi.size or 0) / (1024 * 1024), 1),
            description=mi.extra.get("parameter_size", "") or mi.description,
            downloaded=True if mi.downloaded is None else bool(mi.downloaded),
            capabilities=list(mi.capabilities),
            source="ollama.com",
        )

    async def list_models(self) -> list["LocalModel"]:
        """Locally-installed ollama models (downloaded=True — these are pulled)."""
        return [self._to_local(mi) for mi in await self._catalog().list_models()]

    async def search_models(self, query: str) -> list["LocalModel"]:
        """Search the ollama.com library for installable models (the searchable axis)."""
        out = []
        for mi in await self._catalog().search_catalog(query):
            lm = self._to_local(mi)
            lm.downloaded = False  # search results are remote/installable, not local
            out.append(lm)
        return out

    async def download_model(self, model_name: str) -> bool:
        """Pull a model, draining the streaming progress into a success bool for the
        shared download-job runner. Byte-progress is tracked by on-disk polling."""
        try:
            async for frame in self._catalog().pull_model(model_name):
                if frame.error:
                    logger.warning("Ollama pull %s failed: %s", model_name, frame.error)
                    return False
            return True
        except Exception:
            logger.warning("Ollama pull %s raised", model_name, exc_info=True)
            return False

    async def delete_model(self, model_name: str) -> bool:
        try:
            await self._catalog().delete_model(model_name)
            return True
        except Exception:
            logger.warning("Ollama delete %s raised", model_name, exc_info=True)
            return False

    def cache_dir(self) -> str | None:
        # Ollama manages its own model store (~/.ollama/models); byte-progress falls
        # back to indeterminate rather than pointing at a dir core doesn't own.
        return None

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the provider; auto-detect model from Ollama if not specified."""
        if not self._model or self._model == "*":
            try:
                resp = await self._client.get("/api/tags")
                resp.raise_for_status()
                models = resp.json().get("models", [])
                if models:
                    self._model = models[0]["name"]
                    if not self._embedding_model:
                        self._embedding_model = self._model
                    logger.info("Ollama auto-detected model: %s", self._model)
                else:
                    logger.warning("Ollama has no models available at %s", self._endpoint)
            except Exception:
                logger.warning("Failed to auto-detect Ollama model", exc_info=True)
        logger.info("Ollama provider ready: model=%s endpoint=%s", self._model, self._endpoint)

    async def shutdown(self) -> None:
        """Close the underlying HTTP client and clear conversation history."""
        try:
            await self._client.aclose()
        except Exception:  # pragma: no cover — defensive
            logger.warning("Ollama client aclose raised", exc_info=True)
        self._history.clear()

    # ── Streaming ─────────────────────────────────────────────────────

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        """Stream a chat turn; translate NDJSON lines to :class:`LLMEvent`.

        POSTs to ``/api/chat`` with ``stream=true`` and reads the response
        body as newline-delimited JSON. Each non-final chunk's
        ``message.content`` becomes an :data:`EVENT_TEXT_CHUNK`; the final
        ``{"done": true, ...}`` chunk produces an :data:`EVENT_COMPLETE`
        event populated with ``prompt_eval_count`` / ``eval_count``.
        """
        self._history.append({"role": "user", "content": message})
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]

        body: dict[str, Any] = {
            "model": self._model,
            "messages": self._history,
            "stream": True,
        }
        # Allow ``options`` (Ollama temperature / num_ctx / etc.), ``tools``,
        # and other top-level fields to flow through unchanged.
        for key, value in self._extra_options.items():
            body.setdefault(key, value)
        # Native json-schema enforcement, server-side (§2.4). Set last so it is the
        # normalized value that reaches the wire, never a raw passthrough.
        if self._output_format is not None:
            body[_FORMAT_FIELD] = self._output_format

        assistant_text = ""
        # Self-gating inline <think> splitter (see openai.py stream()).
        splitter = make_think_splitter()
        input_tokens = 0
        output_tokens = 0

        async with self._client.stream("POST", "/api/chat", json=body) as response:
            if response.status_code >= 400:
                err_text = (await response.aread()).decode(errors="replace")
                logger.error(
                    "Ollama %d: %s (model=%r, msg_count=%d)",
                    response.status_code,
                    err_text[:200],
                    self._model,
                    len(self._history),
                )
                _raise_refused(response, err_text)
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Ollama stream returned non-JSON line: %r", line[:200])
                    continue

                msg = chunk.get("message") or {}
                text_delta = msg.get("content") or ""
                if text_delta:
                    for seg in splitter.feed(text_delta):
                        if seg.kind == KIND_OUTSIDE:
                            assistant_text += seg.text
                            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=seg.text)
                        else:
                            yield LLMEvent(kind=EVENT_THINKING_CHUNK, text=seg.text)

                if chunk.get("done"):
                    input_tokens = int(chunk.get("prompt_eval_count", 0) or 0)
                    output_tokens = int(chunk.get("eval_count", 0) or 0)
                    break

        for seg in splitter.flush():
            if seg.kind == KIND_OUTSIDE:
                assistant_text += seg.text
                yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=seg.text)
            else:
                yield LLMEvent(kind=EVENT_THINKING_CHUNK, text=seg.text)

        if assistant_text:
            self._history.append({"role": "assistant", "content": assistant_text})

        self._last_context_pct = await self._context_pct(
            self._model, input_tokens, body["messages"]
        )

        yield LLMEvent(
            kind=EVENT_COMPLETE,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            context_usage_pct=self._last_context_pct,
        )

    # ── Stateless completion (native loop) ────────────────────────────

    async def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        reasoning_effort: str = "",  # accepted for interface parity; Ollama has no effort axis
    ) -> AsyncIterator[LLMEvent]:
        """Stream a stateless multi-message chat turn, tools included.

        Unlike :meth:`stream`, this NEVER touches ``self._history`` — the
        native loop owns conversation state and passes the full ``messages``
        list (OpenAI-shaped: user / assistant-with-``tool_calls`` /
        ``role:"tool"`` results). Those are translated to Ollama's ``/api/chat``
        shape by :func:`_to_ollama_messages`.

        When ``tools`` is supplied (and the model hasn't already refused them
        this session), the OpenAI-shaped tool schema is forwarded; Ollama
        streams any tool intent back as ``message.tool_calls`` (arguments as a
        JSON object), which we emit as :data:`EVENT_TOOL_CALL`. A model that
        can't use tools makes the server 400 the request — we catch that once,
        cache it on ``_tools_unsupported``, and retry tool-less so the turn
        still completes.
        """
        send_tools = bool(tools) and not self._tools_unsupported
        ollama_messages = _to_ollama_messages(messages)

        body: dict[str, Any] = {
            "model": model or self._model,
            "messages": ollama_messages,
            "stream": True,
        }
        if send_tools:
            body["tools"] = tools
        # Allow ``options`` (temperature / num_ctx / etc.) to flow through.
        for key, value in self._extra_options.items():
            body.setdefault(key, value)
        # Native json-schema enforcement, server-side (§2.4) — see stream().
        if self._output_format is not None:
            body[_FORMAT_FIELD] = self._output_format

        input_tokens = 0
        output_tokens = 0
        # Accumulate tool calls by index — Ollama may stream them across chunks
        # (name first, then argument fragments) like the OpenAI delta protocol,
        # or deliver a complete list in the final message. Either way we emit
        # one EVENT_TOOL_CALL per call after the stream ends.
        tool_calls: dict[int, dict[str, Any]] = {}
        # Self-gating inline <think> splitter (see openai.py stream()).
        splitter = make_think_splitter()

        async with self._client.stream("POST", "/api/chat", json=body) as response:
            if response.status_code >= 400:
                err_body = await response.aread()
                err_text = err_body.decode(errors="replace")
                # A model that can't use tools rejects the tools request. Retry
                # once without tools so the turn still completes, and remember
                # it so later turns don't pay the failed round-trip again.
                if send_tools and _is_tools_unsupported_error(response.status_code, err_text):
                    logger.info(
                        "Ollama model %r does not support tools; retrying tool-less",
                        model or self._model,
                    )
                    self._tools_unsupported = True
                    async for ev in self.complete(messages, tools=None, model=model):
                        yield ev
                    return
                logger.error(
                    "Ollama %d: %s (model=%r, msg_count=%d)",
                    response.status_code,
                    err_text[:200],
                    model or self._model,
                    len(messages),
                )
                _raise_refused(response, err_text)
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Ollama stream returned non-JSON line: %r", line[:200])
                    continue

                msg = chunk.get("message") or {}
                text_delta = msg.get("content") or ""
                if text_delta:
                    for seg in splitter.feed(text_delta):
                        yield LLMEvent(
                            kind=(
                                EVENT_TEXT_CHUNK
                                if seg.kind == KIND_OUTSIDE
                                else EVENT_THINKING_CHUNK
                            ),
                            text=seg.text,
                        )

                for idx, tc in enumerate(msg.get("tool_calls") or []):
                    _accumulate_ollama_tool_call(tool_calls, idx, tc)

                if chunk.get("done"):
                    input_tokens = int(chunk.get("prompt_eval_count", 0) or 0)
                    output_tokens = int(chunk.get("eval_count", 0) or 0)
                    break

        for seg in splitter.flush():
            yield LLMEvent(
                kind=EVENT_TEXT_CHUNK if seg.kind == KIND_OUTSIDE else EVENT_THINKING_CHUNK,
                text=seg.text,
            )

        for idx in sorted(tool_calls):
            bucket = tool_calls[idx]
            name = bucket.get("name") or ""
            if not name:
                continue
            yield LLMEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=bucket.get("id") or f"call-{idx}",
                title=name,
                tool_input=bucket.get("arguments", ""),
            )

        self._last_context_pct = await self._context_pct(
            model or self._model, input_tokens, body["messages"]
        )

        yield LLMEvent(
            kind=EVENT_COMPLETE,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            context_usage_pct=self._last_context_pct,
            cost_usd=0.0,
        )

    # ── Embeddings ────────────────────────────────────────────────────

    async def embed(self, inputs: list[str]) -> list[list[float]]:
        """Return embedding vectors for ``inputs`` via ``POST /api/embed``.

        The model defaults to the chat model and can be overridden via
        the ``embedding_model`` key in ``extra_options``.
        """
        if not inputs:
            return []
        response = await self._client.post(
            "/api/embed",
            json={"model": self._embedding_model, "input": inputs},
        )
        response.raise_for_status()
        payload = response.json()
        embeddings = payload.get("embeddings") or []
        return [list(vec) for vec in embeddings]

    # ── Tool approval (no-op) ─────────────────────────────────────────

    async def approve_tool(self, request_id: str | int) -> None:
        """No-op: Ollama tool calls are not interactive at this layer."""
        return None

    async def reject_tool(self, request_id: str | int) -> None:
        """No-op: Ollama tool calls are not interactive at this layer."""
        return None

    # ── Context accounting ────────────────────────────────────────────

    async def _served_window(self, model: str) -> int | None:
        """Tokens this Ollama runtime actually SERVES for ``model``, or ``None``.

        Resolution order, most-authoritative first:

        1. the binding's declared ``context_window`` — an operator who set ``num_ctx``
           knows the served window better than any probe or default can;
        2. ``GET /api/ps`` → ``context_length``, which is the window Ollama loaded the
           model WITH. This is the number nothing else in the stack has: the shared
           ``model_tokens.json`` table holds ARCHITECTURAL maxima (``gemma4``'s is
           262144 while 0.34.2 serves 32768 by default), and ``/api/show`` returns that
           same architectural figure. Measured on 0.34.2: ``/api/ps`` reports 32768;
           dividing by the architectural 262144 — or by an adapter default of 128000 —
           understates usage by 4-8x, which is what made the compaction gate unreachable;
        3. ``None`` — nothing told us, so the gauge reports nothing.

        🪤 (3) used to be ``model_context_window(model, local=True)``, i.e. the 4096-token
        conservative FLOOR, and that was the same defect as an adapter default in the
        other direction: the floor exists for the char ESTIMATE, and dividing a real
        26682-token report by it displays 651%. ``model_windows.model_context_window``
        says so in its own trap note — "a displayed measurement may not err at all" — so
        a failed probe now yields an unmeasured gauge, and the floor keeps its one honest
        job on the estimate path in ``runtime._estimated_context_pct`` (which is reachable
        precisely because this returns ``None``).

        The probe is memoized per model id: the served window cannot change without a
        reload, and this runs on the completion path of every turn. It is also strictly
        best-effort — a probe failure falls through to (3) rather than failing the turn,
        because an unavailable ``/api/ps`` must not be able to break inference.

        🪤 ``/api/ps`` lists only LOADED models and returns ``{"models": []}`` until one is
        warm, which is why this is called after the turn's token report rather than at
        construction: by then the model that just answered is necessarily loaded.
        """
        if self.context_window is not None:
            return self.context_window
        cached = self._served_windows.get(model)
        if cached is not None:
            return cached
        try:
            resp = await self._client.get("/api/ps")
            resp.raise_for_status()
            for entry in resp.json().get("models") or []:
                name = str(entry.get("name") or entry.get("model") or "")
                served = int(entry.get("context_length") or 0)
                if served > 0 and name in (model, f"{model}:latest"):
                    self._served_windows[model] = served
                    logger.debug("ollama: %s serves a %d-token window (/api/ps)", model, served)
                    return served
        except Exception as exc:  # noqa: BLE001 - best-effort probe, never fatal
            logger.debug("ollama: /api/ps served-window probe failed (%s)", exc)
        return None

    async def served_context_window(self) -> int | None:
        """The window this runtime serves the bound model with — the probe the gauge divides by.

        This is what core's window resolver asks before a turn is assembled. Before it existed,
        ``/api/ps`` fed only the gauge: with Ollama serving 32,768 the prompt builder budgeted
        for its conservative 4,096 floor, dropped the widget instructions and told the user, on
        every turn, that the model had a 4,096-token window.
        """
        return await self._served_window(self._model)

    async def _context_pct(
        self, model: str, input_tokens: int, messages: list[dict]
    ) -> float | None:
        """Input tokens as a percentage of the SERVED window, or ``None`` if unknown.

        ``None`` rather than 0.0 when there is nothing to report: the composer renders a
        plain dot for an absent measurement and a confident ring for a present one, and
        the native loop's char-based compaction backstop is reachable only while the
        gauge is ``None``. Reporting 0.0 therefore both fabricates a reading and disables
        the fallback that exists to cover the absence.

        ``messages`` is the request body we just sent, and it is what makes the reading
        monotone. Ollama's ``prompt_eval_count`` is what it ACCEPTED: past the served
        window it truncates silently, returns HTTP 200, and reports half the window
        forever, so this gauge used to peak at 98.39% and then read a flat 50.01% for
        every larger prompt (#3405). :func:`context_gauge_pct` calls that what it is —
        a full context — instead of dividing the truncated figure.
        """
        return self._gauge.measure(
            reported_tokens=input_tokens,
            sent_chars=prompt_text_chars(messages),
            window=await self._served_window(model),
        )

    # ── Status ────────────────────────────────────────────────────────

    def context_usage_pct(self) -> float | None:
        return self._last_context_pct

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> CancelOutcome:
        """Cancel is a no-op for now; later phases can wire abort plumbing."""
        return "no_turn"


# ── Capability descriptor ────────────────────────────────────────────────
#
# Per design § A.5, Ollama supports streaming and embeddings fully. Tools are
# advertised here (Ollama's /api/chat forwards a tools schema and streams back
# tool_calls) but remain model-dependent: complete() degrades to a tool-less
# turn when a given model rejects the tools request. Vision stays partial, so
# the descriptor leaves it off; deployments pin a known-capable model and
# declare extra capabilities on the entry.
OLLAMA_CAPABILITY = ProviderCapability(
    type="ollama",
    capabilities=frozenset(
        {
            Capability.CHAT,
            Capability.CODE_TOOLS,
            Capability.SUMMARIZATION,
            Capability.STREAMING,
            Capability.EMBEDDING,
            Capability.VISION,
        }
    ),
    supports_streaming=True,
    supports_tools=True,
    supports_embeddings=True,
    # Vision is model-dependent (moondream/llava/qwen-vl yes, llama3/mistral no). The
    # TYPE advertises VISION so a vision model bound in Settings→Models resolves + can
    # receive images (_to_ollama_messages emits the `images` array); picking a text-only
    # model for a vision role is a user choice the model itself will no-op on.
    supports_vision=True,
    max_context_tokens=0,  # model-dependent
    # NATIVE json-schema enforcement (§2.4). Ollama's /api/chat takes a top-level
    # ``format`` schema and constrains the sampler server-side, so this is the top
    # grade — not JSON_MODE, which would understate it, and not the NONE default,
    # which is what left core's graded descriptor with no provider to dispatch on.
    # Model-independent: the constraint is applied by the ollama runtime, not by the
    # model's own instruction-following, so it holds on a 1B model too.
    structured_output=StructuredOutput.JSON_SCHEMA,
    # NONE - Ollama exposes no per-request cache marker, and any prefix reuse is a
    # property of the local runtime's KV cache rather than a documented, app-verifiable
    # server-side prompt cache the user can rely on. Nothing substantiates AUTOMATIC.
    prompt_cache=PromptCache.NONE,
    notes=(
        "Local Ollama server via httpx; tool use degrades per model, vision "
        "model-dependent; native json-schema output via the `format` field."
    ),
)


# ── Factory ──────────────────────────────────────────────────────────────


def _factory(
    *,
    entry: ProviderEntry,
    session_key: str | None = None,
    **kwargs: object,
) -> ModelProvider:
    """Construct an :class:`OllamaProvider` from a :class:`ProviderEntry`.

    ``session_key`` is accepted for parity with the registry contract but
    Ollama is stateless — we ignore it.

    Ollama typically runs unauth'd on localhost, so a credential is
    optional. If the entry declares one, it is resolved through the
    optional ``credential_store`` keyword forwarded by
    :meth:`ProviderRegistry.build` and stashed on the provider for
    forward compatibility (e.g. a remote Ollama behind a reverse proxy).
    """
    del session_key  # unused — Ollama provider is stateless.

    cred: Credential | None = None
    if entry.credential:
        store = kwargs.get("credential_store")
        if store is not None:
            # ``CredentialStore`` is duck-typed here to keep the registry
            # from depending on a concrete class.
            cred = store.resolve(entry.credential)  # type: ignore[attr-defined]

    options = dict(entry.options or {})
    endpoint_value = options.pop("endpoint", None)
    endpoint = str(endpoint_value) if endpoint_value is not None else _DEFAULT_ENDPOINT
    # The DECLARED option name is ``timeout_secs`` (app.json's schema and the README's
    # option table), and that is what Settings writes into ``entry.options``. This path
    # read only ``timeout`` — a name the app documents nowhere and the UI never writes —
    # so the app shipped a knob with no reader on its main path: a chat turn always used
    # the 60s default no matter what the user set. Measured before the fix: a plan-mode
    # turn died on httpx.ReadTimeout after exactly 61s with timeout_secs="1800".
    # ``timeout`` stays accepted as an undocumented programmatic alias, and wins when
    # both are present because it is the more specific, per-call spelling.
    _explicit = options.pop("timeout", None)
    _declared = options.pop("timeout_secs", None)
    timeout = _timeout_or_default(_explicit if _explicit is not None else _declared)

    # A ``model`` kwarg (threaded by ``registry.build(name, model=…)``) overrides the
    # entry's pinned model — a per-use-case caller (e.g. one_shot_completion's
    # reasoning axis) must be able to pin the active model, or it would silently use
    # the entry default.
    _model_override = kwargs.get("model")
    model = str(_model_override) if _model_override else entry.model

    # The embedding use-case binding arrives as a build kwarg — the embedder
    # constructs its provider WITH the bound model (embed() takes no per-call model).
    _emb_model = kwargs.get("embedding_model")
    if _emb_model:
        options["embedding_model"] = str(_emb_model)

    # A requested output CONTRACT arrives as a build kwarg, on the same channel
    # ``model``/``embedding_model`` ride: the platform decides the contract at the CALL
    # (``one_shot_completion(output_type=…)``) while the provider is constructed per
    # call, so there is nowhere else for it to enter. Forwarded into ``options`` where
    # the constructor normalizes it into ollama's native ``format`` field. A build kwarg
    # WINS over the entry's own option — a per-call contract is more specific than a
    # standing default the user pinned on the provider entry — so the entry's keys are
    # cleared first rather than left to race the precedence rule inside the normalizer.
    _requested = {k: kwargs[k] for k in (_FORMAT_FIELD, _OUTPUT_TYPE_KEY) if kwargs.get(k)}
    if _requested:
        options.pop(_FORMAT_FIELD, None)
        options.pop(_OUTPUT_TYPE_KEY, None)
        options.update(_requested)

    # A per-call sampling TEMPERATURE arrives the same way (HARNESS-CRAFT §2.1: best-of-N needs
    # N genuinely different samples), and on ollama's wire it is not a top-level field: it lives
    # in the request's `options` object. This factory used to read neither — the kwarg was
    # dropped, and a proxy in front of a live ollama saw every best-of-n candidate request carry
    # only `model`, `messages` and `stream`, so N paid calls sampled ONE answer N times. The
    # per-call value wins over an entry-level one: the caller asking for THIS temperature is
    # more specific than the instance default (the SDK's branded factory makes the same call).
    _temperature = kwargs.get("temperature")
    if isinstance(_temperature, (int, float)) and not isinstance(_temperature, bool):
        _wire = options.get(_WIRE_OPTIONS)
        options[_WIRE_OPTIONS] = {
            **(_wire if isinstance(_wire, dict) else {}),
            "temperature": float(_temperature),
        }
    # Routing and label fields are not request parameters: everything left in `options` is
    # `setdefault`-ed onto the request body, so `default_model` (which the "Add instance" form
    # writes) reached the wire as a top-level key on every call.
    for _label in ("model", "default_model", "type", "name"):
        options.pop(_label, None)

    logger.debug("Ollama factory: model=%r endpoint=%r", model, endpoint)
    return OllamaProvider(
        model=model,
        credential=cred,
        endpoint=endpoint,
        timeout=timeout,
        extra_options=options,
    )


def create_provider(config: dict | None = None) -> "OllamaProvider":
    """Extension factory: build an OllamaProvider from a settings dict.

    Used by the bundled ``ollama-models`` model extension. Chat and embedding
    both run through this single provider — embedding uses the ``embedding_model``
    option, chat uses ``default_model``.
    """
    cfg = dict(config or {})
    endpoint = str(cfg.get("endpoint") or _DEFAULT_ENDPOINT)
    timeout = _timeout_or_default(cfg.get("timeout_secs"))
    extra: dict[str, object] = {}
    if cfg.get("embedding_model"):
        extra["embedding_model"] = cfg["embedding_model"]
    # The served-window declaration has to be forwarded here too, not only on the
    # registry factory path (which passes ``entry.options`` through wholesale). This
    # factory hand-picks keys, so a schema field it does not name is silently dropped —
    # the option would render in the form, save to config.json, and never reach the
    # provider that reads it.
    if cfg.get("context_window") is not None:
        extra["context_window"] = cfg["context_window"]
    return OllamaProvider(
        model=str(cfg.get("default_model") or ""),
        endpoint=endpoint,
        timeout=timeout,
        extra_options=extra,
    )


# ── Catalog / management (the reference ModelManager implementer) ──────────
#
# Ollama owns local model lifecycle (list/pull/delete/show + remote-catalog
# search), so it implements the full ModelManager axis. THIS APP registers it via
# the same register_catalog seam every other model app uses — the app is bundled
# (it ships in the wheel and auto-installs at first boot) but it is not eager-imported
# and it is not architecturally special: the app loader imports this module when the
# app is enabled, exactly as it does for a Store install. The HTTP handlers call these
# methods generically; no handler names "ollama".

_CATALOG_TIMEOUT = 10  # seconds — list/connectivity probes
_SHOW_TIMEOUT = 15
_DELETE_TIMEOUT = 30
_PULL_TIMEOUT = 1800  # a large model download can take a long time
_SEARCH_UA = "Mozilla/5.0 (compatible; PersonalClaw/1.0)"


class OllamaCatalog(ModelManager):
    """Discovery + full local model management for an Ollama endpoint.

    Pure function of the entry's ``endpoint`` option — never opens a chat
    session. All network calls are fail-soft for the read paths (list/search
    return ``[]`` on error); the write paths (pull/delete/show) surface errors so
    the UI can report them.
    """

    def __init__(self, endpoint: str = _DEFAULT_ENDPOINT) -> None:
        self._endpoint = (endpoint or _DEFAULT_ENDPOINT).rstrip("/")

    # ── Discovery ──────────────────────────────────────────────────────
    async def list_models(self) -> list[ModelInfo]:
        """List locally-installed models via ``GET /api/tags``."""
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._endpoint}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=_CATALOG_TIMEOUT),
                ) as r:
                    if r.status != 200:
                        return []
                    data = await r.json()
        except Exception:  # noqa: BLE001 — discovery is fail-soft
            return []

        out: list[ModelInfo] = []
        for m in data.get("models", []):
            name = m.get("name", "")
            if not name:
                continue
            details = m.get("details", {}) if isinstance(m.get("details"), dict) else {}
            families = details.get("families") or []
            size = m.get("size", 0)
            out.append(
                ModelInfo(
                    id=name,
                    name=name,
                    capabilities=infer_capabilities(name, families),
                    size=size or None,
                    extra={
                        k: v
                        for k, v in {
                            "size_human": _humanize_bytes(size),
                            "modified_at": m.get("modified_at", ""),
                            "parameter_size": details.get("parameter_size", ""),
                            "quantization": details.get("quantization_level", ""),
                            "family": details.get("family", ""),
                        }.items()
                        if v
                    },
                )
            )
        return out

    async def test_connection(self) -> ConnectionResult:
        """Probe reachability via ``/api/tags`` (a cheap local call)."""
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._endpoint}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=_CATALOG_TIMEOUT),
                ) as r:
                    if r.status != 200:
                        return ConnectionResult(ok=False, detail=f"Ollama returned {r.status}")
                    data = await r.json()
        except Exception as exc:  # noqa: BLE001
            return ConnectionResult(ok=False, detail=str(exc)[:200])
        return ConnectionResult(ok=True, model_count=len(data.get("models", [])))

    # ── Management ─────────────────────────────────────────────────────
    async def search_catalog(self, query: str) -> list[ModelInfo]:
        """Search the ollama.com library for installable models (+ their tags).

        Scrapes the public search + per-model tags pages (Ollama exposes no
        JSON search API). Returns up to 20 ``name:tag`` candidates. Fail-soft.
        """
        import asyncio as _asyncio
        import re as _re

        import aiohttp

        q = (query or "").strip()
        if not q:
            return []
        # ollama.com/search indexes by model BASE NAME (``qwen2.5``), not ``name:tag``.
        # A user naturally types the full tag they want (``qwen2.5:0.5b``) → strip the
        # ``:tag`` so the search matches; the exact tag still surfaces via the per-model
        # tags enumeration below. Without this, a full-tag query returns zero results.
        search_q = q.split(":", 1)[0]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://ollama.com/search",
                    params={"q": search_q},
                    timeout=aiohttp.ClientTimeout(total=_CATALOG_TIMEOUT),
                    headers={"User-Agent": _SEARCH_UA},
                ) as r:
                    if r.status != 200:
                        return []
                    html = await r.text()
        except Exception:  # noqa: BLE001
            return []

        seen: set[str] = set()
        unique_models: list[str] = []
        for model_name in _re.findall(r'href="/library/([^"]+)"', html):
            if model_name in seen:
                continue
            seen.add(model_name)
            unique_models.append(model_name)
            if len(unique_models) >= 8:
                break

        async def _fetch_tags(sess: Any, base_name: str) -> list[str]:
            try:
                async with sess.get(
                    f"https://ollama.com/library/{base_name}/tags",
                    timeout=aiohttp.ClientTimeout(total=5),
                    headers={"User-Agent": _SEARCH_UA},
                ) as tr:
                    if tr.status != 200:
                        return ["latest"]
                    tag_html = await tr.text()
                    tags = _re.findall(
                        rf'href="/library/{_re.escape(base_name)}:([^"]+)"', tag_html
                    )
                    seen_tags: set[str] = set()
                    unique_tags: list[str] = []
                    for t in tags:
                        if t not in seen_tags:
                            seen_tags.add(t)
                            unique_tags.append(t)
                    return unique_tags[:5] if unique_tags else ["latest"]
            except Exception:  # noqa: BLE001
                return ["latest"]

        async with aiohttp.ClientSession() as sess:
            tag_results = await _asyncio.gather(*[_fetch_tags(sess, m) for m in unique_models])

        out: list[ModelInfo] = []
        for base_name, tags in zip(unique_models, tag_results):
            for tag in tags:
                full = f"{base_name}:{tag}"
                out.append(ModelInfo(id=full, name=full, capabilities=infer_capabilities(full)))
                if len(out) >= 20:
                    return out
        return out

    async def pull_model(self, model_id: str) -> AsyncIterator[PullProgress]:
        """Download a model via ``POST /api/pull`` (stream=true), yielding
        progress frames. Raises up front for a bad request; a mid-stream failure
        yields a terminal :class:`PullProgress` with ``error`` set.

        The generator closes its upstream connection when the consumer stops
        iterating (GeneratorExit) — that is what cancels the Ollama-side download.
        """
        import aiohttp

        model = (model_id or "").strip()
        if not model:
            raise ValueError("model is required")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._endpoint}/api/pull",
                json={"name": model, "stream": True},
                timeout=aiohttp.ClientTimeout(total=_PULL_TIMEOUT),
            ) as r:
                if r.status != 200:
                    detail = (await r.text())[:200]
                    yield PullProgress(
                        status="", error=f"Ollama pull returned {r.status}: {detail}"
                    )
                    return
                async for line in r.content:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        frame = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if frame.get("error"):
                        yield PullProgress(status="", error=str(frame["error"])[:200])
                        return
                    yield PullProgress(
                        status=str(frame.get("status", "")),
                        completed=frame.get("completed"),
                        total=frame.get("total"),
                        digest=str(frame.get("digest", "") or ""),
                    )

    async def delete_model(self, model_id: str) -> None:
        """Delete a local model via ``DELETE /api/delete``. Raises on failure."""
        import aiohttp

        model = (model_id or "").strip()
        if not model:
            raise ValueError("model is required")
        async with aiohttp.ClientSession() as session:
            async with session.delete(
                f"{self._endpoint}/api/delete",
                json={"name": model},
                timeout=aiohttp.ClientTimeout(total=_DELETE_TIMEOUT),
            ) as r:
                if r.status != 200:
                    text = (await r.text())[:200]
                    raise RuntimeError(f"Ollama returned {r.status}: {text}")

    async def show_model(self, model_id: str) -> ModelInfo:
        """Return rich metadata via ``POST /api/show``. Raises on failure.

        Maps Ollama's ``details`` + ``model_info`` into a :class:`ModelInfo` whose
        ``extra`` carries the decision-relevant fields the UI renders
        (family / parameter_size / quantization / format / context_length /
        capabilities / license_short)."""
        import aiohttp

        model = (model_id or "").strip()
        if not model:
            raise ValueError("model is required")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._endpoint}/api/show",
                json={"name": model},
                timeout=aiohttp.ClientTimeout(total=_SHOW_TIMEOUT),
            ) as r:
                if r.status != 200:
                    raise RuntimeError(f"Ollama returned {r.status}")
                data = await r.json()

        details = data.get("details", {}) if isinstance(data, dict) else {}
        model_info = data.get("model_info", {}) if isinstance(data, dict) else {}
        ctx = next((v for k, v in model_info.items() if k.endswith(".context_length")), None)
        license_raw = str(data.get("license", "") or "")
        # ``capabilities`` here is Ollama's OWN self-reported list (completion/tools/
        # vision/…) surfaced verbatim for the detail view — NOT the inferred
        # chat/embedding tags. Carry it in extra so the /show response keeps its
        # historical shape; ModelInfo.capabilities stays the inferred tag set.
        extra = {
            k: v
            for k, v in {
                "family": details.get("family", ""),
                "parameter_size": details.get("parameter_size", ""),
                "quantization": details.get("quantization_level", ""),
                "format": details.get("format", ""),
                "context_length": ctx or 0,
                "capabilities": data.get("capabilities", []) if isinstance(data, dict) else [],
                "license_short": license_raw.splitlines()[0][:80] if license_raw else "",
            }.items()
            if v
        }
        return ModelInfo(id=model, name=model, capabilities=[], extra=extra)


def _humanize_bytes(n: int) -> str:
    """Bytes → a compact human size (e.g. ``4.7 GB``). 0/negative → ``""``."""
    if not n or n < 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def create_catalog(options: dict | None = None, *, model: str = "") -> OllamaCatalog:
    """Catalog factory (registry contract): build an OllamaCatalog from an entry's
    options bag. Only ``endpoint`` is relevant for discovery/management."""
    del model  # unused — model lifecycle is endpoint-scoped, not per pinned model
    endpoint = str((options or {}).get("endpoint") or _DEFAULT_ENDPOINT)
    return OllamaCatalog(endpoint=endpoint)


# ── Registration ─────────────────────────────────────────────────────────
#
# Register on import — the app loader imports this module when the app is
# enabled, wiring the type into the default registry. The ``try``/``except``
# makes the registration idempotent against module reload in tests; the
# registry itself remains strict and rejects duplicate types in normal use.
try:
    get_default_registry().register_type(OLLAMA_CAPABILITY, _factory)
except ProviderResolutionError:
    logger.debug("ollama provider type already registered with default registry")

# The catalog axis (register_catalog is idempotent — last wins — so no guard).
get_default_registry().register_catalog("ollama", create_catalog)

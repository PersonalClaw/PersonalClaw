"""SDK helpers for building a model-provider APP with minimal boilerplate.

A model provider that speaks one of the two supported inference PROTOCOLS
(OpenAI-compatible or Anthropic-compatible) over a fixed endpoint + bearer key —
i.e. every branded provider app (Together, Groq, DeepSeek, Mistral, Gemini's
OpenAI shim, …) and the two generic "bring-your-own-endpoint" apps — differs from
its siblings ONLY in: its default base URL, its API-key env var, and its fallback
model catalog. Everything else (the registry ``_factory``, the ``create_provider``
config-path factory, credential resolution, and the ``ModelCatalog``) is identical.

These helpers capture that identical wiring so each app's ``provider.py`` is a few
declarations + one ``register_*_app(...)`` call. The helpers build on the SDK
primitives (``OpenAIProvider`` / ``AnthropicProvider`` / ``ModelCatalog`` /
``openai_compatible_list_models``) — this is generic protocol infra, NOT one app
importing another.

An app that needs provider-specific behavior beyond endpoint+key+catalog (e.g.
Azure OpenAI's distinct ``AsyncAzureOpenAI`` client + ``api_version`` + api-key
header) does NOT use these — it subclasses the protocol client directly.
"""

from __future__ import annotations

from typing import Any, Callable

# ── Re-exported from CORE ────────────────────────────────────────────────────
# Every name below comes from ``personalclaw.llm.*`` DIRECTLY — never from the sibling SDK
# facade ``personalclaw.sdk.model``. That facade re-exports this module's
# ``register_branded_app``, so importing it back from here is a module-scope CYCLE: it made
# ``import personalclaw.sdk.provider_helpers`` from a cold interpreter fail outright, and
# only ``import personalclaw.sdk.model`` first happened to work. The two SDK modules are
# both thin re-export surfaces over the same core layer, so there is nothing to gain by
# routing one through the other — one direction only (``sdk.model`` → here), pinned by
# tests/test_sdk_import_cycle.py.
#
# The spec dataclass, the registry it lands in, and the credential ladder live in
# ``personalclaw.llm.branded_specs`` — below this boundary, because they are core state and
# core policy that four core modules read. They are re-exported here so an app's import path
# is unchanged; only the direction of the dependency moved. See that module's docstring.
from personalclaw.llm import branded_specs  # noqa: E402
from personalclaw.llm.anthropic import AnthropicProvider  # noqa: F401
from personalclaw.llm.base import ModelProvider  # noqa: F401
from personalclaw.llm.branded_specs import (  # noqa: E402,F401
    BrandedProviderSpec,
    resolve_credential,
    resolve_spec_secret,
)
from personalclaw.llm.capabilities import Capability, ProviderCapability  # noqa: F401
from personalclaw.llm.catalog import (  # noqa: F401
    ConnectionResult,
    ModelCatalog,
    ModelInfo,
    infer_capabilities,
    openai_compatible_list_models,
)
from personalclaw.llm.credentials import Credential  # noqa: F401
from personalclaw.llm.openai import OpenAIProvider  # noqa: F401
from personalclaw.llm.prompt_cache import PromptCache  # noqa: F401
from personalclaw.llm.registry import (  # noqa: F401
    CredentialMissing,
    ProviderEntry,
    ProviderResolutionError,
    get_default_registry,
)
from personalclaw.llm.subscription_credentials import (  # noqa: F401
    SubscriptionSource,
    register_subscription_source,
    resolve_subscription_credential,
)


def _build_provider(
    spec: BrandedProviderSpec,
    *,
    model: str,
    credential: Credential,
    base_url: str,
    extra_options: dict[str, object] | None = None,
) -> ModelProvider:
    """Construct the protocol client for ``spec`` with a resolved credential + base_url."""
    if spec.protocol == "anthropic":
        return AnthropicProvider(
            model=model,
            credential=credential,
            base_url=base_url or None,
            max_tokens=spec.max_tokens if spec.max_tokens is not None else 4096,
            extra_options=extra_options,
        )
    return OpenAIProvider(
        model=model,
        credential=credential,
        base_url=base_url or None,
        max_tokens=spec.max_tokens,
        extra_options=extra_options,
    )


class BrandedCatalog(ModelCatalog):
    """Discovery for a branded OpenAI-compatible provider: try the live
    ``/v1/models`` endpoint, fall back to the spec's curated list so the picker is
    never empty when the key is set but the endpoint has no models route (some
    providers don't expose one). Anthropic-compatible providers have no models
    endpoint, so they always use the fallback list."""

    def __init__(
        self,
        spec: BrandedProviderSpec,
        *,
        endpoint: str = "",
        api_key: str = "",
        default_model: str = "",
    ) -> None:
        self._spec = spec
        self._endpoint = endpoint or spec.default_base_url
        # Only the EXPLICIT key is stored; the env / subscription hops are resolved per call
        # by `_resolved_key` (a catalog instance outlives a `claude login`, so freezing the
        # answer here would keep reporting "not signed in" after the user signed in).
        self._explicit_api_key = api_key
        self._default_model = default_model

    def _resolved_key(self) -> tuple[str, str]:
        """This catalog's effective key, plus the honest reason when there isn't one.

        Same shared order as both factories (:func:`resolve_spec_secret`), so a subscription
        app the user is signed into gets probed with its CLI's token instead of being told to
        set an API-key env var it deliberately doesn't have. Returns ``("", reason)`` when no
        secret resolves; ``reason`` is the resolver's secret-free sentence, or ``""`` for an
        ordinary key-based app that simply has no key set.
        """
        cred, reason = resolve_spec_secret(self._spec, explicit_key=self._explicit_api_key)
        return (str(cred.secret or "") if cred is not None else "", reason)

    def _no_key_detail(self, reason: str) -> str:
        """The 'no credential' line, never a dangling parenthetical.

        A subscription app declares NO ``api_key_env`` by design, so the old unconditional
        ``f"... (set it or {api_key_env})"`` rendered literally as ``(set it or )`` and told a
        signed-out user to set a variable that does not exist. Prefer the resolver's typed
        reason, mention an env var only when the app actually declares one.
        """
        env = self._spec.api_key_env
        if reason:
            return f"{reason} (or set {env})" if env else reason
        return f"No API key configured (set it or {env})" if env else "No API key configured"

    def _fallback(self) -> list[ModelInfo]:
        rows = [
            ModelInfo(
                id=m["id"],
                name=m.get("name", m["id"]),
                capabilities=list(m.get("capabilities", infer_capabilities(m["id"]))),
            )
            for m in self._spec.fallback_models
        ]
        # A no-discovery provider (e.g. an Anthropic-compatible endpoint) has no
        # models-list route AND an empty static fallback — so the ONLY selectable
        # model is the one the user configured on the instance. Surface it, else the
        # picker is empty and the configured provider can't be bound at all.
        if self._default_model and not any(r.id == self._default_model for r in rows):
            rows.insert(
                0,
                ModelInfo(
                    id=self._default_model,
                    name=self._default_model,
                    capabilities=list(self._spec.capabilities)
                    or infer_capabilities(self._default_model),
                ),
            )
        return rows

    async def list_models(self) -> list[ModelInfo]:
        if self._spec.protocol == "anthropic":
            return self._fallback()  # no models endpoint on the Anthropic wire
        api_key, _ = self._resolved_key()
        live = await openai_compatible_list_models(
            self._endpoint,
            api_key,
            default_base=self._spec.default_base_url,
        )
        return live if live else self._fallback()

    async def test_connection(self) -> ConnectionResult:
        api_key, missing_reason = self._resolved_key()
        if not api_key:
            return ConnectionResult(ok=False, detail=self._no_key_detail(missing_reason))
        # Anthropic-wire providers expose NO models-list endpoint, so a models
        # count can't prove connectivity (a bring-your-own-endpoint app has an empty
        # fallback list → the old code wrongly reported "No models available" for a
        # perfectly good key). Probe the REAL path instead: a 1-token completion.
        # An auth failure (401/403) is a genuine failure; any model-level response
        # (success, or even a model-not-found/validation error) proves the key +
        # endpoint authenticated.
        if self._spec.protocol == "anthropic":
            return await self._probe_completion(api_key)
        models = await self.list_models()
        if not models:
            return ConnectionResult(ok=False, detail="No models available (check key/endpoint)")
        return ConnectionResult(ok=True, model_count=len(models))

    async def _probe_completion(self, api_key: str) -> ConnectionResult:
        """Verify an Anthropic-wire key/endpoint with a minimal completion. Auth
        errors → not connected; a model/validation error still means the credentials
        authenticated → connected.

        Takes the already-resolved secret (a subscription token as readily as a pasted key)
        so the probe validates exactly what :meth:`test_connection` found."""
        model = self._spec.default_model or "claude-3-5-haiku-latest"
        try:
            from personalclaw.llm.anthropic import AnthropicProvider
            from personalclaw.llm.credentials import Credential

            prov = AnthropicProvider(
                model=model,
                credential=Credential(
                    name=self._spec.type, kind="api_key", secret=api_key, source="file"
                ),
                base_url=self._endpoint or None,
                max_tokens=1,
            )
            async for _ in prov.complete([{"role": "user", "content": "hi"}]):
                break
            return ConnectionResult(ok=True, detail="Connected (completion probe)")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if any(
                s in msg
                for s in (
                    "401",
                    "403",
                    "authentication",
                    "invalid api key",
                    "unauthorized",
                    "permission",
                    "x-api-key",
                )
            ):
                return ConnectionResult(
                    ok=False, detail=f"Auth failed — check the API key ({str(exc)[:80]})"
                )
            # A model-not-found / bad-request still proves the endpoint authenticated.
            if any(
                s in msg for s in ("not_found", "model", "400", "invalid_request", "bad request")
            ):
                return ConnectionResult(
                    ok=True, detail="Connected (key valid; verify the model id)"
                )
            return ConnectionResult(ok=False, detail=f"Connection failed: {str(exc)[:100]}")


#: provider TYPE → the spec that registered it. Populated by :func:`register_branded_app` (the
#: same import-time side effect that registers the type + catalog) so core can read an installed
#: app's DECLARATIONS — today its prices — without the app having to push them anywhere. Last-wins
#: on re-registration, mirroring ``register_catalog``.
def register_branded_app(spec: BrandedProviderSpec) -> tuple[Callable, Callable, Callable]:
    """Wire a branded/generic protocol provider app into the default registry and
    return its ``(_factory, create_provider, create_catalog)`` trio.

    Registers both the provider TYPE (inference) and the catalog (discovery) as the
    same import-time side effect the app loader triggers. Idempotent against reload
    (type registration is guarded; catalog registration is last-wins). The returned
    callables are what the app module exposes so the manifest's
    ``implementation: "provider:create_provider"`` resolves.
    """

    def _factory(
        *, entry: ProviderEntry, session_key: str | None = None, **kwargs: object
    ) -> ModelProvider:
        del session_key  # these providers are stateless
        cred = resolve_credential(entry, kwargs, label=spec.type)
        options = dict(entry.options or {})
        # Pop BOTH base_url and endpoint unconditionally (a short-circuit `or` would
        # leave the second in options → leak). base_url wins if both are set.
        _base = options.pop("base_url", None)
        _endpoint = options.pop("endpoint", None)
        base_url = str(_base or _endpoint or spec.default_base_url)
        # Credential resolution order for a config-registry entry:
        #   1. an explicit credential-store descriptor (entry.credential — resolved above),
        #      else 2. the per-instance api_key in entry.options, else 3. the spec's
        #      subscription credential_source, else 4. the spec's api_key_env, else
        #      5. the anon placeholder.
        # Hops 2-4 live in `resolve_spec_secret` so the config path below resolves the SAME
        # order from the SAME code. Two hand-maintained ladders drift, and this one had:
        # `create_provider` carried no subscription hop at all, so a subscription app wired
        # the documented way (`implementation: "provider:create_provider"`) built a provider
        # whose secret was the literal placeholder and 401'd at first use.
        # Pop BOTH key spellings unconditionally — a short-circuit `or` would leave the
        # second in options and leak it into extra_options → the SDK call kwargs.
        _snake_key = str(options.pop("api_key", "") or "")
        _camel_key = str(options.pop("apiKey", "") or "")
        if cred is None:
            cred, _ = resolve_spec_secret(spec, explicit_key=_snake_key or _camel_key)
        # Drop remaining routing/label fields that are NOT model-call params so they
        # don't leak into extra_options → request_kwargs → the SDK's stream()/create()
        # ("unexpected keyword argument …"). Only genuine call params (temperature,
        # top_p, …) should remain in extra_options.
        for _k in ("model", "default_model", "type", "name"):
            options.pop(_k, None)
        # The embedding use-case binding arrives as a build kwarg (the embedder
        # constructs its provider WITH the bound model — embed() takes no per-call
        # model). Thread it into extra_options where the protocol client reads it.
        _emb_model = kwargs.get("embedding_model")
        if _emb_model:
            options["embedding_model"] = str(_emb_model)
        # A per-call sampling temperature arrives the same way (HARNESS-CRAFT §2.1:
        # best-of-N needs N genuinely different samples). Same precedent as
        # ``embedding_model`` — a named build kwarg threaded into extra_options, where
        # both protocol clients already forward it into the request kwargs. It wins over
        # an entry-level temperature: the caller asking for THIS temperature is more
        # specific than the instance default.
        _temperature = kwargs.get("temperature")
        if isinstance(_temperature, (int, float)) and not isinstance(_temperature, bool):
            options["temperature"] = float(_temperature)
        max_tokens_value = options.pop("max_tokens", None)
        if isinstance(max_tokens_value, int):
            # entry override wins over the spec default
            eff_spec = BrandedProviderSpec(**{**spec.__dict__, "max_tokens": max_tokens_value})
        else:
            eff_spec = spec
        return _build_provider(
            eff_spec,
            model=entry.model or spec.default_model,
            credential=cred or _anon_credential(spec),
            base_url=base_url,
            extra_options=options,
        )

    def create_provider(config: dict[str, Any] | None = None) -> ModelProvider:
        cfg = dict(config or {})
        # The SAME order as `_factory` above, from the SAME helper — this is the path an
        # app manifest's `implementation: "provider:create_provider"` names, so a
        # subscription app must resolve its CLI's token here too. Both key spellings are
        # accepted for the same reason the entry path pops both.
        cred, _ = resolve_spec_secret(
            spec, explicit_key=str(cfg.get("api_key", "") or cfg.get("apiKey", "") or "")
        )
        cred = cred or _anon_credential(spec)
        base_url = str(cfg.get("endpoint") or cfg.get("base_url") or spec.default_base_url)
        model = str(cfg.get("model") or cfg.get("default_model") or spec.default_model)
        return _build_provider(spec, model=model, credential=cred, base_url=base_url)

    def create_catalog(options: dict[str, Any] | None = None, *, model: str = "") -> ModelCatalog:
        opts = options or {}
        # The configured default_model (or an explicit per-call model) is the only
        # selectable model for a no-discovery provider — thread it into the catalog
        # so its picker isn't empty.
        return BrandedCatalog(
            spec,
            endpoint=str(opts.get("endpoint") or opts.get("base_url") or ""),
            api_key=str(opts.get("api_key") or ""),
            default_model=str(model or opts.get("default_model") or opts.get("model") or ""),
        )

    # ── Registration (import-time side effect, like every model app) ──
    cap = ProviderCapability(
        type=spec.type,
        capabilities=spec.capabilities or frozenset({Capability.CHAT, Capability.STREAMING}),
        supports_streaming=True,
        supports_tools=Capability.CODE_TOOLS in spec.capabilities,
        supports_embeddings=Capability.EMBEDDING in spec.capabilities,
        supports_vision=Capability.VISION in spec.capabilities,
        max_context_tokens=0,
        notes=spec.notes or f"{spec.type}: {spec.protocol}-compatible endpoint.",
        prompt_cache=spec.prompt_cache,
    )
    try:
        get_default_registry().register_type(cap, _factory)
    except ProviderResolutionError:
        pass  # already registered (idempotent against reload)
    get_default_registry().register_catalog(spec.type, create_catalog)
    branded_specs._REGISTERED_SPECS[spec.type] = (
        spec  # so core can read this app's declarations (pricing)
    )

    return _factory, create_provider, create_catalog


def _anon_credential(spec: BrandedProviderSpec) -> Credential:
    """A placeholder credential for an unauth'd/optional-key provider. The OpenAI
    SDK client constructor requires a populated secret even when the endpoint
    ignores it (mirrors the vLLM app's placeholder)."""
    return Credential(name=f"{spec.type}-anon", kind="none", secret="unused", source="none")


__all__ = [
    "BrandedProviderSpec",
    "BrandedCatalog",
    # A subscription provider app declares its CLI's credential store with these two and
    # names it in ``BrandedProviderSpec.credential_source``; the resolver itself is core's
    # business, so it is not part of the app-facing surface.
    "SubscriptionSource",
    "register_subscription_source",
    "register_branded_app",
    # The registry ACCESSORS are deliberately not exported here, by this file's own rule:
    # a public SDK export with no consumer is a declared-but-inert surface, and the
    # inert-surface ratchet catches exactly that. ``registered_spec``, ``spec_pricing``,
    # ``spec_credential_source`` and ``spec_types_declaring_models`` were exported only
    # because CORE read them through this facade; core now imports them from
    # ``personalclaw.llm.branded_specs``, which is where they live, so every one of these
    # exports lost its last consumer. No app has ever used them (measured: 0 hits across
    # the apps repo, against 18 for ``BrandedProviderSpec`` and 27 for
    # ``register_branded_app``). They come back here the day an app needs one.
]

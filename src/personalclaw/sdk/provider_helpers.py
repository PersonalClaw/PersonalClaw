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

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from personalclaw.sdk.model import (
    AnthropicProvider,
    Capability,
    ConnectionResult,
    Credential,
    CredentialMissing,
    ModelCatalog,
    ModelInfo,
    ModelProvider,
    OpenAIProvider,
    ProviderCapability,
    ProviderEntry,
    ProviderResolutionError,
    get_default_registry,
    infer_capabilities,
    openai_compatible_list_models,
)


@dataclass(frozen=True)
class BrandedProviderSpec:
    """Everything that distinguishes one OpenAI-/Anthropic-compatible provider app
    from another. The rest of the wiring is identical (see module docstring)."""

    type: str  # the provider TYPE this app registers (e.g. "groq")
    protocol: str = "openai"  # "openai" | "anthropic" — which wire client to build
    default_base_url: str = ""  # the provider's OpenAI-/Anthropic-compatible base URL
    api_key_env: str = ""  # env var consulted when config carries no api_key
    default_model: str = ""  # model when neither entry nor config pins one
    max_tokens: int | None = None  # anthropic requires a max_tokens; openai leaves None
    capabilities: frozenset[Capability] = field(default_factory=frozenset)
    fallback_models: tuple[dict[str, Any], ...] = ()  # catalog rows when discovery is unavailable
    notes: str = ""


def _resolve_credential(entry: ProviderEntry, kwargs: dict, *, label: str) -> Credential | None:
    """Resolve a ProviderEntry's credential via the optional credential_store
    (registry contract), or None when the entry declares none. Mirrors the
    credential handling every model _factory uses."""
    if not entry.credential:
        return None
    store = kwargs.get("credential_store")
    if store is None:
        raise CredentialMissing(
            f"{label} provider entry {entry.name!r} declares credential "
            f"{entry.credential!r} but no credential_store was passed to build()"
        )
    cred = store.resolve(entry.credential)  # type: ignore[attr-defined]
    if cred is None or cred.secret is None:
        raise CredentialMissing(f"{label} credential {entry.credential!r} is not configured")
    return cred


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
        self._api_key = api_key or (
            os.environ.get(spec.api_key_env, "") if spec.api_key_env else ""
        )
        self._default_model = default_model

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
        live = await openai_compatible_list_models(
            self._endpoint,
            self._api_key,
            default_base=self._spec.default_base_url,
        )
        return live if live else self._fallback()

    async def test_connection(self) -> ConnectionResult:
        if not self._api_key:
            return ConnectionResult(
                ok=False, detail=f"No API key configured (set it or {self._spec.api_key_env})"
            )
        # Anthropic-wire providers expose NO models-list endpoint, so a models
        # count can't prove connectivity (a bring-your-own-endpoint app has an empty
        # fallback list → the old code wrongly reported "No models available" for a
        # perfectly good key). Probe the REAL path instead: a 1-token completion.
        # An auth failure (401/403) is a genuine failure; any model-level response
        # (success, or even a model-not-found/validation error) proves the key +
        # endpoint authenticated.
        if self._spec.protocol == "anthropic":
            return await self._probe_completion()
        models = await self.list_models()
        if not models:
            return ConnectionResult(ok=False, detail="No models available (check key/endpoint)")
        return ConnectionResult(ok=True, model_count=len(models))

    async def _probe_completion(self) -> ConnectionResult:
        """Verify an Anthropic-wire key/endpoint with a minimal completion. Auth
        errors → not connected; a model/validation error still means the credentials
        authenticated → connected."""
        model = self._spec.default_model or "claude-3-5-haiku-latest"
        try:
            from personalclaw.llm.anthropic import AnthropicProvider
            from personalclaw.llm.credentials import Credential

            prov = AnthropicProvider(
                model=model,
                credential=Credential(
                    name=self._spec.type, kind="api_key", secret=self._api_key, source="file"
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
        cred = _resolve_credential(entry, kwargs, label=spec.type)
        options = dict(entry.options or {})
        # Pop BOTH base_url and endpoint unconditionally (a short-circuit `or` would
        # leave the second in options → leak). base_url wins if both are set.
        _base = options.pop("base_url", None)
        _endpoint = options.pop("endpoint", None)
        base_url = str(_base or _endpoint or spec.default_base_url)
        # Credential resolution order for a config-registry entry:
        #   1. an explicit credential-store descriptor (entry.credential), else
        #   2. the per-instance api_key in entry.options (what the Add-Provider flow
        #      persists — MUST win over the env so a ZAI/Alibaba instance uses ITS
        #      key, not a global ANTHROPIC_API_KEY/OPENAI_API_KEY meant for another
        #      provider — the "wrong key → 401" bug), else
        #   3. the spec's api_key_env, else 4. anon placeholder.
        _opt_key = str(options.pop("api_key", "") or options.pop("apiKey", "") or "")
        if cred is None and _opt_key:
            cred = Credential(name=spec.type, kind="api_key", secret=_opt_key, source="file")
        elif cred is None and spec.api_key_env:
            _env_key = os.environ.get(spec.api_key_env, "")
            if _env_key:
                cred = Credential(name=spec.type, kind="api_key", secret=_env_key, source="env")
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
        api_key = str(
            cfg.get("api_key", "")
            or (os.environ.get(spec.api_key_env, "") if spec.api_key_env else "")
        )
        cred = (
            Credential(name=spec.type, kind="api_key", secret=api_key, source="file")
            if api_key
            else _anon_credential(spec)
        )
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
    )
    try:
        get_default_registry().register_type(cap, _factory)
    except ProviderResolutionError:
        pass  # already registered (idempotent against reload)
    get_default_registry().register_catalog(spec.type, create_catalog)

    return _factory, create_provider, create_catalog


def _anon_credential(spec: BrandedProviderSpec) -> Credential:
    """A placeholder credential for an unauth'd/optional-key provider. The OpenAI
    SDK client constructor requires a populated secret even when the endpoint
    ignores it (mirrors the vLLM app's placeholder)."""
    return Credential(name=f"{spec.type}-anon", kind="none", secret="unused", source="none")


__all__ = ["BrandedProviderSpec", "BrandedCatalog", "register_branded_app"]

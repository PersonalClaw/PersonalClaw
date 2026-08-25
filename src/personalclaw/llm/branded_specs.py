"""The branded-provider spec registry and its credential ladder — CORE, not the SDK.

These live below the ``personalclaw.sdk.*`` boundary on purpose. A branded provider **app**
declares a :class:`BrandedProviderSpec` and registers it through
``sdk.provider_helpers.register_branded_app``; the registry it lands in, and the
credential-resolution order used to turn a spec into a usable secret, are **core state and
core policy**. Four core modules read them (``llm/catalog.py``, ``providers/loader.py``,
``routing/rates.py``, ``inbound/capture_proxy.py``), and before this module existed every one
of them reached UP through the app-facing facade to do it — the inversion
``structural-import-direction`` names ``core-must-not-import-its-own-published-facade``.

``sdk/provider_helpers.py`` now re-exports everything here, so an app's import path is
unchanged; the direction of the dependency is what changed. The credential ladder is defined
once, here, which is the property that matters: a hand-copied second spelling of it is what
made a subscription provider 401 at first use.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from personalclaw.llm.capabilities import Capability
from personalclaw.llm.credentials import Credential
from personalclaw.llm.prompt_cache import PromptCache
from personalclaw.llm.registry import CredentialMissing, ProviderEntry
from personalclaw.llm.subscription_credentials import resolve_subscription_credential


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
    # Graded prompt-cache support this provider declares. Defaults NONE (no caching
    # hint). An app whose family caches a stable prefix on its own sets AUTOMATIC; one
    # needing a per-request marker sets EXPLICIT. Threaded into ProviderCapability below.
    prompt_cache: PromptCache = PromptCache.NONE
    # OPTIONAL per-model prices this app ships: {model_pattern: {in_per_mtok, out_per_mtok}}
    # in USD per 1,000,000 tokens, where model_pattern is a model id or a glob
    # ("claude-sonnet-*"). Prices belong beside default_model/capabilities — the same place the
    # rest of an app's model facts live. Read by routing/rates.py:rate_for as the app-default
    # tier, under the user's ~/.personalclaw/model_rates.json overlay (MRT-2, §5.1). Empty means
    # "this app declares no prices", which resolves to a lower tier and never to a free model.
    # ``hash=False`` keeps the frozen dataclass hashable despite the dict (equality still counts
    # it); treat the map as read-only, like every other field on a frozen spec.
    pricing: dict[str, dict[str, float]] = field(default_factory=dict, hash=False)
    # OPTIONAL id of a registered :class:`SubscriptionSource` (see
    # ``llm/subscription_credentials.py``). Set it when this provider's vendor bills by
    # SUBSCRIPTION and the user has no API key to paste — they signed the vendor's own agent
    # CLI in, and the token lives in a store that CLI owns. The resolver reads that store
    # READ-ONLY and sits at ONE fixed place in the credential order (below the explicit
    # entry.credential and options.api_key, above spec.api_key_env) — it is NOT a second way
    # to set an API key and can never override a key the user chose. Empty (the norm) means
    # this app rides no subscription.
    credential_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        """A JSON-round-trippable view of the spec (enums → their values, tuples → lists).

        Paired with :meth:`from_dict` so ``from_dict(spec.to_dict()) == spec`` — the round-trip
        discipline the repo enforces on every persisted/serialized shape, so a later-added field
        (``pricing`` was one) can't silently fail to survive a save/load.
        """
        return {
            "type": self.type,
            "protocol": self.protocol,
            "default_base_url": self.default_base_url,
            "api_key_env": self.api_key_env,
            "default_model": self.default_model,
            "max_tokens": self.max_tokens,
            "capabilities": sorted(c.value for c in self.capabilities),
            "fallback_models": [dict(m) for m in self.fallback_models],
            "notes": self.notes,
            "prompt_cache": self.prompt_cache.value,
            "pricing": {
                str(pattern): {str(k): float(v) for k, v in dict(row).items()}
                for pattern, row in self.pricing.items()
            },
            "credential_source": self.credential_source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BrandedProviderSpec:
        """Rebuild a spec from :meth:`to_dict` output. Unknown keys are ignored; absent keys take
        the field default (so an older serialized spec still loads)."""
        raw_cache = data.get("prompt_cache", PromptCache.NONE)
        return cls(
            type=str(data.get("type", "")),
            protocol=str(data.get("protocol", "openai")),
            default_base_url=str(data.get("default_base_url", "")),
            api_key_env=str(data.get("api_key_env", "")),
            default_model=str(data.get("default_model", "")),
            max_tokens=(int(data["max_tokens"]) if data.get("max_tokens") is not None else None),
            capabilities=frozenset(Capability(c) for c in data.get("capabilities", ()) or ()),
            fallback_models=tuple(dict(m) for m in data.get("fallback_models", ()) or ()),
            notes=str(data.get("notes", "")),
            prompt_cache=PromptCache(raw_cache),
            pricing={
                str(pattern): {str(k): float(v) for k, v in dict(row).items()}
                for pattern, row in (data.get("pricing", {}) or {}).items()
            },
            credential_source=str(data.get("credential_source", "") or ""),
        )


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


def _resolve_spec_secret(
    spec: BrandedProviderSpec, *, explicit_key: str = ""
) -> tuple[Credential | None, str]:
    """The ONE credential-resolution order a branded spec's secret follows, shared by every
    surface that needs it (both factories and the catalog).

    ``explicit_key`` is whatever the calling surface offers as an explicit, user-typed key:
    ``entry.options["api_key"]`` on the registry path, ``config["api_key"]`` on the config
    path. The order below it:

      1. ``explicit_key`` — the per-instance key the Add-Provider flow persists. MUST win
         over the env so a ZAI/Alibaba instance uses ITS key, not a global
         ANTHROPIC_API_KEY/OPENAI_API_KEY meant for another provider (the "wrong key → 401"
         bug), else
      2. the spec's subscription ``credential_source`` — an already-signed-in agent CLI's own
         store, read READ-ONLY. BELOW every explicit choice so it can never silently outrank
         a credential the user set, and ABOVE the env so a subscription app works with
         nothing configured at all, else
      3. the spec's ``api_key_env``.

    Independent hops, not an ``elif`` chain: a source that is merely not signed in must fall
    THROUGH to ``api_key_env``, never short-circuit it.

    Returns ``(credential, reason)``. ``credential`` is None when no secret was found at all,
    and each caller decides what that means — a factory substitutes the anon placeholder so
    the protocol client can still be constructed, while the catalog reports "not configured".
    ``reason`` carries the resolver's displayable, secret-free explanation when the spec
    declares a subscription source that is NOT usable ("" otherwise), so a caller can say
    "sign in with `x login` first" instead of naming an env var the app doesn't have.

    ``entry.credential`` (the explicit credential-store descriptor) outranks everything here
    but is registry-only, so it is resolved by :func:`_resolve_credential` BEFORE this is
    consulted: five hops on the registry path, four on the config path, identical tail.
    """
    if explicit_key:
        return (Credential(name=spec.type, kind="api_key", secret=explicit_key, source="file"), "")
    reason = ""
    if spec.credential_source:
        auth = resolve_subscription_credential(spec.credential_source)
        if auth.logged_in and auth.secret:
            # ``source="file"`` is factually where it came from (the CLI's own on-disk
            # store); the credential-store Literal is deliberately not widened for this.
            return (
                Credential(name=spec.type, kind="oauth2", secret=auth.secret, source="file"),
                "",
            )
        reason = auth.reason
    if spec.api_key_env:
        env_key = os.environ.get(spec.api_key_env, "")
        if env_key:
            return (Credential(name=spec.type, kind="api_key", secret=env_key, source="env"), "")
    return (None, reason)


_REGISTERED_SPECS: dict[str, BrandedProviderSpec] = {}


def registered_spec(provider: str) -> BrandedProviderSpec | None:
    """The registered spec for ``provider``, or None.

    ``provider`` may be a provider TYPE ("groq") or a user-named INSTANCE of one ("groq-work" —
    instances are named freely in ``active_models.json``). Resolution: exact type, then
    case-insensitive type, then — only when EXACTLY ONE registered type appears in the name — that
    type. An ambiguous or unrecognized name resolves to None rather than to a guess.
    """
    name = str(provider or "").strip()
    if not name:
        return None
    spec = _REGISTERED_SPECS.get(name)
    if spec is not None:
        return spec
    lowered = name.lower()
    for known, known_spec in _REGISTERED_SPECS.items():
        if known.lower() == lowered:
            return known_spec
    hits = [s for known, s in _REGISTERED_SPECS.items() if known and known.lower() in lowered]
    return hits[0] if len(hits) == 1 else None


def spec_pricing(provider: str) -> dict[str, dict[str, float]]:
    """The app-declared ``{model_pattern: {in_per_mtok, out_per_mtok}}`` map for ``provider``, or
    an empty map when the provider is unknown or declares no prices (never a fabricated rate)."""
    spec = registered_spec(provider)
    return dict(spec.pricing) if spec is not None and spec.pricing else {}


def spec_credential_source(provider: str) -> str:
    """The app-declared subscription ``credential_source`` for ``provider``, or ``""``.

    The core-facing reader that lets ``providers/loader.py`` derive an ``availability()``
    probe for a subscription provider app without importing the app or knowing its vendor.
    Same shape and precedent as :func:`spec_pricing`: one narrow question answered from the
    registered spec, keeping :func:`registered_spec` module-internal.
    """
    spec = registered_spec(provider)
    return str(spec.credential_source) if spec is not None and spec.credential_source else ""


def spec_types_declaring_models(markers: tuple[str, ...]) -> frozenset[str]:
    """Registered provider TYPES whose app declares at least one model id containing one of
    ``markers`` (case-insensitive), looking at its ``default_model`` and ``fallback_models``.

    The narrow reader behind ``llm/catalog.model_family_provider_types``: it answers "which
    installed apps say they serve this model family?" so that map does not have to name every
    app by hand. Marker semantics stay in ``catalog.py`` (all model-id classification lives
    there); this side only knows what each spec DECLARED. Same precedent as :func:`spec_pricing`
    and :func:`spec_credential_source` — one narrow question, :func:`registered_spec` stays
    module-internal.

    An app that declares no models (pure live discovery) contributes nothing, which is the
    honest answer: nothing was declared.
    """
    wanted = tuple(m.lower() for m in markers if m)
    if not wanted:
        return frozenset()
    out: set[str] = set()
    for provider_type, spec in _REGISTERED_SPECS.items():
        declared = [str(spec.default_model or "")] + [
            str(row.get("id", "") or "") for row in spec.fallback_models
        ]
        if any(m in model_id.lower() for model_id in declared if model_id for m in wanted):
            out.add(provider_type)
    return frozenset(out)

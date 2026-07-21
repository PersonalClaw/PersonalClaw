"""Provider-resolution unification.

The active model selected for a use case (``active_models.json``,
``"provider:model"``) resolves through the config/registry path, pinned to the
named provider + model — so the Settings → Models selection and capability
fallback share one resolution path.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fakes import FAKE_MODEL_CAPABILITY, FAKE_MODEL_TYPE, ensure_fake_model_type

import personalclaw.providers.provider_bridge as pb
from personalclaw.llm.registry import ProviderEntry, get_default_registry

# Model provider TYPES (openai/anthropic/ollama/bedrock) register from their
# standalone apps now, so they aren't in the registry in a bare unit test. These
# resolution tests need SOME concrete model-provider type — register the in-tree
# fake (tests/fakes.py) and DECLARE the caps needed on each entry (the resolver
# reads entry.declared_capabilities first). Resolution builds via registry.build
# → the registered type factory, so tests patch registry.build.
ensure_fake_model_type(get_default_registry())
_MODEL_TYPE = FAKE_MODEL_TYPE
_MODEL_CAPS = FAKE_MODEL_CAPABILITY.capabilities


def _ensure_registry_entry(name: str, *, model: str = "gpt-x") -> None:
    reg = get_default_registry()
    if any(e.name == name for e in reg.list_entries()):
        return
    reg.register_entry(
        ProviderEntry(
            name=name,
            type=_MODEL_TYPE,
            model=model,
            options={"endpoint": "https://x/v1", "api_key": "k"},
            declared_capabilities=_MODEL_CAPS,
        )
    )


def test_active_selection_resolves_pinned_to_provider_and_model():
    _ensure_registry_entry("UnifyCloud")
    seen = {}
    real_resolver = pb._resolve_from_config_registry

    def spy(use_case, **kw):
        seen["provider_hint"] = kw.get("provider_hint")
        seen["model_override"] = kw.get("model_override")
        return real_resolver(use_case, **kw)

    # ``embedding`` so the native-agent branch (chat) doesn't short-circuit.
    reg = get_default_registry()
    with (
        patch(
            "personalclaw.providers.use_cases.active_model_refs",
            return_value=["UnifyCloud:my-embed"],
        ),
        patch.object(pb, "_resolve_from_config_registry", side_effect=spy),
        patch.object(reg, "build", return_value=MagicMock(name="ModelProvider")),
    ):
        prov = pb.resolve_provider_for_use_case("embedding", agent=None)

    assert prov is not None
    # The active selection pinned resolution to the named provider + model.
    assert seen.get("provider_hint") == "UnifyCloud"
    assert seen.get("model_override") == "my-embed"


def test_image_modality_maps_to_vision_capability():
    """The Settings→Models capability 'image_modality' (and 'video_modality') has no
    matching Capability enum member — it must normalise to Capability.VISION, or
    _resolve_from_config_registry's Capability(cap) raises and every vision/ocr
    resolution fails even with a vision model bound (the video-extraction bug)."""
    from personalclaw.llm.capabilities import Capability

    assert pb._capability_enum("image_modality") == Capability.VISION
    assert pb._capability_enum("video_modality") == Capability.VISION
    assert pb._capability_enum("vision") == Capability.VISION  # already an enum value
    assert pb._capability_enum("chat") == Capability.CHAT  # passthrough
    assert pb._capability_enum("not_a_capability") is None  # clean None, no raise


def test_vision_use_case_resolves_to_vision_capable_provider():
    """The image-understanding use-case whose bound model lives on a VISION-capable
    provider must resolve (build) — closing the can_resolve/resolve divergence:
    previously can_resolve said True (a ref existed) but resolve raised because the
    capability name didn't map. Uses a registered VISION-capable entry. (Ingestion's
    OCR/vision nodes resolve DIRECTLY to ``image_modality`` now — no ingestion use-case.)"""
    reg = get_default_registry()
    if not any(e.name == "VisionCloud" for e in reg.list_entries()):
        # the entry declares VISION directly (resolver reads declared_capabilities)
        reg.register_entry(
            ProviderEntry(
                name="VisionCloud",
                type=_MODEL_TYPE,
                model="vlm-1",
                options={"endpoint": "https://x/v1", "api_key": "k"},
                declared_capabilities=_MODEL_CAPS,
            )
        )
    with (
        patch(
            "personalclaw.providers.use_cases.active_model_refs", return_value=["VisionCloud:vlm-1"]
        ),
        patch.object(reg, "build", return_value=MagicMock(name="ModelProvider")),
    ):
        prov = pb.resolve_provider_for_use_case("image_modality", agent=None)
    assert prov is not None  # resolved, did not raise


def test_active_selection_naming_unknown_provider_raises_immediately():
    # An active selection naming a provider the registry can't build (its app isn't
    # installed) must raise a CLEAR error immediately — NOT silently fall through to
    # the implicit fallback (the stale-Bedrock-pin → Ollama-404 bug). The error
    # names the provider and points at the App Store / Settings → Models.
    with (
        patch(
            "personalclaw.providers.use_cases.active_model_refs",
            return_value=["DoesNotExistAnywhere:m"],
        ),
        patch("personalclaw.llm.registry.get_default_registry") as gdr,
    ):
        reg = MagicMock()
        reg.list_entries.return_value = []
        gdr.return_value = reg
        try:
            pb.resolve_provider_for_use_case("embedding", agent=None)
            raised, msg = False, ""
        except pb.ProviderResolutionError as exc:
            raised, msg = True, str(exc)
    assert raised
    assert "DoesNotExistAnywhere" in msg and "isn't available" in msg


def test_fallback_chat_model_skips_stale_default_agent_pin():
    """_fallback_chat_model must NOT return a default-agent pin that names an
    uninstalled provider (a stale 'Bedrock:…' after removal) — that dead ref would
    be handed to whatever provider resolves (→ wrong-provider 404). It reconciles
    the pin to '' and falls through to the first active chat model instead."""
    from types import SimpleNamespace

    import personalclaw.providers.provider_bridge as pbmod

    class _Cfg:
        agents = {"default": SimpleNamespace(model="Bedrock:global.anthropic.claude-opus-4-8")}

    with (
        patch("personalclaw.config.loader.AppConfig") as AppCfg,
        patch("personalclaw.agents.defaults.default_agent_name", return_value="default"),
        patch(
            "personalclaw.providers.use_cases.active_model_refs",
            return_value=["OpenAI:gpt-4o-mini"],
        ),
    ):
        AppCfg.load.return_value = _Cfg()
        got = pbmod._fallback_chat_model()
    # The stale Bedrock pin is dropped; the active OpenAI model's bare id wins.
    assert got == "gpt-4o-mini", got


def test_unknown_selection_does_not_fall_back_to_other_provider():
    # The KEY behavior change: even when a DIFFERENT capable provider IS available,
    # a selection naming an uninstalled provider must NOT silently resolve to the
    # other one — it blocks with the clear error (per the user's decision). The
    # implicit fallback applies ONLY when there is no active selection at all.
    _ensure_registry_entry("SomeWorkingProvider")
    reg = get_default_registry()
    with (
        patch(
            "personalclaw.providers.use_cases.active_model_refs", return_value=["UninstalledProv:m"]
        ),
        patch.object(reg, "build", return_value=MagicMock(name="ModelProvider")),
    ):
        # build() would succeed for a real entry, but "UninstalledProv" isn't one →
        # the pinned attempt returns None → we must raise, not fall back.
        try:
            pb.resolve_provider_for_use_case("embedding", agent=None)
            raised = False
        except pb.ProviderResolutionError:
            raised = True
    assert raised, "a stale selection must block, not fall back to another provider"


def test_model_axis_only_skips_acp_agent_entry():
    """Regression: the native loop's inner model must never resolve to an
    agent-runtime (acp_agent) entry — it calls ModelProvider.complete(), which
    an AcpAgentProvider does not implement ("object has no attribute 'complete'").
    With _model_axis_only set, an acp_agent entry is skipped in favor of a model
    entry even when the acp_agent declares the chat capability.
    """
    reg = get_default_registry()
    # Register an acp_agent entry that declares CHAT (the failing-stack shape).
    if not any(e.name == "SomeAcpAgent" for e in reg.list_entries()):
        acp_cap = reg.capability_of("acp_agent")
        reg.register_entry(
            ProviderEntry(
                name="SomeAcpAgent",
                type="acp_agent",
                model="",
                options={"command": ["some-cli"]},
                declared_capabilities=acp_cap.capabilities,
            )
        )
    _ensure_registry_entry("UnifyCloud")  # a real ModelProvider entry

    with patch.object(reg, "build", return_value=MagicMock(name="ModelProvider")) as build:
        prov = pb._resolve_from_config_registry("chat", _model_axis_only=True)
    assert prov is not None
    # Resolution now runs through the single registry.build path for BOTH axes, so
    # the acp_agent-skip is proven by the TYPE of the entry it selected: it built a
    # model entry, NOT an acp_agent — even though the acp_agent also declares CHAT.
    # (Assert on the entry TYPE, not a specific name: the shared registry singleton
    # may hold other CHAT-capable model entries registered by earlier test files, so
    # the exact winner is order-dependent, but it must never be an agent-runtime.)
    assert build.called
    built_name = build.call_args.args[0]
    built_entry = next(e for e in reg.list_entries() if e.name == built_name)
    assert built_entry.type != "acp_agent"


def test_model_axis_only_resolves_acp_agent_without_flag():
    """Counterpart: without the flag, an acp_agent entry IS a valid chat
    candidate and is built via the registry's own factory.
    """
    reg = get_default_registry()
    if not any(e.name == "SomeAcpAgent" for e in reg.list_entries()):
        acp_cap = reg.capability_of("acp_agent")
        reg.register_entry(
            ProviderEntry(
                name="SomeAcpAgent",
                type="acp_agent",
                model="",
                options={"command": ["some-cli"]},
                declared_capabilities=acp_cap.capabilities,
            )
        )
    # Pin to the acp_agent entry via provider_hint and confirm it routes through
    # registry.build (the single build path — the agent-runtime factory).
    with patch.object(reg, "build", return_value=MagicMock(name="AcpAgentProvider")) as rbuild:
        prov = pb._resolve_from_config_registry("chat", provider_hint="SomeAcpAgent")
    assert prov is not None
    assert rbuild.called
    assert rbuild.call_args.args[0] == "SomeAcpAgent"


def test_config_resolver_honors_explicit_provider_hint():
    _ensure_registry_entry("UnifyCloud")
    # A second entry the hint must NOT select, to prove the hint pins the choice.
    reg = get_default_registry()
    if not any(e.name == "OtherCloud" for e in reg.list_entries()):
        reg.register_entry(
            ProviderEntry(
                name="OtherCloud",
                type=_MODEL_TYPE,
                model="other-model",
                options={"endpoint": "https://o/v1", "api_key": "k"},
                declared_capabilities=_MODEL_CAPS,
            )
        )
    with patch.object(reg, "build", return_value=MagicMock()) as build:
        prov = pb._resolve_from_config_registry("chat", provider_hint="UnifyCloud")
    assert prov is not None
    # Resolution builds via registry.build(build_name, ...); the hint pins build_name
    # to the HINTED entry (UnifyCloud), not the other capable entry (OtherCloud) —
    # proving the hint pinned the choice.
    assert build.call_args.args[0] == "UnifyCloud"


def test_model_override_threaded_as_build_kwarg():
    """A model_override (e.g. the active model an axis resolved from
    active_models.json, which the entry itself doesn't pin) must reach the provider
    factory as the ``model`` build kwarg — every model factory honors ``model`` over
    ``entry.model``. Regression: this used to rely on re-registering a model-replaced
    entry, but register_entry raises on a duplicate name, so the override was silently
    lost and the provider fell back to the entry/default model."""
    # Entry with NO pinned model (like the config.json Bedrock entry — the model
    # lives in active_models.json and is resolved per use-case).
    reg = get_default_registry()
    if not any(e.name == "UnifyPinless" for e in reg.list_entries()):
        reg.register_entry(
            ProviderEntry(
                name="UnifyPinless",
                type=_MODEL_TYPE,
                model="",
                options={"endpoint": "https://p/v1", "api_key": "k"},
                declared_capabilities=_MODEL_CAPS,
            )
        )
    with patch.object(reg, "build", return_value=MagicMock()) as build:
        pb._resolve_from_config_registry(
            "chat",
            provider_hint="UnifyPinless",
            model_override="pinned-model-x",
        )
    # The override is threaded as the `model` build kwarg (not via a doomed
    # entry re-registration).
    assert build.call_args.kwargs.get("model") == "pinned-model-x"


def test_provider_kind_native_routes_to_native_runtime_despite_name_guess():
    """Regression (native chat served by ACP): chat_runner passes the ACP-internal
    provider_agent name (e.g. "personalclaw") as `agent`, which does NOT match the
    agent profile key — so _agent_provider_kind misses the profile and falls back
    to the global "acp" default. The explicit `provider_kind` (resolved from the
    agent's PROFILE by resolve_agent_bindings) must win and build the native loop."""
    called = {}

    def _fake_native(**kw):
        called["native"] = True
        return MagicMock(name="NativeRuntime")

    with (
        patch.object(pb, "_build_native_runtime", side_effect=_fake_native),
        patch.object(pb, "_agent_provider_kind", return_value="acp"),
    ):  # name-guess wrong
        prov = pb.resolve_provider_for_use_case(
            "chat", agent="personalclaw", provider_kind="native"
        )
    assert prov is not None
    assert called.get("native") is True


def test_extra_tool_roots_forwarded_to_native_runtime():
    """A Code/Goal-Loop worker passes extra_tool_roots (its project files dir) so the
    native file tools can reach engine files outside the workspace cwd. It must reach
    _build_native_runtime."""
    seen = {}

    def _fake_native(**kw):
        seen.update(kw)
        return MagicMock(name="NativeRuntime")

    with patch.object(pb, "_build_native_runtime", side_effect=_fake_native):
        pb.resolve_provider_for_use_case(
            "chat",
            agent="personalclaw-coder",
            provider_kind="native",
            extra_tool_roots=["/tmp/code/abcd1234"],
        )
    assert seen.get("extra_tool_roots") == ["/tmp/code/abcd1234"]


def test_extra_tool_roots_not_leaked_to_acp_resolver():
    """On the ACP path extra_tool_roots must be popped, not forwarded into the
    config-registry resolver (which doesn't accept it and would raise)."""
    captured = {}

    def _fake_cfg(*a, **kw):
        captured.update(kw)
        return MagicMock(name="AcpProvider")

    with (
        patch.object(pb, "_agent_provider_kind", return_value="native"),
        patch("personalclaw.providers.use_cases.active_model_refs", return_value=[]),
        patch.object(pb, "_resolve_from_config_registry", side_effect=_fake_cfg),
    ):
        pb.resolve_provider_for_use_case(
            "chat", agent="x", provider_kind="acp", extra_tool_roots=["/tmp/code/x"]
        )
    assert "extra_tool_roots" not in captured


def test_provider_kind_acp_does_not_build_native():
    """Counterpart: provider_kind="acp" (or acp:<cli>) must NOT build the native
    loop even if the name-based guess would say native."""
    called = {}

    def _fake_native(**kw):
        called["native"] = True
        return MagicMock(name="NativeRuntime")

    with (
        patch.object(pb, "_build_native_runtime", side_effect=_fake_native),
        patch.object(pb, "_agent_provider_kind", return_value="native"),
        patch("personalclaw.providers.use_cases.active_model_refs", return_value=[]),
        patch.object(
            pb, "_resolve_from_config_registry", return_value=MagicMock(name="AcpProvider")
        ),
    ):
        prov = pb.resolve_provider_for_use_case(
            "chat", agent="whatever", provider_kind="acp:claude-code"
        )
    assert prov is not None
    assert called.get("native") is None


def test_colon_qualified_override_routes_to_named_provider():
    """Regression (bug #14 — composer per-message model pick): a colon-qualified
    override "Provider:model" must route to the NAMED provider, not the first
    active chat ref. The chat model picker offers models from EVERY active chat
    provider, so picking "PickB:model-b" while the FIRST active ref is
    "PickA:model-a" used to send "PickB:model-b" to PickA's client → 404. The
    top-level resolver must hand a colon-qualified override to the config resolver
    (which routes via provider_hint), not fall through to the active-refs loop."""
    _ensure_registry_entry("PickA", model="model-a")
    _ensure_registry_entry("PickB", model="model-b")
    reg = get_default_registry()
    with (
        patch(
            "personalclaw.providers.use_cases.active_model_refs",
            return_value=["PickA:model-a", "PickB:model-b"],
        ),
        patch.object(reg, "build", return_value=MagicMock()) as build,
    ):
        prov = pb.resolve_provider_for_use_case(
            "chat",
            agent=None,
            model_override="PickB:model-b",
            _force_model_axis=True,
        )
    assert prov is not None
    # Routed to PickB (the prefixed provider), NOT PickA (the first active ref),
    # and the bare model id reached the factory.
    assert build.call_args.args[0] == "PickB"
    assert build.call_args.kwargs.get("model") == "model-b"


def test_strip_provider_prefix_uses_known_provider_names(monkeypatch):
    """Regression (bug #14 second facet): _strip_provider_prefix must strip a
    CONFIG-provider prefix even when the live ModelProvider registry hasn't loaded
    that entry in this call path (its register_type() is lazy). It falls back to the
    authoritative config-provider name set. Without this the bare id "OpenAI:gpt-5.4"
    reaches the SDK verbatim → 404. A colon-bearing bare id whose prefix ISN'T a
    provider (e.g. "gpt-oss:20b") must be left intact."""
    # Force the registry-entry path to MISS (empty registry), so the strip must
    # rely on the known-names fallback. Patch registry.list_entries to return [].
    reg = get_default_registry()
    monkeypatch.setattr(reg, "list_entries", lambda: [])
    monkeypatch.setattr(
        "personalclaw.providers.use_cases._known_provider_names",
        lambda: {"OpenAI", "Anthropic", "Ollama"},
    )
    assert pb._strip_provider_prefix("OpenAI:gpt-5.4") == "gpt-5.4"
    assert pb._strip_provider_prefix("Anthropic:claude-opus-4-8") == "claude-opus-4-8"
    # Not a provider prefix → left intact (ollama tag form).
    assert pb._strip_provider_prefix("gpt-oss:20b") == "gpt-oss:20b"

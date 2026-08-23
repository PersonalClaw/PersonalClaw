"""PHF-7 — the seam that BINDS the offline scripted provider, and its guardrails.

Four properties of ``personalclaw.llm.registry``'s scripted registration:

1. With the opt-in ABSENT the type is not registered at all — the registry's type
   set is identical to a fresh one, asserted against a NON-EMPTY baseline so
   "unchanged" cannot pass vacuously.
2. With the opt-in PRESENT the type registers once, stays idempotent across
   repeated ``sync_entries_from_config()`` calls (its docstring claims idempotence,
   so it is proven rather than trusted), and ``build()`` returns the provider with
   NO credential.
3. A real, credential-declaring type STILL raises ``CredentialMissing`` under the
   same opt-in. A credential-free provider is a security-adjacent seam; this is the
   rail that stops it becoming an auth hole, and it goes through the production
   credential helper rather than a hand-rolled imitation of it.
4. The declared capability set is exactly the honest minimum, asserted as an
   EQUALITY so a later widening reds instead of sliding in.

``personalclaw.llm.scripted`` is a sibling deliverable of the same atom and is not
on this branch. These tests inject a stub module into ``sys.modules`` under its
real name: that lets the suite run standalone AND proves the production code really
imports that symbol from that module (a wrong module path would fail here).
"""

import json
import sys
import types

import pytest

import personalclaw.llm as _llm_pkg
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import (
    SCRIPTED_PROVIDER_CAPABILITY,
    SCRIPTED_PROVIDER_ENTRY_NAME,
    SCRIPTED_PROVIDER_ENV,
    SCRIPTED_PROVIDER_MODEL,
    SCRIPTED_PROVIDER_TYPE,
    CredentialMissing,
    ProviderEntry,
    ProviderRegistry,
    ProviderResolutionError,
    get_default_registry,
    reset_default_registry,
    scripted_provider_enabled,
    sync_entries_from_config,
)

# ── The sibling's provider, stubbed ───────────────────────────────────────────


class _StubScriptedProvider:
    """Stands in for the sibling's ``ScriptedProvider``.

    Mirrors the real fixture's constructor EXACTLY: it takes nothing. The real
    ``ScriptedProvider.__init__`` is ``(self)`` on purpose — a ``script_path`` kwarg
    would be a hole in its env gate, and its own test pins ``inspect.signature`` to
    ``["self"]``. This stub previously accepted a keyword-only ``model``, mirroring a
    factory call that did not typecheck against the real class; keeping the two in step
    is the whole point of a stub, and `ignore_missing_imports` means nothing else checks
    it. It deliberately accepts NO credential argument either — a scripted fixture that
    could take one would blur the exemption these tests fence.
    """

    def __init__(self) -> None:
        self.model = ""


@pytest.fixture
def stub_scripted_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a stub ``personalclaw.llm.scripted`` for the duration of one test."""
    mod = types.ModuleType("personalclaw.llm.scripted")
    mod.ScriptedProvider = _StubScriptedProvider  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "personalclaw.llm.scripted", mod)
    # Also bind it on the parent package so the ``from ... import`` fromlist path
    # resolves regardless of which branch the import machinery takes.
    monkeypatch.setattr(_llm_pkg, "scripted", mod, raising=False)
    return mod


# ── A REAL, credential-declaring type (the anti-bypass counterparty) ─────────

_REAL_TYPE = "credentialed_real"

_REAL_CAPABILITY = ProviderCapability(
    type=_REAL_TYPE,
    capabilities=frozenset({Capability.CHAT}),
    supports_streaming=True,
    supports_tools=True,
    supports_embeddings=False,
    supports_vision=False,
    max_context_tokens=0,
    notes="stands in for an installed vendor model app",
)


def _real_factory(*, entry: ProviderEntry, session_key: str | None = None, **kwargs: object):
    """A real provider's factory, refusing through the PRODUCTION credential path.

    ``sdk.provider_helpers._resolve_credential`` is the one helper every shipped
    model-app factory calls, and it is what raises ``CredentialMissing`` — so this
    stand-in exercises the real refusal instead of imitating it.
    """
    # ``sdk.model`` FIRST, deliberately: the two modules import each other (model.py
    # imports BrandedProviderSpec from provider_helpers at its foot, provider_helpers
    # imports from model.py at its head), so importing provider_helpers first raises
    # ImportError on a partially initialized module. Production always reaches
    # ``sdk.model`` first; this keeps the test on the same order.
    import personalclaw.sdk.model  # noqa: F401
    from personalclaw.sdk.provider_helpers import _resolve_credential

    _resolve_credential(entry, kwargs, label=_REAL_TYPE)
    # Reaching this line means the credential check did NOT refuse — which is
    # exactly the auth hole the rail exists to catch. Return rather than raise, so
    # the failure reads as the honest "DID NOT RAISE CredentialMissing".
    return _StubScriptedProvider(model=entry.model)


def _seed_real_type(registry: ProviderRegistry) -> None:
    """Register the stand-in real type.

    Doubles as the VACUITY FLOOR for the opt-in-absent test: it is what makes the
    "type set unchanged" comparison run against a non-empty set.
    """
    registry.register_type(_REAL_CAPABILITY, _real_factory)


def _registered_types(registry: ProviderRegistry) -> set[str]:
    """The registry exposes no public type-set accessor (``capability_of`` answers
    one type at a time), so read the mapping directly — the same access
    ``sync_entries_from_config`` itself takes on ``_entries``."""
    return set(registry._capabilities)


def _write_config(providers: list[dict]) -> None:
    """Write a ``config.json`` at the path production reads, under the fake home."""
    from personalclaw.config.loader import config_path

    config_path().write_text(json.dumps({"providers": providers}), encoding="utf-8")


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_registry_and_home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """No shared registry state, no real home, no inherited opt-in.

    ``reset_default_registry()`` runs on BOTH sides: this suite runs under xdist
    beside everything else, and a leaked ``scripted`` type would make an unrelated
    test's provider resolution pick up a fixture.
    """
    monkeypatch.delenv(SCRIPTED_PROVIDER_ENV, raising=False)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    reset_default_registry()
    yield
    reset_default_registry()


@pytest.fixture
def opt_in(monkeypatch: pytest.MonkeyPatch, tmp_path) -> str:
    """Set the one explicit opt-in, naming a script path."""
    script = tmp_path / "chat-script.json"
    monkeypatch.setenv(SCRIPTED_PROVIDER_ENV, str(script))
    return str(script)


# ── 1. Opt-in ABSENT: nothing is registered ──────────────────────────────────


def test_opt_in_absent_registers_no_scripted_type_or_entry():
    registry = get_default_registry()
    _seed_real_type(registry)
    baseline = _registered_types(registry)

    # VACUITY FLOOR: "unchanged" is trivially true of an empty set.
    assert baseline, "baseline type set is empty — the unchanged assertion below would be vacuous"

    assert scripted_provider_enabled() is False
    assert sync_entries_from_config() == 0

    assert _registered_types(registry) == baseline
    assert SCRIPTED_PROVIDER_TYPE not in _registered_types(registry)
    assert [e.name for e in registry.list_entries()] == []

    # capability_of behaves exactly as it does for any unknown type.
    with pytest.raises(ProviderResolutionError):
        registry.capability_of(SCRIPTED_PROVIDER_TYPE)


def test_opt_in_absent_leaves_a_configured_real_provider_untouched():
    """The absent-opt-in path must not perturb the ordinary config sync at all."""
    _write_config([{"name": "Real", "type": _REAL_TYPE, "model": "m", "credential": "real-key"}])
    registry = get_default_registry()
    _seed_real_type(registry)

    assert sync_entries_from_config() == 1
    assert [e.name for e in registry.list_entries()] == ["Real"]
    assert registry.get_entry("Real").credential == "real-key"


# ── 2. Opt-in PRESENT: registered once, idempotent, buildable ────────────────


def test_opt_in_present_registers_the_type_once_and_stays_idempotent(
    opt_in: str, stub_scripted_module: types.ModuleType
):
    registry = get_default_registry()

    assert scripted_provider_enabled() is True
    assert sync_entries_from_config() == 1

    assert SCRIPTED_PROVIDER_TYPE in _registered_types(registry)
    assert registry.capability_of(SCRIPTED_PROVIDER_TYPE) is SCRIPTED_PROVIDER_CAPABILITY
    assert [e.name for e in registry.list_entries()] == [SCRIPTED_PROVIDER_ENTRY_NAME]

    # register_type is strict about duplicates by design, so a second sync would
    # RAISE if the repeat-call tolerance were missing. Prove the claimed idempotence.
    assert sync_entries_from_config() == 1
    assert sync_entries_from_config() == 1
    assert [e.name for e in registry.list_entries()] == [SCRIPTED_PROVIDER_ENTRY_NAME]
    assert len(_registered_types(registry)) == 1


def test_build_returns_the_scripted_provider_with_no_credential(
    opt_in: str, stub_scripted_module: types.ModuleType
):
    registry = get_default_registry()
    sync_entries_from_config()

    entry = registry.get_entry(SCRIPTED_PROVIDER_ENTRY_NAME)
    assert entry.type == SCRIPTED_PROVIDER_TYPE
    assert entry.credential is None

    # No credential, and no credential_store kwarg — the whole point of the fixture.
    provider = registry.build(SCRIPTED_PROVIDER_ENTRY_NAME)
    assert isinstance(provider, _StubScriptedProvider)

    # The model id lives on the ENTRY, not on the built provider, and that is the real
    # contract rather than a limitation. This used to assert
    # `provider.model == SCRIPTED_PROVIDER_MODEL` and that a `model="other-1"` override
    # won — both describing a factory that passed `model=` into the constructor. The real
    # `ScriptedProvider.__init__` takes only `self` (a `script_path` kwarg would be a hole
    # in its env gate), so there was nowhere for either value to go: the reply text comes
    # from the script file the opt-in names, and a model id cannot change it. Asserting the
    # entry keeps the descriptive value is the honest form.
    assert registry.get_entry(SCRIPTED_PROVIDER_ENTRY_NAME).model == SCRIPTED_PROVIDER_MODEL

    # A per-turn model override must therefore be ACCEPTED AND IGNORED — never an error,
    # because the resolver threads one for every provider type.
    assert isinstance(
        registry.build(SCRIPTED_PROVIDER_ENTRY_NAME, model="other-1"), _StubScriptedProvider
    )


def test_the_entry_declares_the_capability_a_chat_turn_resolves_on(
    opt_in: str, stub_scripted_module: types.ModuleType
):
    """Ties the declared set to the predicate real resolution actually applies.

    ``_resolve_from_config_registry`` picks the first entry whose declared
    capabilities contain ``_capability_enum(use_case)``; asserting against that
    function keeps this honest if the mapping ever changes.
    """
    from personalclaw.providers.provider_bridge import _capability_enum

    sync_entries_from_config()
    entry = get_default_registry().get_entry(SCRIPTED_PROVIDER_ENTRY_NAME)

    assert _capability_enum("chat") in entry.declared_capabilities


def test_the_chat_resolver_actually_returns_the_fixture_with_no_credential(
    opt_in: str, stub_scripted_module: types.ModuleType
):
    """One level up from the registry: the production resolver returns it.

    ``_resolve_from_config_registry`` owns the implicit
    "first configured provider declaring the capability" fallback that applies when
    nothing is bound in ``active_models.json`` — which is the browser gate's state —
    and it is four of the five provider-returning paths in
    ``resolve_provider_for_use_case``. Asserting the entry merely *declares* chat
    would leave "and resolution picks it up" untested.
    """
    from personalclaw.providers.provider_bridge import _resolve_from_config_registry

    sync_entries_from_config()

    resolved = _resolve_from_config_registry("chat")
    assert isinstance(resolved, _StubScriptedProvider)
    # The entry carries the descriptive model id; the built fixture takes no arguments at
    # all (see the note in test_build_returns_the_scripted_provider_with_no_credential).
    assert (
        get_default_registry().get_entry(SCRIPTED_PROVIDER_ENTRY_NAME).model
        == SCRIPTED_PROVIDER_MODEL
    )


# ── 3. Anti-bypass: a real type still refuses without a credential ───────────


def test_a_real_type_still_raises_credential_missing_under_the_opt_in(
    opt_in: str, stub_scripted_module: types.ModuleType
):
    """The rail that stops a credential-free fixture becoming an auth hole.

    The exemption must be scoped to the scripted type alone: a real entry keeps its
    ``credential`` verbatim through the sync, and building it still refuses through
    the production credential helper — in the SAME registry state in which the
    fixture builds with none.
    """
    _write_config([{"name": "Real", "type": _REAL_TYPE, "model": "m", "credential": "real-key"}])
    registry = get_default_registry()
    _seed_real_type(registry)

    assert sync_entries_from_config() == 2  # the fixture + the real entry

    # (a) Building the real type still REFUSES. Asserted FIRST, deliberately: an
    # assertion placed after a cheaper one only ever reds via that one, and this is
    # the security-critical claim, so it must be the assertion that bites.
    with pytest.raises(CredentialMissing):
        registry.build("Real")

    # (b) ...because the exemption did not spread into the config-derived entry.
    real = registry.get_entry("Real")
    assert real.credential == "real-key"

    # (c) ...while the fixture, in the same registry, needs none.
    assert registry.get_entry(SCRIPTED_PROVIDER_ENTRY_NAME).credential is None
    assert isinstance(registry.build(SCRIPTED_PROVIDER_ENTRY_NAME), _StubScriptedProvider)


def test_the_exemption_is_a_property_of_the_type_not_of_the_opt_in(
    opt_in: str, stub_scripted_module: types.ModuleType
):
    """A second credentialed entry (no scripted involvement) also still refuses.

    Guards the shape of the mistake: an exemption keyed on "the opt-in is set"
    rather than "this type is scripted" would exempt every entry synced while the
    gate runs.
    """
    _write_config(
        [
            {"name": "RealOne", "type": _REAL_TYPE, "model": "m", "credential": "k1"},
            {"name": "RealTwo", "type": _REAL_TYPE, "model": "m", "credential": "k2"},
        ]
    )
    registry = get_default_registry()
    _seed_real_type(registry)
    sync_entries_from_config()

    for name in ("RealOne", "RealTwo"):
        # The refusal first, for the same reason as above.
        with pytest.raises(CredentialMissing):
            registry.build(name)
        assert registry.get_entry(name).credential


# ── 4. Capabilities are exactly the honest minimum ───────────────────────────


def test_declared_capabilities_are_exactly_the_minimum_set(
    opt_in: str, stub_scripted_module: types.ModuleType
):
    """Equality, not containment — a later widening must red here.

    CHAT because clause 1 is "completes a scripted chat turn"; CODE_TOOLS because
    the fixture's declared job includes tool-call emission. Nothing else: a JSON
    fixture performs none of the rest, and declaring one would make this entry the
    implicit fallback for a use case it cannot serve.
    """
    expected = frozenset({Capability.CHAT, Capability.CODE_TOOLS})

    assert SCRIPTED_PROVIDER_CAPABILITY.capabilities == expected

    sync_entries_from_config()
    entry = get_default_registry().get_entry(SCRIPTED_PROVIDER_ENTRY_NAME)
    assert entry.declared_capabilities == expected

    # The graded/boolean axes agree with the flag set — a fixture replays a string.
    assert SCRIPTED_PROVIDER_CAPABILITY.supports_streaming is False
    assert SCRIPTED_PROVIDER_CAPABILITY.supports_embeddings is False
    assert SCRIPTED_PROVIDER_CAPABILITY.supports_vision is False
    assert SCRIPTED_PROVIDER_CAPABILITY.supports_tools is True
    assert SCRIPTED_PROVIDER_CAPABILITY.type == SCRIPTED_PROVIDER_TYPE


def test_the_omitted_capabilities_are_named_so_the_omission_is_deliberate():
    """Each of these is a capability a scripted JSON fixture cannot perform."""
    for absent in (
        Capability.EMBEDDING,
        Capability.VISION,
        Capability.STREAMING,
        Capability.PLANNING,
        Capability.SUMMARIZATION,
        Capability.TOOL_APPROVAL,
    ):
        assert absent not in SCRIPTED_PROVIDER_CAPABILITY.capabilities


# ── Integration rails: the two defects only the MERGED tree can see ───────────────
#
# Both halves of PHF-7 were built on separate branches against a written contract, and
# `pyproject.toml` sets `ignore_missing_imports = true`, so mypy said NOTHING about either
# mismatch: `make lint` was exit 0 on both branches while the pair was broken. These rails
# turn that into a test failure instead of a TypeError in a running gateway.
#
# They deliberately do NOT use `stub_scripted_module` — a stub is exactly what hid both
# defects, since it accepts any constructor call and needs no script file.


def test_the_registry_and_the_fixture_name_the_SAME_env_var() -> None:
    """One switch for the pair, asserted rather than promised.

    `SCRIPTED_PROVIDER_ENV`'s docstring claims it is "the SAME variable ScriptedProvider
    itself requires". It was not: the two modules independently spelled
    `PERSONALCLAW_SCRIPTED_LLM` and `PERSONALCLAW_SCRIPTED_MODEL_SCRIPT`, so the pair could
    only ever be half-enabled — one variable registers a type whose factory then refuses to
    construct, the other builds nothing because no type is registered. Neither branch's
    suite could fail on its own.
    """
    from personalclaw.llm import registry as R
    from personalclaw.llm import scripted as S

    assert R.SCRIPTED_PROVIDER_ENV == S.SCRIPT_ENV_VAR, (
        "the registration gate and the fixture's own gate name different env vars, so the "
        "pair can be half-enabled and neither half looks broken in isolation"
    )


def test_the_factory_builds_the_REAL_fixture_through_the_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Build for real — a constructor mismatch is invisible to lint.

    The factory called `ScriptedProvider(model=...)` while the fixture's `__init__` takes
    only `self` — deliberately, because a `script_path` kwarg would be a hole in the env
    gate, and its own test pins `inspect.signature` to `["self"]`. That is a TypeError at
    bind time, and `ignore_missing_imports` meant mypy never mentioned it. So this asserts
    the built object, not the factory's source.
    """
    import json

    from personalclaw.llm.registry import (
        SCRIPTED_PROVIDER_ENTRY_NAME,
        get_default_registry,
        register_scripted_provider_type,
    )
    from personalclaw.llm.scripted import ScriptedProvider

    script = tmp_path / "chat-script.json"
    script.write_text(
        json.dumps(
            {
                "version": 1,
                "turns": [
                    {
                        "text": "Hello from the scripted provider.",
                        "stop_reason": "end_turn",
                        "usage": {
                            "input_tokens": 42,
                            "output_tokens": 9,
                            "cache_creation_tokens": 0,
                            "cache_read_tokens": 0,
                        },
                    }
                ],
            }
        )
    )
    monkeypatch.setenv(SCRIPTED_PROVIDER_ENV, str(script))

    assert register_scripted_provider_type() is True
    registry = get_default_registry()
    built = registry.build(SCRIPTED_PROVIDER_ENTRY_NAME)
    assert isinstance(built, ScriptedProvider), type(built)

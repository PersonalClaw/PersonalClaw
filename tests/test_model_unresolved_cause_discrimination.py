"""#3408 — ``ERR_MODEL_UNRESOLVED`` names the cause that actually fired.

``_resolve_from_config_registry`` answers a bare ``None`` for every reason an active ref
cannot be built, and the stale-pin raise used to state ONE of them unconditionally:

    WHY: the active ref names provider 'X', which is absent from config.json
         (its app isn't installed or configured)
    FIX: install 'X' in the App Store, or rebind 'chat' to an available model in
         Settings → Models

For the measured case that motivated the issue — a ``vllm``-typed entry that IS in
config.json and IS in the live registry, whose type simply has no registered factory —
that ``why`` is false and the first half of that ``fix`` is unactionable: ``X`` is the
provider ENTRY name the user typed in Settings, not an app name, so there is nothing
called ``X`` to install in the App Store.

One test per distinguishable cause, each asserting the message names THAT cause and gives
an action that would resolve it. The last one is the deliberate generic: it says it is
unsure instead of asserting a specific wrong cause.

"The model name is not offered by that provider" is NOT a cause here, and that is
measured rather than assumed: ``_resolve_from_config_registry`` threads ``model_override``
to the factory unvalidated (``provider_bridge`` build kwargs), so a wrong model id does
not make it return ``None`` — a factory that rejects one lands in the generic branch,
which is why that branch names the model id.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import (
    ProviderEntry,
    get_default_registry,
    reset_default_registry,
)
from personalclaw.providers.provider_bridge import _diagnose_unbuildable_ref

GHOST = "Ghost Provider"
GHOST_TYPE = "vllm"
MODEL = "ghost-7b"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home + an empty provider registry, per test."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    reset_default_registry()
    yield h
    reset_default_registry()


def _write_config(home, providers: list[dict]) -> None:
    (home / "config.json").write_text(json.dumps({"providers": providers}), encoding="utf-8")


def _ghost_config_row(**extra) -> dict:
    """The exact entry the filed probe used."""
    return {
        "name": GHOST,
        "type": GHOST_TYPE,
        "model": MODEL,
        "options": {"endpoint": "http://127.0.0.1:59999"},
        **extra,
    }


def _register_entry(**extra) -> None:
    get_default_registry().register_entry(
        ProviderEntry(name=GHOST, type=GHOST_TYPE, model=MODEL, **extra)
    )


def _register_type(caps: set[Capability]) -> None:
    get_default_registry().register_type(
        ProviderCapability(
            type=GHOST_TYPE,
            capabilities=frozenset(caps),
            supports_streaming=False,
            supports_tools=False,
            supports_embeddings=Capability.EMBEDDING in caps,
            supports_vision=False,
            max_context_tokens=0,
        ),
        lambda entry=None, session_key=None, **kw: object(),
    )


def _diagnose(use_case: str = "chat", capability: str = "chat") -> tuple[str, str]:
    return _diagnose_unbuildable_ref(GHOST, MODEL, use_case, capability)


# ── Cause: the use case maps to no provider capability at all ────────────────


def test_an_unmappable_use_case_says_so_instead_of_blaming_the_provider(home):
    """``audio_modality`` has no ``Capability`` member, so nothing can ever satisfy it."""
    why, fix = _diagnose_unbuildable_ref(GHOST, MODEL, "audio_modality", "audio_modality")
    assert "maps to no provider capability" in why
    assert "audio_modality" in why
    assert "absent from config.json" not in why
    assert "Settings → Models" in fix


# ── Cause: config.json cannot be read ────────────────────────────────────────


def test_an_unreadable_config_says_unknown_rather_than_absent(home):
    (home / "config.json").write_text("{not json", encoding="utf-8")
    why, fix = _diagnose()
    assert "config.json could not be read" in why
    assert "is unknown" in why, "it must not assert a cause it cannot see"
    assert "repair config.json" in fix


# ── Cause: the entry really is gone ──────────────────────────────────────────


def test_a_genuinely_absent_entry_says_absent_and_says_re_add_it(home):
    """The original sentence — kept, but only for the case in which it is TRUE."""
    _write_config(home, [])
    why, fix = _diagnose()
    assert f"no provider named {GHOST!r} is in config.json" in why
    assert "renamed or removed" in why
    assert f"re-add {GHOST!r} in Settings → Providers" in fix


# ── Cause: in config.json, but never registered in the running gateway ───────


def test_a_configured_but_unregistered_entry_names_the_boot_gap(home):
    """The case the boot-sync rail describes: config.json has it, the registry does not."""
    _write_config(home, [_ghost_config_row()])
    why, fix = _diagnose()
    assert f"provider {GHOST!r} IS in config.json" in why
    assert "not registered in the running gateway" in why
    assert "re-save" in fix and "restart the gateway" in fix


# ── Cause: the entry's TYPE has no registered factory ────────────────────────


def test_a_missing_type_factory_names_the_TYPE_not_the_entry_name(home):
    """THE measured case. ``config.json`` plainly contains the entry; the type has no app.

    The fix must name ``'vllm'`` — the token the Store and ``POST /api/providers`` speak.
    Naming ``'Ghost Provider'`` is the dead end: it is the user's own label for the entry,
    and the App Store has no row for it.
    """
    _write_config(home, [_ghost_config_row()])
    _register_entry()
    why, fix = _diagnose()

    assert f"declares type {GHOST_TYPE!r}" in why
    assert "no installed app registers that type" in why
    assert "absent from config.json" not in why, "the entry is right there"
    assert f"install an app that provides {GHOST_TYPE!r} in the App Store" in fix
    assert f"install {GHOST!r} in the App Store" not in fix, "that names nothing installable"


def test_an_installed_but_disabled_app_says_enable_it(home, monkeypatch):
    """A disabled app never runs ``register_type``, so this looks identical from the
    registry — and the fix is one click, not an install."""
    _install_model_app(monkeypatch, app="vllm-models", enabled=False)
    _write_config(home, [_ghost_config_row()])
    _register_entry()
    why, fix = _diagnose()

    assert "installed but DISABLED" in why
    assert "'vllm-models'" in why
    assert fix == "enable 'vllm-models' on the Apps page"


def test_an_installed_enabled_app_whose_type_never_registered_points_at_the_log(home, monkeypatch):
    """Installed, enabled, and still no factory — the app failed to import."""
    _install_model_app(monkeypatch, app="vllm-models", enabled=True)
    _write_config(home, [_ghost_config_row()])
    _register_entry()
    why, fix = _diagnose()

    assert "is installed and enabled, but the type never registered" in why
    assert "the app failed to load" in why
    assert "import error" in fix


# ── Cause: the entry does not declare the capability the use case needs ──────


def test_a_capability_mismatch_names_the_capability(home):
    _write_config(home, [_ghost_config_row()])
    _register_type({Capability.CHAT})
    _register_entry(declared_capabilities=frozenset({Capability.CHAT}))
    why, fix = _diagnose(use_case="embed", capability="embedding")

    assert f"provider {GHOST!r} (type {GHOST_TYPE!r}) does not declare the 'embedding'" in why
    assert "absent from config.json" not in why
    assert "declares 'embedding'" in fix


# ── Cause: the credential it names has no secret ─────────────────────────────


def test_a_missing_credential_names_the_credential(home):
    _write_config(home, [_ghost_config_row()])
    _register_type({Capability.CHAT})
    _register_entry(declared_capabilities=frozenset({Capability.CHAT}), credential="ghost-api-key")
    why, fix = _diagnose()

    assert "needs credential 'ghost-api-key'" in why
    assert "no secret in the credential store" in why
    assert "set 'ghost-api-key' in Settings → Providers" in fix


def test_a_present_credential_is_not_reported_as_missing(home):
    """The control: with the secret in place, the credential branch must NOT fire."""
    (home / "credentials.json").write_text(
        json.dumps({"ghost-api-key": {"type": "api_key", "value": "shh"}}), encoding="utf-8"
    )
    _write_config(home, [_ghost_config_row()])
    _register_type({Capability.CHAT})
    _register_entry(declared_capabilities=frozenset({Capability.CHAT}), credential="ghost-api-key")
    why, _fix = _diagnose()
    assert "needs credential" not in why, why


# ── The deliberate generic: unsure, and it says so ───────────────────────────


def test_an_indistinguishable_cause_says_it_cannot_see_the_reason(home):
    """Everything checkable is in place. It must NOT assert a specific wrong cause."""
    _write_config(home, [_ghost_config_row()])
    _register_type({Capability.CHAT})
    _register_entry(declared_capabilities=frozenset({Capability.CHAT}))
    why, fix = _diagnose()

    assert "is configured and its type is registered" in why
    assert "not visible from here" in why
    assert "absent from config.json" not in why
    # Actionable anyway: the log line to read and the model id to check.
    assert f"failed to build provider {GHOST}" in fix
    assert repr(MODEL) in fix


# ── The seam: the raise itself uses the diagnosis ────────────────────────────


def test_the_raise_carries_the_derived_cause_not_the_old_unconditional_one(home, monkeypatch):
    """Drive the real raise, in-process, with the filed probe's config + pin.

    A per-cause helper nobody calls would be a dead wire, so this asserts the envelope a
    user actually reads.
    """
    from personalclaw.providers import provider_bridge as pb

    _write_config(home, [_ghost_config_row()])
    _register_entry()  # in config.json AND in the live registry; type has no factory
    monkeypatch.setattr(pb, "_capability_enum", lambda cap: Capability.CHAT)
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.active_model_refs",
        lambda use_case: [f"{GHOST}:{MODEL}"],
    )

    with pytest.raises(pb.ProviderResolutionError) as excinfo:
        pb.resolve_provider_for_use_case("chat")

    err = excinfo.value.agent_error
    assert err is not None and err.code == "ERR_MODEL_UNRESOLVED"
    assert err.what == f"the model pinned for use case 'chat' ('{GHOST}:{MODEL}') cannot be built"
    # The half the issue is about: the WHY is the real cause, not the unconditional one.
    assert f"declares type {GHOST_TYPE!r}" in err.why, err.render()
    assert "absent from config.json" not in err.why, err.render()
    assert f"install an app that provides {GHOST_TYPE!r}" in err.fix, err.render()
    # The plain message is no longer a second, contradicting sentence.
    assert err.fix in str(excinfo.value)


def _install_model_app(monkeypatch, *, app: str, enabled: bool) -> None:
    """Make the extension registry report one installed model app for ``GHOST_TYPE``."""

    class _Cfg:
        type = "model"
        providerType = GHOST_TYPE
        multiInstance = False
        capabilities = ["chat"]

    class _Manifest:
        name = app

    class _Ext:
        def __init__(self) -> None:
            self.name = app
            self.manifest = _Manifest()
            self.provider_config = _Cfg()
            self.enabled = enabled

    class _Reg:
        @staticmethod
        def list_by_type(kind: str):
            return [_Ext()] if kind == "model" else []

    monkeypatch.setattr("personalclaw.providers.registry.get_provider_registry", lambda: _Reg())

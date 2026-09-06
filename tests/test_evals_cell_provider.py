"""The declared provider binding an eval cell may run against.

Both directions are pinned here, and the SECOND one is the security claim:

* a cell WITH a binding resolves a real (non-``scripted``) provider from inside its
  isolated per-cell home — the thing that was structurally impossible before;
* a cell WITHOUT one gets no provider grant at all, and specifically does NOT acquire a
  credential the parent process happened to be carrying. That is the whole point of
  building the child env from an allowlist instead of copying ``os.environ``.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.evals import cell_provider
from personalclaw.evals import overlay as overlay_lib
from personalclaw.evals import runner as runner_mod
from personalclaw.evals.matrix import MatrixSpec
from personalclaw.evals.runner import run_matrix
from personalclaw.llm import registry as registry_lib
from personalclaw.llm.capabilities import Capability
from tests.test_evals_matrix_runner import _FakeRun, write_pinnable_home  # noqa: F401

AMBIENT_KEY = "OPENAI_API_KEY"


@pytest.fixture()
def bound_home(tmp_path, monkeypatch):
    """An isolated home that configures ONE provider and can pin a run against it."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    write_pinnable_home(tmp_path, model="LocalRuntime:test-model")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "name": "LocalRuntime",
                        "type": "openai_compatible",
                        "model": "test-model",
                        "options": {"base_url": "http://127.0.0.1:9/v1"},
                    },
                    {"name": "Other", "type": "openai_compatible", "model": "other-model"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def clean_registry():
    """Isolate the process-wide provider registry — a cell registers a type into it."""
    registry_lib.reset_default_registry()
    yield
    registry_lib.reset_default_registry()


def _spawn(monkeypatch, *, binding=None, subject="s"):
    """Run one cell through a faked ``subprocess.run`` and return the child env it got."""
    fake = _FakeRun(behavior=lambda i: ("ok", {"ok": True, "passed": True, "score": 1.0}))
    monkeypatch.setattr(runner_mod.subprocess, "run", fake)
    run_matrix(
        MatrixSpec(subject=subject, axes={}, trial_count=1),
        matrix_id=f"m-{subject}-{'bound' if binding else 'plain'}",
        provider_binding=binding,
    )
    assert len(fake.calls) == 1
    return fake.calls[0]["env"]


# ── the security claim: nothing crosses that was not declared ────────────────


def test_ambient_credential_does_not_reach_an_unbound_cell(bound_home, monkeypatch):
    monkeypatch.setenv(AMBIENT_KEY, "sk-ambient-must-not-cross")
    env = _spawn(monkeypatch)
    assert AMBIENT_KEY not in env
    assert "sk-ambient-must-not-cross" not in json.dumps(env)
    # And no binding / no forwarded secret either.
    assert cell_provider.BINDING_ENV not in env
    assert cell_provider.CELL_KEY_ENV not in env


def test_ambient_credential_does_not_reach_a_bound_cell_either(bound_home, monkeypatch):
    """A binding grants the model it NAMES — not everything else in the parent env."""
    monkeypatch.setenv(AMBIENT_KEY, "sk-ambient-must-not-cross")
    binding = cell_provider.resolve_binding("LocalRuntime:test-model")
    env = _spawn(monkeypatch, binding=binding)
    assert AMBIENT_KEY not in env
    assert cell_provider.BINDING_ENV in env


def test_unbound_cell_still_gets_the_scripted_optin(bound_home, monkeypatch, tmp_path):
    """The offline default keeps working: the replay-script PATH is forwarded by name."""
    script = tmp_path / "replay.json"
    script.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(registry_lib.SCRIPTED_PROVIDER_ENV, str(script))
    env = _spawn(monkeypatch)
    assert env[registry_lib.SCRIPTED_PROVIDER_ENV] == str(script)


def test_parent_env_never_mutated_by_a_binding(bound_home, monkeypatch):
    import os

    binding = cell_provider.resolve_binding("LocalRuntime:test-model")
    before = dict(os.environ)
    _spawn(monkeypatch, binding=binding)
    assert dict(os.environ) == before


# ── the declared grant crosses, and only by name ─────────────────────────────


def test_bound_cell_env_carries_the_binding_and_no_secret(bound_home, monkeypatch):
    binding = cell_provider.resolve_binding("LocalRuntime:test-model")
    env = _spawn(monkeypatch, binding=binding)
    decoded = cell_provider.decode(env[cell_provider.BINDING_ENV])
    assert decoded is not None
    assert decoded.model_ref() == "LocalRuntime:test-model"
    assert decoded.use_case == "chat"
    # No key was named, so none was forwarded.
    assert cell_provider.CELL_KEY_ENV not in env


def test_a_named_key_variable_is_forwarded_and_only_that_one(monkeypatch):
    binding = cell_provider.CellProviderBinding(
        use_case="chat",
        provider_name="Cloudy",
        model="m1",
        api_key_env="CLOUDY_API_KEY",
    )
    source = {"CLOUDY_API_KEY": "sk-declared", AMBIENT_KEY: "sk-ambient"}
    env = cell_provider.spawn_env_for({}, binding, source=source)
    assert env[cell_provider.CELL_KEY_ENV] == "sk-declared"
    assert AMBIENT_KEY not in env
    # Under a CELL-SCOPED name — the provider's own variable is never set in the child.
    assert "CLOUDY_API_KEY" not in env


def test_a_named_key_that_is_unset_forwards_nothing(monkeypatch):
    binding = cell_provider.CellProviderBinding(
        use_case="chat", provider_name="Cloudy", model="m1", api_key_env="CLOUDY_API_KEY"
    )
    env = cell_provider.spawn_env_for({}, binding, source={})
    assert cell_provider.CELL_KEY_ENV not in env


def test_no_binding_adds_nothing_to_the_env():
    assert cell_provider.spawn_env_for({"A": "b"}, None) == {"A": "b"}


# ── resolving a binding out of the invoking home ─────────────────────────────


def test_resolve_binding_reads_only_the_named_entry(bound_home):
    binding = cell_provider.resolve_binding("LocalRuntime:test-model")
    assert binding.provider_name == "LocalRuntime"
    assert binding.model == "test-model"
    assert binding.base_url == "http://127.0.0.1:9/v1"
    # The other configured provider contributed nothing.
    assert "Other" not in json.dumps(binding.to_dict())


def test_resolve_binding_keeps_a_colon_in_the_model_id(bound_home):
    """A model id may contain a colon; only the FIRST one separates the provider."""
    (bound_home / "config.json").write_text(
        json.dumps({"providers": [{"name": "LocalRuntime", "type": "openai_compatible"}]}),
        encoding="utf-8",
    )
    binding = cell_provider.resolve_binding("LocalRuntime:gemma4:12b")
    assert binding.model == "gemma4:12b"


def test_resolve_binding_refuses_an_unconfigured_provider(bound_home):
    with pytest.raises(cell_provider.CellBindingError):
        cell_provider.resolve_binding("Nope:m1")


def test_resolve_binding_refuses_an_unqualified_ref(bound_home):
    with pytest.raises(cell_provider.CellBindingError):
        cell_provider.resolve_binding("bare-model-id")


def test_binding_round_trips_through_its_dict():
    binding = cell_provider.CellProviderBinding(
        use_case="code_tools",
        provider_name="P",
        model="m",
        protocol="anthropic",
        base_url="http://h/v1",
        api_key_env="K",
        max_tokens=4096,
    )
    assert cell_provider.CellProviderBinding.from_dict(binding.to_dict()) == binding
    assert cell_provider.decode(cell_provider.encode(binding)) == binding


def test_binding_rejects_an_unknown_use_case():
    with pytest.raises(ValueError):
        cell_provider.CellProviderBinding(use_case="telepathy", provider_name="P", model="m")


def test_binding_rejects_an_unknown_protocol():
    with pytest.raises(ValueError):
        cell_provider.CellProviderBinding(
            use_case="chat", provider_name="P", model="m", protocol="grpc"
        )


def test_garbage_in_the_env_leaves_the_cell_unbound():
    assert cell_provider.from_env({cell_provider.BINDING_ENV: "not json"}) is None
    assert cell_provider.from_env({}) is None


# ── the child side ───────────────────────────────────────────────────────────


def test_apply_in_child_without_a_binding_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    assert cell_provider.apply_in_child(None) == []
    assert not (tmp_path / "config.json").exists()
    assert not (tmp_path / "active_models.json").exists()


def test_apply_in_child_refuses_the_operators_real_home(monkeypatch):
    """The same rail the overlay and the gate arm stage behind — a `providers[]` entry
    written into the real home is a permanent edit by a run that promised to touch
    nothing."""
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    binding = cell_provider.CellProviderBinding(use_case="chat", provider_name="P", model="m")
    with pytest.raises(cell_provider.CellBindingError):
        cell_provider.apply_in_child(binding)


def test_apply_in_child_binds_one_use_case_and_one_entry(tmp_path, monkeypatch, clean_registry):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    binding = cell_provider.CellProviderBinding(
        use_case="chat",
        provider_name="LocalRuntime",
        model="test-model",
        base_url="http://127.0.0.1:9/v1",
    )
    changed = cell_provider.apply_in_child(binding)
    assert changed  # it reports WHAT it did

    config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert [p["name"] for p in config["providers"]] == ["LocalRuntime"]
    assert config["providers"][0]["type"] == cell_provider.CELL_PROVIDER_TYPE

    active = json.loads((tmp_path / "active_models.json").read_text(encoding="utf-8"))
    assert active == {"chat": ["LocalRuntime:test-model"]}


def _resolve_cell_model():
    """The MODEL a cell's chat turn would infer through.

    ``create_provider_factory()`` answers with the native agent runtime, which wraps the
    model it resolves — so asking it what it built says nothing about which model a cell
    can reach. ``_force_model_axis`` is how the native builder resolves that inner model,
    and it is the axis this whole seam is about.
    """
    from personalclaw.providers.provider_bridge import resolve_provider_for_use_case

    return resolve_provider_for_use_case("chat", _force_model_axis=True)


def _apply_chat_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.delenv(registry_lib.SCRIPTED_PROVIDER_ENV, raising=False)
    binding = cell_provider.CellProviderBinding(
        use_case="chat",
        provider_name="LocalRuntime",
        model="test-model",
        base_url="http://127.0.0.1:9/v1",
    )
    cell_provider.apply_in_child(binding)
    return binding


def test_a_bound_cell_has_a_registered_type_and_a_capable_entry(
    tmp_path, monkeypatch, clean_registry
):
    """The done-when, stated as the two causes it closes — and with no optional SDK in the
    way, because the protocol clients are extras and a cell must still be provably BOUND on
    an install that has neither.

    Cause 2 was that the type is registered by nobody in a cell home, so the synced entry
    carried an EMPTY capability set and was therefore unmatchable. Both halves are asserted:
    the type exists in this process's registry, and the entry the ref names declares chat.
    """
    binding = _apply_chat_binding(tmp_path, monkeypatch)
    registry = registry_lib.get_default_registry()

    assert registry.capability_of(cell_provider.CELL_PROVIDER_TYPE).capabilities
    entry = registry.get_entry(binding.provider_name)
    assert entry.type == cell_provider.CELL_PROVIDER_TYPE
    assert Capability.CHAT in entry.declared_capabilities
    assert entry.model == "test-model"


def test_a_bound_cell_resolves_a_real_provider_for_chat(tmp_path, monkeypatch, clean_registry):
    """The done-when end to end: after the child-side apply, the ordinary resolution path
    builds a real protocol client — not the scripted replay.

    Skipped where the OpenAI-compatible wire SDK is not installed: it is a declared extra,
    and a provider whose SDK is absent is unbuildable for reasons that have nothing to do
    with this binding. The sibling test above pins the binding itself without it.
    """
    pytest.importorskip("openai", reason="the OpenAI-compatible protocol client is an extra")
    _apply_chat_binding(tmp_path, monkeypatch)
    assert type(_resolve_cell_model()).__name__ == "OpenAIProvider"


def test_an_unbound_cell_cannot_resolve_any_provider(tmp_path, monkeypatch, clean_registry):
    """The negative half: with no binding applied, the same call has nothing to build."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.delenv(registry_lib.SCRIPTED_PROVIDER_ENV, raising=False)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")

    from personalclaw.providers.provider_bridge import ProviderResolutionError

    with pytest.raises(ProviderResolutionError):
        _resolve_cell_model()


def test_an_unbound_cell_resolves_the_scripted_fixture(tmp_path, monkeypatch, clean_registry):
    """`scripted` stays the default: with only the offline opt-in set, that is what a cell
    gets — and it is all it gets."""
    script = tmp_path / "replay.json"
    script.write_text(
        json.dumps({"version": 1, "turns": [{"text": "hi", "stop_reason": "end_turn"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv(registry_lib.SCRIPTED_PROVIDER_ENV, str(script))
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    registry_lib.sync_entries_from_config()

    assert type(_resolve_cell_model()).__name__ == "ScriptedProvider"


# ── the grant is narrow ──────────────────────────────────────────────────────


def test_a_chat_binding_declares_no_embedding_or_vision_capability():
    caps = cell_provider.cell_capabilities("chat")
    assert Capability.CHAT in caps
    # CODE_TOOLS is present so the native loop offers tool schemas at all.
    assert Capability.CODE_TOOLS in caps
    assert Capability.EMBEDDING not in caps
    assert Capability.VISION not in caps


def test_a_chat_subcategory_borrows_the_chat_capability():
    assert cell_provider.cell_capabilities("code_tools") == cell_provider.cell_capabilities("chat")


def test_a_use_case_with_no_provider_capability_is_refused():
    with pytest.raises(cell_provider.CellBindingError):
        cell_provider.cell_capabilities("audio_modality")


# ── the descriptor says which kind of run this was ───────────────────────────


def test_the_cell_descriptor_records_the_binding(bound_home, monkeypatch):
    binding = cell_provider.resolve_binding("LocalRuntime:test-model")
    _spawn(monkeypatch, binding=binding)
    from personalclaw.evals import store

    descriptor = json.loads(
        (store.matrix_dir("m-s-bound") / "cell-0000" / "descriptor.json").read_text(
            encoding="utf-8"
        )
    )
    assert descriptor["provider_binding"]["provider_name"] == "LocalRuntime"
    assert descriptor["provider_binding"]["use_case"] == "chat"


def test_an_unbound_cell_descriptor_says_so(bound_home, monkeypatch):
    _spawn(monkeypatch)
    from personalclaw.evals import store

    descriptor = json.loads(
        (store.matrix_dir("m-s-plain") / "cell-0000" / "descriptor.json").read_text(
            encoding="utf-8"
        )
    )
    assert "provider_binding" not in descriptor


def test_the_overlay_config_patcher_is_still_the_only_config_writer(tmp_path, monkeypatch):
    """One answer to "how does a cell edit its own config" — the binding reuses it."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    assert overlay_lib.patch_child_config("providers", []) == "config.json:providers=[]"

"""E3-P1 / P4b: agent-scoped lifecycle-trigger resolution chain.

F5 shipped the store primitive (``ScriptHookStore.fire_for_ids`` — covered by
``test_fire_for_ids.py``). P1 wires the *agent* side: an ``AgentProfile`` carries
referenced lifecycle-trigger IDs, ``resolve_agent_bindings`` surfaces them on
``ResolvedBindings.triggers``, and the chat runner fires only those. These tests
pin the resolution chain so the "never global" invariant holds end-to-end: the
IDs the runner will pass to ``fire_for_ids`` come from exactly this agent's
profile. (The field was renamed hooks→triggers in P4b.)
"""

from __future__ import annotations

from personalclaw.config.loader import AgentProfile, AppConfig, resolve_agent_bindings


def _cfg_with_agent(name: str, triggers: list[str]) -> AppConfig:
    cfg = AppConfig()
    cfg.agents = {name: AgentProfile(triggers=triggers)}
    cfg.default_agent = name
    return cfg


def test_profile_triggers_default_empty():
    # The seeded/default agent ships zero triggers (the "default fires nothing" rule).
    assert AgentProfile().triggers == []


def test_resolve_surfaces_agent_triggers():
    cfg = _cfg_with_agent("coder", ["trig-a", "trig-b"])
    bindings = resolve_agent_bindings(cfg, "coder")
    assert bindings.triggers == ["trig-a", "trig-b"]


def test_resolve_empty_when_agent_has_no_triggers():
    cfg = _cfg_with_agent("plain", [])
    assert resolve_agent_bindings(cfg, "plain").triggers == []


def test_resolve_is_per_agent_not_global():
    # Two agents, different trigger sets — resolution returns THIS agent's set only,
    # never a union (the structural "no global firing" guarantee).
    cfg = AppConfig()
    cfg.agents = {
        "a": AgentProfile(triggers=["only-a"]),
        "b": AgentProfile(triggers=["only-b"]),
    }
    cfg.default_agent = "a"
    assert resolve_agent_bindings(cfg, "a").triggers == ["only-a"]
    assert resolve_agent_bindings(cfg, "b").triggers == ["only-b"]


def test_resolve_unknown_agent_falls_back_to_default_triggers():
    # An unknown agent name resolves to the default agent's bindings (and its
    # trigger set), never a phantom — so firing stays scoped to a real profile.
    cfg = _cfg_with_agent("default-one", ["d1"])
    assert resolve_agent_bindings(cfg, "nonexistent").triggers == ["d1"]


def test_triggers_survive_save_load_roundtrip(tmp_path, monkeypatch):
    """Regression: AgentProfile.triggers must round-trip through config.json.

    The load path reconstructs AgentProfile from an explicit key allowlist; if
    ``triggers`` is omitted there, the attach UI saves but firing reads [] forever
    (caught live, not by direct-construction unit tests). Save → reload → fired
    set must be preserved.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    cfg = AppConfig.load()
    cfg.agents = {"coder": AgentProfile(triggers=["trig-x", "trig-y"])}
    cfg.default_agent = "coder"
    cfg.save()

    reloaded = AppConfig.load()
    assert reloaded.agents["coder"].triggers == ["trig-x", "trig-y"]
    # And the firing path reads the persisted set.
    assert resolve_agent_bindings(reloaded, "coder").triggers == ["trig-x", "trig-y"]


def test_legacy_hooks_key_migrates_to_triggers(tmp_path, monkeypatch):
    """A pre-P4b config.json with the old ``hooks`` agent key still loads its
    scoped triggers (migrate-on-read; the write side only emits ``triggers``)."""
    import json

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.config.loader import config_dir

    cfg_path = config_dir() / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"agents": {"coder": {"hooks": ["legacy-a"]}}}), encoding="utf-8")

    reloaded = AppConfig.load()
    assert reloaded.agents["coder"].triggers == ["legacy-a"]

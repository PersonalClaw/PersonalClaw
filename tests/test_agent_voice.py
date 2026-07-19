"""Agent soul/voice layer (#42) — separate persona, injected high-priority."""

from __future__ import annotations

from personalclaw.agents.marketplace import AgentDefinition
from personalclaw.config.loader import _compose_voice


# ── compose: voice goes BEFORE the operating rules ──


def test_compose_prepends_voice():
    out = _compose_voice("Be blunt.", "Follow the rules.")
    assert out.index("Be blunt.") < out.index("Follow the rules.")
    assert "VOICE" in out


def test_compose_empty_voice_is_prompt_asis():
    assert _compose_voice("", "rules") == "rules"
    assert _compose_voice("   ", "rules") == "rules"


def test_compose_empty_prompt_with_voice():
    out = _compose_voice("Witty.", "")
    assert "VOICE" in out and "Witty." in out  # the voice header + the persona


# ── marketplace AgentDefinition round-trip (S6 loader-allowlist gotcha) ──


def test_marketplace_voice_round_trips():
    d = AgentDefinition(name="bot", voice="sardonic, terse", system_prompt="do x")
    d2 = AgentDefinition.from_dict(d.to_dict())
    assert d2.voice == "sardonic, terse"


def test_marketplace_voice_default_empty():
    d = AgentDefinition.from_dict({"name": "bot"})
    assert d.voice == ""


# ── config AgentProfile round-trip via disk ──


def test_config_profile_voice_round_trips(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.config.loader import AppConfig, config_path

    config_path().write_text(json.dumps({"agents": {"bot": {"voice": "dry wit", "system_prompt": "rules"}}}))
    cfg = AppConfig.load()
    assert cfg.agents["bot"].voice == "dry wit"
    # survives a save round-trip (asdict serialization emits voice)
    assert cfg.to_dict()["agents"]["bot"]["voice"] == "dry wit"

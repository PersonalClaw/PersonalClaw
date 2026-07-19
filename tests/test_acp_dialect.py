"""P1: ACPDialect strategy — per-CLI protocol divergences.

Core AcpClient stays vendor-neutral by delegating the handshake/permission
divergences to a dialect. These pin each dialect's concrete choices + the
selection registry. The byte-identical-default guarantee (that extracting the
dialect didn't change legacy behaviour) is covered by test_acp_client.py +
test_acp_set_mode_all_agents.py, which still pass against DefaultDialect.
"""

from __future__ import annotations

from personalclaw.acp.dialect import (
    ClaudeCodeDialect,
    CodexDialect,
    DefaultDialect,
    DiscoveryResult,
    ZedAdapterDialect,
    get_dialect,
)


def test_default_dialect_shape():
    d = DefaultDialect()
    assert d.protocol_version() == "2025-08-22"  # date-string
    act = d.activate_agent_request(session_id="s", agent="MyAgent")
    assert act is not None and act.method == "session/set_mode"
    assert act.params == {"sessionId": "s", "modeId": "MyAgent"}
    # Empty agent → no activation message: ACP has no global default agent, so
    # an unselected agent must NOT fabricate a modeId (the agent would reject it).
    assert d.activate_agent_request(session_id="s", agent="") is None
    sm = d.set_model_request(session_id="s", model="glm-5.1", default_model="auto")
    assert sm is not None and sm.method == "session/set_model"
    assert sm.params == {"sessionId": "s", "modelId": "glm-5.1"}
    # auto/default model → no set_model (legacy skip)
    assert d.set_model_request(session_id="s", model="auto", default_model="auto") is None


def test_default_permission_options_id_label():
    d = DefaultDialect()
    parsed = d.parse_permission_options([{"id": "allow_once", "label": "Allow once", "kind": "allow_once"}])
    assert parsed == [{"id": "allow_once", "label": "Allow once", "kind": "allow_once"}]
    assert d.approve_outcome("allow_once") == {
        "outcome": {"outcome": "selected", "optionId": "allow_once"}
    }
    assert d.reject_outcome() == {"outcome": {"outcome": "cancelled"}}


def test_select_allow_option_id_echoes_agent_defined_id():
    """Regression (fs_write denial): the agent's optionId is agent-defined and
    NOT assumed to be the literal ``allow_once``. approve must echo the id the
    agent offered, selected via the spec ``kind`` classifier."""
    d = DefaultDialect()
    # claude-code-acp-style: optionId differs from the well-known constant.
    offered = [
        {"id": "allow", "label": "Allow once", "kind": "allow_once"},
        {"id": "allow_all", "label": "Allow always", "kind": "allow_always"},
        {"id": "no", "label": "Reject", "kind": "reject_once"},
    ]
    assert d.select_allow_option_id(offered) == "allow"  # the once option's id
    assert d.select_allow_option_id(offered, prefer_always=True) == "allow_all"
    # No options captured → empty (caller falls back to default).
    assert d.select_allow_option_id([]) == ""
    # Only a reject option offered → no allow id, falls through to "".
    assert d.select_allow_option_id([{"id": "no", "kind": "reject_once"}]) == ""


def test_zed_dialect_int_protocol_no_setmode_setconfig():
    d = ZedAdapterDialect()
    assert d.protocol_version() == 1  # int, not date-string
    # No set_mode — the adapter binds the agent at spawn.
    assert d.activate_agent_request(session_id="s", agent="A") is None
    # Model via set_config_option, not set_model.
    sm = d.set_model_request(session_id="s", model="opus-4.8", default_model="auto")
    assert sm is not None and sm.method == "session/set_config_option"
    assert sm.params == {"sessionId": "s", "configId": "model", "value": "opus-4.8"}


def test_default_dialect_has_no_separate_mode_axis():
    """The default dialect's set_mode IS agent activation — there is no separate
    permission-mode axis, so set_mode_request returns None and the client skips the step."""
    d = DefaultDialect()
    assert d.set_mode_request(session_id="s", mode="plan") is None
    assert d.set_mode_request(session_id="s", mode="") is None


def test_zed_dialect_set_mode_via_set_config_option():
    """Zed adapters (claude/codex) expose the permission mode as a separate axis
    set via session/set_config_option configId=mode. Empty mode → None."""
    d = ZedAdapterDialect()
    req = d.set_mode_request(session_id="s", mode="plan")
    assert req is not None and req.method == "session/set_config_option"
    assert req.params == {"sessionId": "s", "configId": "mode", "value": "plan"}
    # Empty mode keeps the adapter default (no message).
    assert d.set_mode_request(session_id="s", mode="") is None
    # Subclasses inherit the behaviour.
    assert ClaudeCodeDialect().set_mode_request(session_id="s", mode="acceptEdits") is not None
    assert CodexDialect().set_mode_request(session_id="s", mode="default") is not None


def test_default_dialect_normalize_discovery_modes_are_agents():
    """Default-dialect shape: availableModes ARE agents (provider_agent=modeId),
    availableModels are model overrides, no separate permission-mode axis."""
    d = DefaultDialect()
    snew = {
        "modes": {"availableModes": [
            {"id": "gpu-dev", "name": "gpu-dev", "description": "Dev agent"},
            {"id": "planner", "name": "Planner", "description": "Plans"},
            {"name": "no-id-skipped"},  # dropped — no id
        ]},
        "models": {"availableModels": [
            {"modelId": "auto", "name": "auto"},
            {"modelId": "glm-5", "name": "glm-5"},
        ]},
    }
    r = d.normalize_discovery(snew)
    assert isinstance(r, DiscoveryResult)
    assert [a["id"] for a in r.agents] == ["gpu-dev", "planner"]
    assert r.agents[0]["provider_agent"] == "gpu-dev"
    assert r.agents[0]["use_runtime_prefix"] is False
    assert r.models == ["auto", "glm-5"]
    assert r.permission_modes == []  # the default dialect has no separate mode axis


def test_zed_dialect_normalize_discovery_effort_as_setting():
    """Zed (claude) shape: configOptions.effort → supported_efforts (verbatim
    values, per-turn SETTING — NOT separate agents), configOptions.model →
    models, configOptions.mode → permission modes. Exactly ONE base agent."""
    d = ClaudeCodeDialect()
    snew = {
        "modes": {"availableModes": [{"id": "default"}, {"id": "plan"}]},  # ignored as agents
        "configOptions": [
            {"id": "mode", "options": [
                {"value": "default"}, {"value": "acceptEdits"}, {"value": "plan"},
                {"value": "dontAsk"}, {"value": "bypassPermissions"},
            ]},
            {"id": "model", "options": [
                {"value": "default"}, {"value": "opus"}, {"value": "sonnet"},
            ]},
            {"id": "effort", "options": [
                {"value": "default", "name": "Default"},
                {"value": "low", "name": "Low"},
                {"value": "high", "name": "High"},
                {"value": "max", "name": "Max"},
            ]},
        ],
    }
    r = d.normalize_discovery(snew)
    # Exactly ONE base agent — effort is no longer exploded into agents.
    assert len(r.agents) == 1
    assert r.agents[0]["id"] == "" and r.agents[0]["reasoning_effort"] == ""
    # Non-default effort levels surface VERBATIM as supported_efforts (value+label).
    assert r.supported_efforts == [
        {"value": "low", "label": "Low"},
        {"value": "high", "label": "High"},
        {"value": "max", "label": "Max"},
    ]
    assert r.models == ["default", "opus", "sonnet"]
    assert r.permission_modes == ["default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"]


def test_zed_dialect_normalize_discovery_no_effort_still_has_base_agent():
    """A Zed backend with no effort axis still surfaces one base agent so the
    runtime is selectable."""
    d = CodexDialect()
    r = d.normalize_discovery({"configOptions": [{"id": "model", "options": [{"value": "gpt-5"}]}]})
    assert len(r.agents) == 1 and r.agents[0]["id"] == "" and r.agents[0]["use_runtime_prefix"]
    assert r.models == ["gpt-5"]


def test_zed_dialect_permission_optionId_name():
    d = ZedAdapterDialect()
    # Public ACP spec keys.
    parsed = d.parse_permission_options([{"optionId": "allow", "name": "Allow", "kind": "allow_once"}])
    assert parsed == [{"id": "allow", "label": "Allow", "kind": "allow_once"}]
    # Tolerates the id/label shape too (forward-compat).
    parsed2 = d.parse_permission_options([{"id": "x", "label": "Y"}])
    assert parsed2 == [{"id": "x", "label": "Y", "kind": ""}]
    # Options with no id are dropped.
    assert d.parse_permission_options([{"name": "no id"}]) == []


def test_claude_and_codex_are_zed_with_distinct_child_names():
    assert isinstance(ClaudeCodeDialect(), ZedAdapterDialect)
    assert isinstance(CodexDialect(), ZedAdapterDialect)
    assert ClaudeCodeDialect().child_process_names() == ("claude",)
    assert CodexDialect().child_process_names() == ("codex",)
    assert ClaudeCodeDialect().name == "claude"
    assert CodexDialect().name == "codex"


def test_get_dialect_registry():
    assert get_dialect("default").name == "default"
    assert get_dialect("unknown-cli").name == "default"  # unknown id → default fallback
    assert get_dialect("claude-code").name == "claude"
    assert get_dialect("codex").name == "codex"
    # Unknown / empty / None → default (never raises).
    assert get_dialect("nonexistent").name == "default"
    assert get_dialect("").name == "default"
    assert get_dialect(None).name == "default"


def test_set_model_explicit_switch_always_sends():
    """The live set_model path passes a sentinel default so an explicit switch
    is never suppressed — verify each dialect still produces its model verb."""
    assert DefaultDialect().set_model_request(session_id="s", model="m", default_model="\x00").method == "session/set_model"
    assert ZedAdapterDialect().set_model_request(session_id="s", model="m", default_model="\x00").method == "session/set_config_option"

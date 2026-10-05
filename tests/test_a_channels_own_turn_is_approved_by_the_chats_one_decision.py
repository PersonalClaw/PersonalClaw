"""Every call a channel's own turn asks about is answered by the chat's one decision.

A channel app that runs a conversation itself (Slack's threads) asks PersonalClaw, at each call its
turn's runtime asks about, who approves the call without asking anyone (``chat_grant``). That is the
decision PersonalClaw's own chat makes for a call put to its gate: an operator's pattern in the hook
settings, what the call's tool declares, and the chat's Trust, Trust reads and YOLO. Each grant is
held to the same two rules: no grant answers a call that reaches a host off the allowed hosts, and
the operator ceiling bounds every one. Nothing approves a call the hook chain refuses.

The channel used to answer three kinds of call itself before asking: one an operator's pattern
named, one that starts a subagent while the spawn setting was on, and every call of a turn run
"auto". Neither rule reached any of them, so a ceiling saying every call asks a person, or a host
off the allowed hosts, approved nothing less there.

The dashboard state, the hook settings, the allowed hosts and the operator ceiling are real, in a
scratch home; only the audit sink is a double where a row is read.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from chat_test_helpers import _make_state, links_kept_in_a_session_map

from personalclaw import session_restrictions, trust_mode
from personalclaw.guardrails import ceiling as C
from personalclaw.inbox_providers import native_source
from personalclaw.llm_helpers import save_conversation_turn
from personalclaw.sdk.channel import answer_in_chat, chat_grant

#: A conversation the channel runs itself, keyed as it keys it (its thread), and the channel.
THREAD = "1700000200.000100"
CHANNEL = "chatapp"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.dashboard.state as st
    import personalclaw.session_workspace as ws

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(st, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)
    C.reset_ceiling()
    yield tmp_path
    C.reset_ceiling()
    trust_mode.disable_yolo()
    session_restrictions.clear(THREAD)


@pytest.fixture
def state(tmp_path):
    """The gateway's dashboard state, as the channel's own turn reaches it: the channel has linked
    the conversation to its thread, as it does before it runs a turn of one."""
    state = _make_state(tmp_path)
    state.push_sessions_update = MagicMock()
    links_kept_in_a_session_map(state.sessions).set_channel_link(THREAD, THREAD, "D0CHAT")
    before = native_source.get_dashboard_state()
    native_source.set_dashboard_state(state)
    yield state
    native_source.set_dashboard_state(before)


@pytest.fixture
def settings(tmp_path):
    """Write the owner's settings as they are saved: ``settings(patterns=[...], hosts=[...])``."""

    def write(*, patterns: tuple[str, ...] = (), hosts: tuple[str, ...] = (), spawns: bool = False):
        hooks: dict[str, Any] = {"auto_approve_tools": list(patterns)}
        if spawns:
            hooks["auto_approve_subagent_spawn"] = True
        data = {"hooks": hooks, "security": {"egress": {"allow_hosts": list(hosts)}}}
        (tmp_path / "config.json").write_text(json.dumps(data))

    return write


@pytest.fixture
def ceiling(tmp_path, monkeypatch):
    """Install an operator ceiling for this test: ``ceiling("ask")``, ``ceiling("hook_based")``."""

    def install(value: str) -> None:
        path = tmp_path / "operator" / "ceiling.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "scopes": {"approval": {"value": value}}}))
        monkeypatch.setenv(C.CEILING_PATH_ENV, str(path))
        C.reset_ceiling()

    return install


def _call(command: str = "", *, tool: str = "write_file", **declared: Any) -> SimpleNamespace:
    """A call the channel's own turn asks about, as its runtime hands it over."""
    tool_input = json.dumps({"command": command} if command else {"path": "notes.md"})
    return SimpleNamespace(
        title="bash" if command else tool,
        tool_kind="",
        risk_level=declared.get("risk_level", ""),
        work_asks=declared.get("work_asks", False),
        tool_input=tool_input,
        tool_purpose="tidy the notes",
        request_id="req-1",
        tool_meta={},
    )


def _refused(sel: MagicMock) -> list[str]:
    """The grants each ``approval.grant_refused`` row the audit sink was given names."""
    rows = [
        c.kwargs
        for c in sel.return_value.log_api_access.call_args_list
        if c.kwargs.get("operation") == "approval.grant_refused"
    ]
    return [str(r["resources"]).split(",")[0].removeprefix("grant=") for r in rows]


# ── An operator's pattern ─────────────────────────────────────────────────────────────────


def test_an_operators_pattern_answers_the_call_it_names_as_the_settings_read_now(state, settings):
    settings(patterns=("write_file",))
    assert chat_grant(THREAD, _call()) == "hook_pattern"
    assert chat_grant(THREAD, _call(tool="edit_file")) == "", "a call no pattern names is asked"

    settings()
    assert chat_grant(THREAD, _call()) == "", "a pattern taken off the settings still answered"


def test_a_ceiling_that_lets_a_hook_decide_keeps_the_pattern_and_one_that_asks_refuses_it(
    state, settings, ceiling
):
    settings(patterns=("write_file",))
    ceiling("hook_based")
    assert chat_grant(THREAD, _call()) == "hook_pattern"

    ceiling("ask")
    with patch("personalclaw.sel.sel") as audit:
        assert chat_grant(THREAD, _call()) == ""
    assert _refused(audit) == ["hook_pattern"], "the refused grant is audited, naming it"


def test_no_pattern_answers_a_call_to_a_host_off_the_allowed_hosts(state, settings):
    settings(patterns=("bash",), hosts=("docs.example.com",))
    assert chat_grant(THREAD, _call("curl https://docs.example.com/notes")) == "hook_pattern"
    assert chat_grant(THREAD, _call("curl https://example.com/notes")) == ""


def test_nothing_approves_a_call_the_hook_chain_refuses(state, settings):
    """The deny is read on the command that would run, which a call's title need not carry: the
    chat's Trust, YOLO and a pattern naming the call answer nothing for a command it refuses."""
    assert answer_in_chat(THREAD, "trust", channel=CHANNEL) is True
    assert chat_grant(THREAD, _call("ls build")) == "trust", "the control: the Trust answers"
    assert chat_grant(THREAD, _call("mkfs.ext4 /dev/sda1")) == ""

    trust_mode.enable_yolo(ttl_secs=60)
    settings(patterns=("bash",))
    assert chat_grant(THREAD, _call("ls build")) == "hook_pattern"
    assert chat_grant(THREAD, _call("mkfs.ext4 /dev/sda1")) == ""


def test_no_grant_answers_a_delete_of_the_folder_the_conversation_runs_in(state, settings):
    """A delete of the folder the conversation's runtime works in, of the owner's home folder or
    of the filesystem root is put to the owner on the channel's prompt, whatever the chat's
    Trust, YOLO or a pattern would say; an ordinary delete is the chat's Trust to answer."""
    from personalclaw.run_bounds import session_folder

    folder = session_folder(THREAD)
    assert answer_in_chat(THREAD, "trust", channel=CHANNEL) is True
    assert chat_grant(THREAD, _call("rm -rf build")) == "trust", "the control: the Trust answers"
    for command in ("rm -rf .", f"rm -fr {folder}/", "rm -r -f ~", "rm -rf /*"):
        assert chat_grant(THREAD, _call(command)) == "", command

    trust_mode.enable_yolo(ttl_secs=60)
    settings(patterns=("bash",))
    assert chat_grant(THREAD, _call("rm -rf build")) == "hook_pattern"
    assert chat_grant(THREAD, _call("rm -rf ./")) == ""


# ── What a call declares ──────────────────────────────────────────────────────────────────


def test_a_call_answered_by_what_it_declares_is_answered_as_the_chat_answers_it(
    state, settings, ceiling
):
    """A call to a tool that only reads, and one whose work asks the owner itself (the call that
    starts a subagent, whose start is asked, or started by the spawn setting under the ceiling),
    ask nobody in a chat. Neither is a grant, so the ceiling is not what holds them."""
    settings(spawns=True)
    read = _call(tool="memory_recall", risk_level="safe")
    spawn = _call(tool="mcp__personalclaw-core__subagent_run", work_asks=True)
    assert chat_grant(THREAD, read) == "declared_read"
    assert chat_grant(THREAD, spawn) == "work_asks"

    ceiling("ask")
    assert chat_grant(THREAD, read) == "declared_read"
    assert chat_grant(THREAD, spawn) == "work_asks"


def test_the_spawn_setting_answers_no_call_of_its_own(state, settings, ceiling):
    """The spawn setting starts a subagent where its start is decided, held to the ceiling there.
    A call that does not declare it starts one is asked like any other, ceiling or none."""
    settings(spawns=True)
    assert chat_grant(THREAD, _call(tool="subagent_run")) == ""
    ceiling("ask")
    assert chat_grant(THREAD, _call(tool="subagent_run")) == ""


# ── A conversation an app started ─────────────────────────────────────────────────────────


def test_a_conversation_an_app_started_takes_the_operators_pattern_and_no_switch_of_yours(
    state, settings
):
    save_conversation_turn(state.conversation_log, THREAD, "tidy the notes", "Done.")
    state.conversation_log.update_metadata(THREAD, {"created_by_app": "some-app"})
    chat = state.get_or_create_session(THREAD)
    chat._trust = True
    trust_mode.enable_yolo(ttl_secs=60)
    assert chat_grant(THREAD, _call()) == "", "your Trust and YOLO approve nothing of an app's"

    settings(patterns=("write_file",))
    assert chat_grant(THREAD, _call()) == "hook_pattern", "the operator's own pattern still does"


# ── Under an ask ceiling ──────────────────────────────────────────────────────────────────


def test_under_an_ask_ceiling_no_grant_answers_a_call(state, settings, ceiling):
    settings(patterns=("write_file", "bash"))
    assert answer_in_chat(THREAD, "trust", channel=CHANNEL) is True
    trust_mode.enable_yolo(ttl_secs=60)
    assert chat_grant(THREAD, _call()) == "hook_pattern", "the control: with no ceiling it answers"

    ceiling("ask")
    with patch("personalclaw.sel.sel") as audit:
        for call in (_call(), _call("ls build")):
            assert chat_grant(THREAD, call) == ""
    assert set(_refused(audit)) == {"hook_pattern", "yolo"}

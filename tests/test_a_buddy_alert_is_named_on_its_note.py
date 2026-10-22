"""A buddy alert is named on its note, and only on a note that may raise a banner (#3897).

The desktop shell raises an approval, a question or an error as the buddy shows it: the alert
kind's still orb, one line of what changed, and no sound. The gateway names which of the three a
note is (``note["alert"]``), on the one delivery path that already decides whether a note may
interrupt now (mute, the severity floor, quiet hours, the owner's rule) and whether it reaches the
shell (``native``). So the alert is named only where a banner is already allowed, and the decision
itself is unchanged: phase changes, finished work and every other ambient note name none.
"""

from __future__ import annotations

import json

import pytest

from personalclaw import notification_kinds as nk

# ── which alert a note is ────────────────────────────────────────────────────────────────────


def test_an_approval_row_is_an_approval():
    assert nk.alert_of("agent_request", {"approval": "ap-1"}) == "approval"


def test_any_other_decision_is_a_question():
    assert nk.alert_of("needs_input") == "question"
    assert nk.alert_of("agent_request", {}) == "question"


def test_an_error_is_an_error():
    assert nk.alert_of("error") == "error"


@pytest.mark.parametrize(
    "kind", ["info", "warning", "loop_progress", "cron_result", "", "no.such.kind"]
)
def test_an_ambient_note_is_no_alert(kind):
    assert nk.alert_of(kind) == ""


def test_the_alert_kinds_are_the_shells():
    """`desktop/nativeNotifications.js`'s ALERT_KINDS reads the same three words."""
    from pathlib import Path

    shell = (Path(__file__).parents[1] / "desktop" / "nativeNotifications.js").read_text()
    assert 'ALERT_KINDS = Object.freeze(["approval", "question", "error"])' in shell
    assert nk.ALERT_KINDS == ("approval", "question", "error")


# ── named on the note, only where a banner may go ────────────────────────────────────────────


_RULES = {
    "rules": {
        "system/agent_request": {"mode": "immediate", "targets": ["dashboard", "native"]},
        "loop/needs_input": {"mode": "immediate", "targets": ["dashboard", "native"]},
        "system/error": {"mode": "immediate", "targets": ["dashboard", "native"]},
        "system/info": {"mode": "immediate", "targets": ["dashboard", "native"]},
    }
}


def _cap():
    return {
        "native_notifications": {
            "available": True,
            "granted": "granted",
            "requestable": False,
            "reason": "",
        }
    }


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """An isolated config dir for the rules store, the notification settings and the log."""
    (tmp_path / "entity_settings").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("personalclaw.notification_rules.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.providers.entity_routes.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    return tmp_path


def _state(home, monkeypatch, rules=None):
    from tests.chat_test_helpers import _make_state

    if rules is not None:
        (home / "entity_settings" / "notification_rules.json").write_text(
            json.dumps(rules), encoding="utf-8"
        )
    state = _make_state(home)
    sent: list[dict] = []
    monkeypatch.setattr(state, "_broadcast", sent.append)
    state.desktop.register(shell={"version": "0.1.0", "platform": "darwin"}, capabilities=_cap())
    return state, sent


@pytest.fixture()
def shell_state(home, monkeypatch):
    """A DashboardState with a connected shell, its rules naming `native`."""
    return _state(home, monkeypatch, _RULES)


def test_an_approval_a_question_and_an_error_each_name_their_alert(shell_state):
    state, sent = shell_state
    state.notify("agent_request", "Approval needed: shell", "ls", meta={"approval": "ap-1"})
    state.notify("needs_input", "Loop needs your input", "Keep the old page?")
    state.notify("error", "Loop failed", "The model did not answer")
    assert [n.get("native", {}).get("deliver") for n in sent] == [True, True, True]
    assert [n.get("alert") for n in sent] == ["approval", "question", "error"]


def test_an_ambient_note_reaching_the_shell_names_none(shell_state):
    """🪤 The vacuity leg: the same rule path and shell, a note that is no alert."""
    state, sent = shell_state
    state.notify("info", "Backup finished", "nothing to do")
    assert sent[0]["native"]["deliver"] is True
    assert "alert" not in sent[0]


def test_an_emitter_cannot_name_an_alert_itself(shell_state):
    """`alert` is the platform's, as `native` is: meta naming one is dropped."""
    state, sent = shell_state
    state.notify("info", "Backup finished", "", meta={"alert": "approval"})
    assert "alert" not in sent[0]


def test_a_note_that_does_not_reach_the_shell_names_none(home, monkeypatch):
    """No rule naming `native`: no banner, so no alert on the note."""
    state, sent = _state(home, monkeypatch)
    state.notify("error", "Loop failed", "The model did not answer")
    assert "native" not in sent[0]
    assert "alert" not in sent[0]


def test_inside_quiet_hours_an_approval_raises_no_banner(home, shell_state, monkeypatch):
    """Quiet hours stop the ping before any banner: the approval is recorded, not broadcast."""
    from personalclaw.providers import entity_routes as er

    (home / "entity_settings" / "notifications.json").write_text(
        json.dumps(
            {"quiet_hours_enabled": True, "quiet_hours_start": "22:00", "quiet_hours_end": "08:00"}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(er, "_in_quiet_window", lambda *_a, **_k: True)
    state, sent = shell_state
    state.notify("agent_request", "Approval needed: shell", "ls", meta={"approval": "ap-1"})
    assert sent == []
    monkeypatch.setattr(er, "_in_quiet_window", lambda *_a, **_k: False)
    state.notify("agent_request", "Approval needed: shell", "ls", meta={"approval": "ap-2"})
    assert [n.get("alert") for n in sent] == ["approval"]

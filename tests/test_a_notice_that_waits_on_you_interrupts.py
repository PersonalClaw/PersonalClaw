"""A notice that asks you something, or says work stopped until you act, interrupts by default.

Measured before this was written: since only a decision or an error interrupts an owner who has set
no rule (``notification_kinds``), four notices that wait on the owner were still sent as
``system/warning``, a badge, so they waited in the bell with no toast, banner or push:

* an unpaired sender's Allow or Deny (``channel_trust.note_unknown_sender``);
* automations paused by the spend budget (``GatewayOrchestrator._notify_budget_once``);
* an MCP sign-in that ended, whose server's tools stop until you sign in again
  (``mcp_oauth._end_sign_in``);
* an action the denylist held for your approval (``guardrails.denylist.enforce_action``).

Now each is a decision kind of its own, owned by its emitter, at the warning severity it had: it
interrupts by default, and its own row in the rules matrix can quieten it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from personalclaw import notification_kinds as nk


class _Told:
    """A dashboard state that keeps what it was told."""

    def __init__(self) -> None:
        self.kinds: list[str] = []

    def notify(self, kind, title, body, meta=None, **_kw):
        self.kinds.append(kind)


@pytest.fixture
def home(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _interrupts(told: _Told, key: str) -> None:
    assert len(told.kinds) == 1, told.kinds
    kind = nk.kind_for_legacy(told.kinds[0])
    assert kind.key == key
    assert kind.decision, f"{key} is not a decision"
    assert kind.default_mode == "immediate"
    assert kind.default_severity == nk.SEV_WARNING, "its severity is the warning it was"
    assert kind.owner, f"{key} names no owner, so its rules row is not configurable"


def test_an_unpaired_senders_question_interrupts(home) -> None:
    """🔴 Red before: system/warning, a badge."""
    from personalclaw import channel_trust

    told = _Told()
    assert channel_trust.note_unknown_sender(told, "telegram", "4242", "Pat Example")

    _interrupts(told, "channel/unknown_sender")


def test_automations_paused_by_the_budget_interrupt() -> None:
    """🔴 Red before: system/warning, a badge."""
    from personalclaw.gateway import GatewayOrchestrator

    told = _Told()
    GatewayOrchestrator._notify_budget_once(
        SimpleNamespace(dashboard_state=told),
        "Automations paused",
        "The spend ceiling for today is reached, so no automation fires until it resets.",
    )

    _interrupts(told, "cron/budget_paused")


def test_an_mcp_sign_in_that_ended_interrupts(monkeypatch) -> None:
    """🔴 Red before: system/warning, a badge."""
    import personalclaw.config.credentials as credentials
    from personalclaw import mcp_oauth
    from personalclaw.inbox_providers import native_source

    told = _Told()
    monkeypatch.setattr(credentials, "delete_credential", lambda _key: None)
    monkeypatch.setattr(mcp_oauth, "_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(native_source, "get_dashboard_state", lambda: told)

    mcp_oauth._end_sign_in("example-docs", "the authorization server revoked the grant")

    _interrupts(told, "mcp/sign_in_ended")


def test_an_action_held_for_your_approval_interrupts(home, monkeypatch) -> None:
    """🔴 Red before: system/warning, a badge."""
    from personalclaw import sel as sel_mod
    from personalclaw.action_providers import services
    from personalclaw.guardrails import denylist

    told = _Told()
    held = denylist.DenyDecision(
        blocked=True, verdict="needs_human", reason="it deletes a folder", matched="rm -rf"
    )
    monkeypatch.setattr(denylist, "check_action", lambda *_a, **_k: held)
    monkeypatch.setattr(services, "get_action_services", lambda: SimpleNamespace(state=told))
    monkeypatch.setattr(sel_mod, "sel", lambda: SimpleNamespace(log_api_access=lambda **_k: None))

    decision = denylist.enforce_action("shell", {"command": "rm -rf build"})

    assert decision.verdict == "needs_human"
    _interrupts(told, "guardrails/action_held")

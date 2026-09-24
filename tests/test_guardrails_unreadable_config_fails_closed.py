"""Two security controls resolved an UNREADABLE config to their most permissive value.

Both are `except` arms around ``AppConfig.load()``, and both are a different mechanism from
#3424 — that was the *normal return* handing back a permissive dataclass default, which one
central fix in the loader closed. These two are the *exception* path, which no loader change can
reach, and they were left open:

* ``agents/runners.py::guard_unattended_spawn`` — ``except Exception: return``, i.e. **no gate at
  all**, in a function whose own docstring says *"Fail closed: a runtime with no catalog row
  cannot be verified, so it is refused too — the flag's whole promise is that nothing unproven
  runs while nobody is watching."* Code contradicting its declared contract, not a trade-off.
* ``guardrails/denylist.py::_load_config_rules`` — ``except Exception: return [], []``, i.e. an
  **empty denylist**. A denylist that could not be read has not said "nothing is denied"; it has
  said it does not know. Substituting the first for the second converts an unknown into a
  permission.

**Why refusal and not a substituted value.** A denylist's restrictive value is "everything", which
a list cannot express — so the membership rule in ``CONFIG_ON_DISCARDED_READ`` (a security control
whose restrictive value is *expressible*) correctly excludes it. But *refusing the action* is
expressible and invents nothing, and ``DenyDecision`` already carries exactly that shape. Same for
the spawn gate: ``UnverifiedAdapterError`` is the refusal the function already raises when it
cannot verify an adapter, and "cannot read the config" is a case of cannot-verify.

**Both refusals are narrow, which is what makes failing closed affordable here.** The spawn gate
returns early unless the spawn is *unattended* AND carries a runtime id, so interactive chat and
the native in-process loop are untouched. ``check_action`` gates action-provider executions at
three dispatch seams, not chat. And both arms only fire on an UNEXPECTED raise — after #3424 a
merely corrupt ``config.json`` no longer raises, it resolves fail-closed by value — so reaching
either arm means the host cannot read its own configuration, which is exactly when nothing
unattended should run.

Every arm drives the real production function. The config read is made to fail by patching
``AppConfig.load`` to raise, because that is the only thing that reaches these branches — the
lesson ``test_app_catalog_provenance`` already recorded: a corrupt file does not get you there.
"""

from __future__ import annotations

from typing import Any

import pytest


def _explode() -> Any:
    raise OSError("config unreadable (permissions, bad mount, truncated read)")


@pytest.fixture
def unreadable_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every ``AppConfig.load()`` raise — the only way into the arms under test."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader.AppConfig, "load", staticmethod(_explode))


# ── the unattended-spawn adapter gate ───────────────────────────────────────────────────


#: A runtime id that is NOT in the runner catalog, so the gate's own "no catalog row cannot be
#: verified" refusal applies once it gets that far. Named as a probe so it cannot be mistaken
#: for a real runner.
_UNKNOWN_RUNTIME = "acp:probe-runtime-that-is-not-in-the-catalog"


def test_the_spawn_gate_refuses_when_the_config_cannot_be_read(unreadable_config: None) -> None:
    """The defect. An unreadable config must not silently retire the gate.

    The operator who turned ``agent.unattended_requires_verified_adapter`` on asked that nothing
    unproven run while nobody is watching. A config read that failed cannot establish that they
    turned it off, and "we could not check" is not permission.
    """
    from personalclaw.agents.runners import UnverifiedAdapterError, guard_unattended_spawn

    with pytest.raises(UnverifiedAdapterError, match="config"):
        guard_unattended_spawn(_UNKNOWN_RUNTIME, unattended=True)


def test_the_spawn_gate_is_silent_for_an_attended_spawn(unreadable_config: None) -> None:
    """The blast-radius control. Interactive chat is never gated, config readable or not.

    The early return on ``not unattended`` runs before anything reads config, which is what keeps
    failing closed here from taking out the product's main path.
    """
    from personalclaw.agents.runners import guard_unattended_spawn

    guard_unattended_spawn(_UNKNOWN_RUNTIME, unattended=False)


def test_the_spawn_gate_is_silent_without_a_runtime_id(unreadable_config: None) -> None:
    """Second blast-radius control: the native in-process loop carries no runtime id."""
    from personalclaw.agents.runners import guard_unattended_spawn

    guard_unattended_spawn("", unattended=True)


def test_a_readable_config_with_the_flag_off_still_permits_an_unattended_spawn() -> None:
    """The vacuity floor, and the thing a careless fix would break.

    The flag defaults OFF, so refusing every unattended spawn on a *readable* config would
    silently gate every existing install. This arm is what distinguishes "refuse when we cannot
    read the operator's choice" from "refuse always".
    """
    from personalclaw.agents.runners import guard_unattended_spawn

    guard_unattended_spawn(_UNKNOWN_RUNTIME, unattended=True)


def test_the_gate_still_refuses_an_unknown_runtime_when_the_flag_is_on(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second vacuity floor: the gate's pre-existing refusal still works.

    Without this, the arms above could all pass on a gate that had stopped gating for some
    unrelated reason, and the fix would be untested rather than proven.
    """
    import json

    from personalclaw.agents.runners import UnverifiedAdapterError, guard_unattended_spawn

    home = tmp_path / "gate-home"
    home.mkdir()
    (home / "config.json").write_text(
        json.dumps({"agent": {"unattended_requires_verified_adapter": True}}), encoding="utf-8"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))

    with pytest.raises(UnverifiedAdapterError, match="catalog"):
        guard_unattended_spawn(_UNKNOWN_RUNTIME, unattended=True)


# ── the action-provider denylist ─────────────────────────────────────────────────────────


#: An action config with nothing objectionable in it: no sensitive path, no denied command. On a
#: readable config it must be ALLOWED, which is what makes the refusal below attributable to the
#: unreadable config rather than to the action.
_INNOCUOUS_ACTION = {"path": "/tmp/pc-probe-3424b/report.txt", "command": "echo hello"}


def test_the_denylist_refuses_the_action_when_the_config_cannot_be_read(
    unreadable_config: None,
) -> None:
    """The defect. An unreadable operator denylist became an empty one.

    ``check_action`` composes built-ins with the operator's ``security.autonomy_denylist`` and
    ``security.denied_commands``. Reading the operator half as empty means every path and command
    they had denied is waived, silently, for as long as the file is unreadable.
    """
    from personalclaw.guardrails.denylist import check_action

    decision = check_action("probe-provider", dict(_INNOCUOUS_ACTION))
    assert decision.blocked is True, (
        "an unreadable security config resolved to an EMPTY denylist, so every operator deny "
        "rule was waived and the action was allowed. A denylist that could not be read has not "
        "said 'nothing is denied'."
    )
    assert decision.verdict == "block"
    assert "config" in decision.matched


def test_the_enforcement_wrapper_reports_the_refusal(unreadable_config: None) -> None:
    """The refusal reaches the seam the three dispatch points actually call, and is auditable.

    ``enforce_action`` is the wrapper; a refusal that only ``check_action`` could see would not
    stop anything and would not be logged to the SEL.
    """
    from personalclaw.guardrails.denylist import enforce_action

    decision = enforce_action("probe-provider", dict(_INNOCUOUS_ACTION))
    assert decision.blocked is True
    assert decision.reason, "a refusal with no reason tells the user nothing"


def test_a_readable_config_with_no_operator_rules_still_allows_the_action() -> None:
    """The blast-radius control. The denylist is empty by default, so refusing on a readable
    config would take out every automated action on every install."""
    from personalclaw.guardrails.denylist import check_action

    decision = check_action("probe-provider", dict(_INNOCUOUS_ACTION))
    assert decision.allowed is True, (
        "an action with no sensitive path and no denied command must run on a default config — "
        "a fail-closed default that refuses everything is its own defect"
    )


def test_the_builtin_sensitive_path_check_still_fires_on_a_readable_config() -> None:
    """The vacuity floor: the denylist is still enforcing on the normal path.

    Without it, every arm above could pass on a ``check_action`` that had stopped composing
    rules at all.
    """
    from personalclaw.guardrails.denylist import check_action

    decision = check_action("probe-provider", {"path": "~/.ssh/id_rsa"})
    assert decision.blocked is True
    assert decision.matched == "builtin:sensitive_path"

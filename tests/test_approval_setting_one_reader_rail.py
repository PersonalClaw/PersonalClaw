"""The owner's Approval mode ships asking, and is read as an unattended agent's grant in ONE place.

Settings → Agent defaults → Approval mode (``agent.approval_mode``) shipped as ``auto``, the
loosest end of its own scale, and ``auto`` approves every tool call of an agent no chat started.
So by default every such agent approved its own calls, whatever its trigger's Allow had said. The
mode now ships as ``interactive``, and an unattended run gets its approval from the consent given
for that run (the step's own ``approval_mode``, its trigger's Allow, its loop's Mode, a workflow
run's own unattended grant), never from this setting, unless the owner chooses "auto".

This rail keeps it that way, by the shape of the code rather than by a list of call sites:

* the setting's value is read only in ``approval_grants`` (``approval_mode_now``);
* only ``approval_grants.setting_grant`` reads it as a grant, and only it names the grant;
* ``approval_mode_now`` is called only where it is not a grant: a chat's own trust-reads floor
  and what the Doctor shows;
* the shipped value is the strictest the control has.

A new reader is not wrong by being new: it reds here so the question "is this a grant for a run
nobody consented to?" is answered where it is added.
"""

from __future__ import annotations

import pytest

from personalclaw import approval_grants
from personalclaw.config.editable import _EDITABLE_CONFIG
from personalclaw.config.loader import AgentConfig


def test_the_shipped_mode_is_the_strictest_the_control_has():
    control = _EDITABLE_CONFIG["agent.approval_mode"]["security"]
    shipped = AgentConfig().approval_mode
    assert shipped == "interactive"
    assert all(
        not control.loosens(other, shipped)
        for other in _EDITABLE_CONFIG["agent.approval_mode"]["values"]
    ), "some value is stricter than the shipped one"


@pytest.mark.parametrize(("mode", "grant"), [("auto", "setting"), ("interactive", ""), ("", "")])
def test_setting_grant_reads_the_mode_now(monkeypatch, mode, grant):
    monkeypatch.setattr(approval_grants, "approval_mode_now", lambda: mode)
    assert approval_grants.setting_grant() == grant

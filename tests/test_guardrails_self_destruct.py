"""A scheduled action can no longer kill the gateway running it (WF2AUT-14).

🔴 THE DEFECT, measured before a line was written. The packaged baseline denylist
(`security.baseline_denied_command_patterns`) is the only thing that had ever looked at this, and
it looks at LITERAL TEXT. Counted against it:

* `personalclaw stop` — no pattern. `personalclaw update` — no pattern. `personalclaw service
  uninstall` — no pattern. Each one ends the host process; each one passed.
* `.*personal.?claw restart.*` does exist, and `PC=personalclaw; $PC restart` walks straight
  past it, as does `personalclaw   restart` (two spaces) and `$(which personalclaw) restart`.

And the hazard is reachable, not theoretical: a clock trigger's `bash` action reaches
`BashActionProvider.execute`, which runs `/bin/sh -c command` and screens nothing itself, while
`personalclaw restart`/`stop` are SERVICE-FIRST (`cli_server._restart` calls
`service_controller.restart_service()` first) — so they bounce the installed service, i.e. the
process hosting the run. The fire's `ScheduleRunStore` row never reaches a terminal state, and
what the user sees afterwards is a hung run, not a self-inflicted stop.

Two things this file is careful about, because both are ways a guard like this goes wrong:

* **Over-refusal is an outage.** `systemctl restart nginx`, `docker restart my-redis`,
  `personalclaw status`, `personalclaw cron update nightly` and a log line that merely mentions
  the word must all still run. Asserted, not assumed.
* **A literal-text screen is the bypass class.** So the evasion shapes are named individually
  and driven individually — a respelled absolute path, a shell variable, a `cd` first, and an
  extra argument appended to an otherwise-innocent command.

Distinctness from WF2AUT-9 (`skip_if_active`) is asserted too, by outcome: that guard admits the
fire this one refuses.
"""

from __future__ import annotations

import asyncio
import types

import pytest

import personalclaw.action_providers as AP
from personalclaw.guardrails.policy import unattended_dispatch_key
from personalclaw.guardrails.self_destruct import classify_host_effect, unattended_host_effect

#: A sessionless unattended identity, exactly what `gateway._fire_store_trigger` threads.
UNATTENDED = unattended_dispatch_key("trigger:clock:nightly")
#: An ordinary chat session key: matches no unattended prefix, so a human is watching.
INTERACTIVE = "main"


# --------------------------------------------------------------------------------------
# The effect classification itself.
# --------------------------------------------------------------------------------------

#: Every one of these ends the process that would run it. Grouped by WHY they are here.
SELF_DESTRUCT = [
    # The plain spellings. Only the first was screened before this atom.
    "personalclaw restart",
    "personalclaw stop",
    "personalclaw update",
    "personalclaw service uninstall",
    "personalclaw service install",
    # The service managers, which is what SERVICE-FIRST actually reaches.
    "sudo systemctl restart personalclaw.service",
    "systemctl stop personalclaw",
    "launchctl unload ~/Library/LaunchAgents/io.personalclaw.gateway.plist",
    "launchctl kickstart -k gui/501/io.personalclaw.gateway",
    "pkill -f personalclaw",
    # Reached through a wrapper, which must not launder it.
    "nohup sudo -u root env FOO=1 personalclaw restart &",
    "timeout 30 personalclaw stop",
    "/bin/sh -c 'personalclaw   stop'",
    "python -m personalclaw restart",
    "uv run personalclaw stop",
    # An innocent prefix does not launder what follows it.
    "personalclaw doctor && personalclaw stop",
]

#: THE EVASION SHAPES the atom names, one per id. A screening rule matched against literal text
#: passes every one of these, which is why the guard classifies the effect instead.
EVASIONS = {
    "respelled_absolute_path": "/usr/local/bin/personalclaw restart --port 10000",
    "respelled_relative_path": "./personalclaw stop",
    "respelled_hyphenated_name": "/usr/bin/personal-claw restart",
    "extra_whitespace": "personalclaw   restart",
    "via_shell_variable": "PC=personalclaw; $PC restart",
    "via_braced_variable": "PC=/usr/local/bin/personalclaw\n${PC} stop",
    "via_command_substitution": "$(which personalclaw) restart",
    "cd_first": "cd /usr/local/bin && ./personalclaw stop",
    "argument_appended_to_innocent_command": "systemctl restart nginx personalclaw",
    "env_assignment_prefix": "FOO=bar personalclaw update",
}

#: Legitimate work. A guard that refuses any of these is an outage, so each is asserted.
BENIGN = [
    "echo nightly backup done",
    "personalclaw status --json",
    "personalclaw snapshot",
    "personalclaw doctor",
    # Same VERB spelling, nested under a different subcommand — not the host process.
    "personalclaw cron update nightly",
    "personalclaw agent update research",
    "personalclaw skills install summarize",
    "personalclaw service status",
    # A DIFFERENT service, and a container. The atom's mandatory negative.
    "systemctl --user restart nginx.service",
    "sudo systemctl restart postgresql",
    "service sshd restart",
    "docker restart my-redis",
    "podman stop my-worker",
    "brew services restart postgresql",
    "pkill -f node",
    "kill 999999",
    # Prose that mentions us. Arguments to a data program are never code.
    'echo "personalclaw restart is dangerous"',
    "grep -r restart /var/log/app.log",
    # Ordinary automation.
    "git commit -am wip && git push",
    "rsync -a /data /backup",
    "$EDITOR notes.md",
    "python -m mypkg.cli restart",
]


@pytest.mark.parametrize("command", SELF_DESTRUCT)
def test_a_command_that_would_kill_the_runner_is_classified_as_self(command):
    """🔴 THE CONTROL. Each of these acts on THIS process, so each must classify as `self`."""
    effect = classify_host_effect(command)
    assert effect.refuses, f"{command!r} would kill its own runner and was not classified"
    assert effect.target == "self", f"{command!r} classified as target={effect.target!r}"


@pytest.mark.parametrize("shape", sorted(EVASIONS), ids=sorted(EVASIONS))
def test_the_evasion_shapes_do_not_evade(shape):
    """The bypass class, one shape at a time.

    Every entry here defeats `.*personal.?claw restart.*` — the only rule that existed before
    this atom — which is the whole argument for classifying the effect rather than the spelling.
    A regression here means the guard has quietly become a text screen again.
    """
    command = EVASIONS[shape]
    effect = classify_host_effect(command)
    assert effect.refuses, f"the {shape} evasion passed the guard: {command!r}"


@pytest.mark.parametrize("command", BENIGN)
def test_legitimate_work_is_not_refused(command):
    """A guard that refuses too much is an outage that happens to pass its own block test.

    `systemctl restart postgresql` and `docker restart my-redis` are the atom's named negative:
    restarting something ELSE is not self-destruction, and the target is what decides.
    """
    effect = classify_host_effect(command)
    assert not effect.refuses, f"{command!r} was refused; effect={effect}"


def test_the_refusal_NAMES_WHAT_IT_TRIED_TO_DO():
    """Legibility is part of the contract: "refused" alone leaves the user with a mystery."""
    reason = classify_host_effect("personalclaw service uninstall").reason()
    assert "personalclaw service uninstall" in reason, reason
    assert "reinstall" in reason, reason


def test_the_refusal_NAMES_THE_INTERACTIVE_PATH():
    """The guard is about unattended self-destruction, not about forbidding restarts — so the
    refusal has to say where the operation IS available, or it reads as "never allowed"."""
    reason = classify_host_effect("personalclaw restart").reason()
    assert "interactive" in reason.lower(), reason


# --------------------------------------------------------------------------------------
# Fail closed.
# --------------------------------------------------------------------------------------

UNCLASSIFIABLE = {
    "unresolved_program_beside_a_lifecycle_verb": "$CMD restart",
    "unresolved_subcommand_of_our_own_cli": "personalclaw ${SUBCOMMAND}",
    "unresolved_service_unit": "systemctl restart $UNIT",
    "unresolved_kill_target": "kill $(cat /tmp/some.pid)",
    "unparseable_quoting": 'echo "unbalanced && personalclaw restart',
}


@pytest.mark.parametrize("shape", sorted(UNCLASSIFIABLE), ids=sorted(UNCLASSIFIABLE))
def test_an_unclassifiable_action_FAILS_CLOSED(shape):
    """Refused, and refused as `unknown` rather than mislabelled as a known effect — the reason
    a user reads must say the guard could not tell, not invent an operation it never saw."""
    effect = classify_host_effect(UNCLASSIFIABLE[shape])
    assert effect.refuses, f"{shape} was admitted: {UNCLASSIFIABLE[shape]!r}"
    assert effect.kind == "unknown", effect
    assert "could not be determined" in effect.reason(), effect.reason()


def test_failing_closed_is_BOUNDED_and_not_a_blanket_refusal():
    """The other half of fail-closed, and the half that turns a guard into an outage if missed:
    an unresolvable variable OUTSIDE lifecycle territory is not this guard's business."""
    for command in ("$EDITOR notes.md", "$PYTHON script.py", "cat $LOGFILE"):
        assert not classify_host_effect(command).refuses, command


# --------------------------------------------------------------------------------------
# Unattended only — and the ONE unattendedness decision.
# --------------------------------------------------------------------------------------


def test_an_unattended_dispatch_is_refused():
    assert unattended_host_effect("personalclaw restart", UNATTENDED) is not None


def test_the_SAME_operation_is_allowed_when_a_HUMAN_asks():
    """Restarting the gateway is ordinary administration. The guard is about unattended
    self-destruction, so an interactive session key must pass the identical command."""
    assert unattended_host_effect("personalclaw restart", INTERACTIVE) is None
    assert unattended_host_effect("personalclaw service uninstall", INTERACTIVE) is None


def test_an_EMPTY_session_key_is_treated_as_unattended():
    """The closed direction on an unknown identity. `is_unattended_session("")` reads ATTENDED
    (the PHF-8 shape), and a caller that cannot name who is running an action has not shown that
    a human is watching — so this guard does not inherit that reading."""
    assert unattended_host_effect("personalclaw stop", "") is not None


def test_unattendedness_comes_from_is_unattended_session_and_NOT_a_SECOND_NOTION(monkeypatch):
    """Proven by outcome: flipping the shared predicate flips this guard.

    If the guard had minted its own notion of "unattended" — a prefix list of its own, an env
    check — this monkeypatch would not reach it and the assertion below would fail.
    """
    import personalclaw.guardrails.policy as policy

    monkeypatch.setattr(policy, "is_unattended_session", lambda key: False)
    assert unattended_host_effect("personalclaw restart", UNATTENDED) is None

    monkeypatch.setattr(policy, "is_unattended_session", lambda key: True)
    assert unattended_host_effect("personalclaw restart", INTERACTIVE) is not None


# --------------------------------------------------------------------------------------
# Distinct from WF2AUT-9's liveness guard.
# --------------------------------------------------------------------------------------


def test_WF2AUT9s_liveness_guard_ADMITS_the_fire_this_guard_refuses():
    """The two guards answer different questions, asserted by outcome rather than by prose.

    `skip_if_active` (triggers/service.py:547, computed by `_target_active_kwargs` at :815) asks
    "is a second CONCURRENT fire about to trample work in flight?" and is fail-OPEN. A
    self-destructing action with no `skip_if_active` declared — the default, and the shape a user
    actually writes — is NOT busy by that guard's reckoning, so it would fire. This guard refuses
    it. Neither subsumes the other, so neither is a duplicate of the other.
    """
    from personalclaw.triggers.liveness import is_target_active

    active, reason = is_target_active(None, now=0.0, base_dir=None)
    assert (active, reason) == (False, ""), "WF2AUT-9's guard should admit an unguarded trigger"
    assert unattended_host_effect("personalclaw restart", UNATTENDED) is not None


# --------------------------------------------------------------------------------------
# The seam: the guard must run BEFORE the provider executes.
# --------------------------------------------------------------------------------------


def _orch():
    """The construction `test_triggers_denylist` uses: the fire path needs no __init__ state."""
    from personalclaw.gateway import GatewayOrchestrator

    return object.__new__(GatewayOrchestrator)


def _trigger(config: dict, *, tid: str = "clock:nightly"):
    return types.SimpleNamespace(
        id=tid,
        kind="clock",
        workflow={"inline": {"provider": "bash", "config": config}},
    )


class _Recorder:
    """Stands in for the real bash provider so "was it reached?" is directly observable."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, config, ctx, timeout=30):
        self.calls.append(dict(config))
        return types.SimpleNamespace(success=True)


@pytest.fixture
def provider(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(AP, "get_action_provider", lambda name: rec)
    return rec


def _fire(trigger):
    asyncio.run(_orch()._fire_store_trigger(trigger, {"trigger_id": trigger.id}))


def _rows(tid: str) -> list[dict]:
    from personalclaw.config.loader import config_dir
    from personalclaw.schedule_history import ScheduleRunStore

    rows, _total = asyncio.run(ScheduleRunStore(config_dir()).list_for_job(tid))
    return rows


def test_the_action_NEVER_REACHES_THE_PROVIDER(provider):
    """🔴 The guard is worth nothing unless it runs BEFORE execution. `bash_provider.execute`
    runs `/bin/sh -c command` immediately and screens nothing, so "refused" can only mean the
    provider was never called — a refusal logged after the shell ran is an epitaph."""
    _fire(_trigger({"command": "PC=personalclaw; $PC restart"}))
    assert provider.calls == [], "the self-destructing action reached the shell"


def test_a_benign_action_at_the_SAME_seam_still_fires(provider):
    """The seam-level inverse of the over-refusal test: the gate must be a filter, not a wall."""
    _fire(_trigger({"command": "docker restart my-redis"}))
    assert provider.calls == [{"command": "docker restart my-redis"}]


def test_the_refusal_is_RECORDED_with_the_legible_reason(provider):
    """Observable, not a silent drop, and recorded as `skipped_gate` rather than `failed` —
    `failed` is what counts toward autopause-after-5, so recording a policy refusal as a failure
    would disable the user's automation for the guardrail doing its job."""
    _fire(_trigger({"command": "personalclaw stop"}, tid="clock:recorded"))
    rows = _rows("clock:recorded")
    assert len(rows) == 1, f"expected exactly one refusal row, got {rows}"
    assert rows[0]["status"] == "skipped_gate"
    assert "personalclaw stop" in rows[0]["error"], rows[0]["error"]
    assert "self_destruct" in rows[0]["error"], rows[0]["error"]


def test_the_guard_reaches_actions_whose_command_lives_under_another_key(provider):
    """`_config_commands` already reads `command`/`cmd`/`script`/`args`, and the guard is wired
    into that same collection — so an app provider that names its field `cmd` inherits this
    without knowing it exists, exactly as the denylist's contract promises."""
    _fire(_trigger({"cmd": "personalclaw service uninstall"}, tid="clock:altkey"))
    assert provider.calls == [], "a self-destructing action under `cmd` was not screened"

"""A command PersonalClaw runs cannot change a security setting through the CLI's config command.

The dashboard keeps every security setting to the owner (`config.edit_spec.app_write_refusal`, the
consent on a loosening write), but an agent's shell could type the same change as a command:
`personalclaw config set agent.yolo true`, a whole file applied with `config set --file`, or
`config edit`. The packaged baseline now refuses those (`security.CLI_SECURITY_CONFIG_PATTERNS`),
on every path that asks the shell denylist. The owner's own terminal runs no screen, so the same
command typed there still works.
"""

from __future__ import annotations

import argparse
import json
import re
from unittest.mock import patch

import pytest

from personalclaw import security
from personalclaw.config.edit_spec import SECURITY_SECTIONS, security_control
from personalclaw.config.editable import _EDITABLE_CONFIG

pytestmark = pytest.mark.security


CLI = "personalclaw"

SECURITY_FIELDS = sorted(k for k, spec in _EDITABLE_CONFIG.items() if security_control(spec))
#: Outside the security sections, which are refused whole, and not a security setting.
ORDINARY_FIELDS = sorted(
    k
    for k, spec in _EDITABLE_CONFIG.items()
    if not security_control(spec) and k.split(".")[0] not in SECURITY_SECTIONS
)


def _rule_matches(command: str) -> bool:
    return any(re.search(p, command, re.IGNORECASE) for p in security.CLI_SECURITY_CONFIG_PATTERNS)


def _refused_by_the_rule(command: str) -> security.DeniedCommand:
    """Refused by the baseline, and the rule matches it: an earlier baseline pattern may match
    first (`guardrails.budgets.max_tokens_per_run` also says `token`), which refuses it too."""
    denied = security.denied_command(command)
    assert denied is not None, f"not refused: {command}"
    assert not denied.added
    assert _rule_matches(command), command
    return denied


def test_the_three_patterns_are_part_of_the_verified_baseline():
    assert len(security.CLI_SECURITY_CONFIG_PATTERNS) == 3
    baseline = security.baseline_denied_command_patterns()
    assert all(p in baseline for p in security.CLI_SECURITY_CONFIG_PATTERNS)


@pytest.mark.parametrize("field", SECURITY_FIELDS)
def test_every_security_setting_is_refused_through_set_and_unset(field):
    """Enumerated from the registry, so a security setting added later is covered or reds here."""
    _refused_by_the_rule(f"{CLI} config set {field} x")
    _refused_by_the_rule(f"{CLI} config unset {field}")


@pytest.mark.parametrize(
    "section", ["auth", "security", "sandbox", "guardrails", "external_access"]
)
def test_a_whole_security_section_is_refused(section):
    _refused_by_the_rule(f"{CLI} config unset {section}")
    _refused_by_the_rule(f"{CLI} config set {section} '{{}}'")


@pytest.mark.parametrize("section", ["agent", "browse", "durability", "proactive"])
def test_a_section_holding_a_security_setting_cannot_be_replaced_whole(section):
    _refused_by_the_rule(f"{CLI} config set {section} '{{}}'")
    _refused_by_the_rule(f"{CLI} config unset {section}")


@pytest.mark.parametrize(
    "command",
    [
        f"{CLI} config set --file /tmp/whole.json",
        f"{CLI} config set -f /tmp/whole.json",
        f"{CLI} config set --file=/tmp/whole.json",
        f"{CLI} config edit",
        f"EDITOR=true {CLI} config edit",
    ],
)
def test_a_whole_file_write_is_refused(command):
    """A file or an editor can carry any security setting, so neither is read for which."""
    _refused_by_the_rule(command)


@pytest.mark.parametrize(
    "command",
    [
        f"{CLI} config set Agent.YOLO true",
        f'{CLI} config set "agent.approval_mode" auto',
        f"{CLI} config set -- agent.yolo true",
        f"{CLI} -v config set agent.yolo true",
        f"python -m {CLI} config set agent.yolo true",
        f"/usr/local/bin/{CLI} config set agent.yolo true",
        f"cd /tmp && {CLI} config set sandbox.nofile 0",
        f"echo $({CLI} config set auth.session_ttl 90d)",
        "personal-claw config set security.outside_home '[]'",
    ],
)
def test_the_spellings_a_shell_accepts_are_refused(command):
    _refused_by_the_rule(command)


@pytest.mark.parametrize("field", ORDINARY_FIELDS)
def test_an_ordinary_setting_is_left_alone(field):
    assert not _rule_matches(f"{CLI} config set {field} x")
    assert not _rule_matches(f"{CLI} config unset {field}")


@pytest.mark.parametrize(
    "field", ["agent.self_qa.enabled", "agent.self_qa.watched_repo", "durability.keep_daily"]
)
def test_a_neighbour_of_a_security_setting_is_left_alone(field):
    """`agent.self_qa.fix_branch_enabled` and `durability.sync_enabled` are named exactly."""
    assert security.denied_command(f"{CLI} config set {field} x") is None


def test_the_hooks_section_is_refused_whole():
    """Its auto-approve lists decide which tool calls run without asking you."""
    _refused_by_the_rule(f"{CLI} config set hooks.auto_approve_tools '[\"*\"]'")
    _refused_by_the_rule(f"{CLI} config unset hooks")


@pytest.mark.parametrize(
    "command",
    [
        f"{CLI} config get",
        f"{CLI} config get security",
        f"{CLI} config get agent.yolo",
        f"{CLI} config set dashboard.url http://localhost:8888",
        f"{CLI} config set agent.log_level DEBUG",
        f"{CLI} config set agent.yolo_hint x",
        f"{CLI} config unset slack",
        f"grep security notes/{CLI}-config.md",
    ],
)
def test_reads_and_other_writes_still_run(command):
    assert security.denied_command(command) is None


def test_the_refusal_says_who_may_change_it_and_where():
    said = _refused_by_the_rule(f"{CLI} config set agent.yolo true").refusal()
    assert "one of the shell denylist's built-in patterns" in said
    assert security.CLI_SECURITY_CONFIG_REASON in said
    assert "Settings → Security" in said and "their own terminal" in said


def test_the_action_seam_refuses_it_too():
    """A bash action, a workflow step, a loop's check: the same rule through `check_action`."""
    from personalclaw.guardrails.denylist import check_action

    decision = check_action("bash", {"command": f"{CLI} config set agent.yolo true"})
    assert decision.blocked
    assert decision.matched.startswith("cmd:")
    assert decision.matched[len("cmd:") :] in security.CLI_SECURITY_CONFIG_PATTERNS


def test_the_owners_own_terminal_still_changes_a_security_setting(tmp_path, capsys):
    """The CLI itself runs no screen: the rule binds the commands PersonalClaw runs, not her."""
    from personalclaw import cli_config

    cfg = tmp_path / "config.json"
    with patch("personalclaw.config.loader.config_path", return_value=cfg):
        cli_config._config_cmd(
            argparse.Namespace(config_action="set", key="sandbox.nofile", value="2048", file=None)
        )
    assert json.loads(cfg.read_text())["sandbox"]["nofile"] == 2048
    assert "sandbox.nofile" in capsys.readouterr().out


def test_each_pattern_compiles():
    for pat in security.CLI_SECURITY_CONFIG_PATTERNS:
        re.compile(pat)

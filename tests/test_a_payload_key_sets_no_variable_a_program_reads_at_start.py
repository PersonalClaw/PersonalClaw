"""A bash action's payload key sets no variable that decides which code a program runs, by family.

A bash action hands each top-level payload key to its command as an environment variable.
``PROTECTED_ENV_NAMES`` kept a key from setting the variables that decide which code runs, but as
a list of single names, so a member of one of those families that nobody listed got through. The
rule is now over the whole name space (``bash_provider._why_left_out``): a key reaches the command
only when it holds a lowercase letter, since the variables programs read at start are named in
capitals, or is one of PersonalClaw's own (``PC_``, which a workflow's step names in capitals);
and whatever its case, never one of the families that decide which code a program runs
(``env.decides_what_runs``) or a variable programs read in lowercase too (a proxy, npm's
settings). Each such key is left out and the warning says why; a lowercase or camelCase key, and a
``PC_`` one, still reach the command.

By name only: each key carries the inert value ``"x"``.
"""

from __future__ import annotations

import logging

import pytest

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.bash_provider import _payload_env

pytestmark = pytest.mark.security

#: Members of each family that no single-name list held.
FAMILY_MEMBERS = (
    "PERL5OPT",
    "JAVA_TOOL_OPTIONS",
    "_JAVA_OPTIONS",
    "JDK_JAVA_OPTIONS",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_KEY_0",
    "GIT_CONFIG_VALUE_0",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_GLOBAL",
    "LD_BIND_NOW",
    "DYLD_FALLBACK_LIBRARY_PATH",
    "PYTHONWARNINGS",
    "RUBYLIB",
    "NODE_EXTRA_CA_CERTS",
    "MAVEN_OPTS",
    "SOME_RUNTIME_OPTIONS",
    "git_config_count",
)

#: Keys in capitals, which no family names: the variables programs read at start are spelled so.
CAPITALS = ("SHELLOPTS", "PS4", "CLASSPATH", "OPENSSL_CONF", "GCONV_PATH", "SOME_UNKNOWN_SETTING")

#: Variables programs read in lowercase too.
READ_IN_ANY_CASE = ("http_proxy", "HTTPS_PROXY", "all_proxy", "npm_config_userconfig")

#: PersonalClaw's own names, which a workflow's step gives its command in capitals: they reach it.
OWN = ("PC_CWD", "PC_GUARD_CMD", "PC_VERIFY_CMD", "PC_OPT_BUDGET_USD", "PC_CHECK")

#: PersonalClaw's own prefix on a name a program reads all the same: still left out, and why.
OWN_BUT_READ = {
    "PC_SOME_OPTIONS": "decides which code a program runs",
    "PC_HTTP_PROXY": "reads whatever its case",
}

#: Keys a trigger's payload carries, lowercase or camelCase, which still reach the command.
ORDINARY = (
    "job_id",
    "last_result",
    "new_count",
    "item",
    "options",
    "url",
    "title",
    "jobId",
    "lastResult",
    "pullRequest",
)


def _ctx(**payload: str) -> ActionContext:
    return ActionContext(event="trigger.fired", context="web_watch:w", payload=payload)


@pytest.mark.parametrize("name", FAMILY_MEMBERS)
def test_a_key_of_a_family_is_left_out_and_said_why(name, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="personalclaw.action_providers.bash_provider"):
        env = _payload_env(_ctx(**{name: "x"}))
    assert name not in env
    said = [r.getMessage() for r in caplog.records if name in r.getMessage()]
    assert said and "decides which code a program runs" in said[0], said


@pytest.mark.parametrize("name", CAPITALS)
def test_a_key_in_capitals_is_left_out_and_said_why(name, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="personalclaw.action_providers.bash_provider"):
        env = _payload_env(_ctx(**{name: "x"}))
    assert name not in env
    said = [r.getMessage() for r in caplog.records if name in r.getMessage()]
    assert said and "written in capitals" in said[0], said


@pytest.mark.parametrize("name", READ_IN_ANY_CASE)
def test_a_variable_read_in_any_case_is_left_out_and_said_why(name, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="personalclaw.action_providers.bash_provider"):
        env = _payload_env(_ctx(**{name: "x"}))
    assert name not in env
    said = [r.getMessage() for r in caplog.records if name in r.getMessage()]
    assert said and "reads whatever its case" in said[0], said


def test_a_workflow_steps_own_names_reach_the_command() -> None:
    env = _payload_env(_ctx(**{name: "x" for name in OWN}))
    assert {name: env.get(name) for name in OWN} == {name: "x" for name in OWN}


@pytest.mark.parametrize(("name", "why"), OWN_BUT_READ.items())
def test_the_own_prefix_does_not_let_a_read_name_through(name, why, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="personalclaw.action_providers.bash_provider"):
        env = _payload_env(_ctx(**{name: "x"}))
    assert name not in env
    said = [r.getMessage() for r in caplog.records if name in r.getMessage()]
    assert said and why in said[0], said


@pytest.mark.parametrize("name", FAMILY_MEMBERS)
def test_the_rule_is_the_family(name) -> None:
    from personalclaw.env import decides_what_runs

    assert decides_what_runs(name), name


def test_an_ordinary_key_still_reaches_the_command() -> None:
    env = _payload_env(_ctx(**{name: "x" for name in ORDINARY}))
    assert {name: env.get(name) for name in ORDINARY} == {name: "x" for name in ORDINARY}

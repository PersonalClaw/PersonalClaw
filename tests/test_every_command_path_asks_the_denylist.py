"""Every place PersonalClaw runs a command someone wrote asks the shell denylist first.

Settings → Security → Shell denylist bound the native bash tool and nothing else: a loop's check, a
bash action, a workflow step and an app's setup hook each ran a command its owner's patterns refuse.
They now ask ``shell_denylist.denied_command``, and this rail keeps every such site asking. It
classifies every spawn site in ``src/personalclaw`` (the census and keys of
``tests/test_spawn_ceiling_audit.py``: ``file::qualname::callee``) as one of two:

* ``_ASKS`` — the site runs a command someone wrote: the agent, an agent CLI's request, a loop, a
  workflow, an automation, an app's author. Checked in the code: the function holding the spawn
  calls ``denied_command`` before the spawn, itself or through a function of its own module.
* ``_RUNS_NO_WRITTEN_COMMAND`` — every other site, with what it runs instead: a program and
  arguments PersonalClaw composes (git, npm, ffmpeg, a probe), a program the owner installed and
  starts (an MCP server, an agent CLI, an app's code), the owner's own terminal, or a seam that runs
  what its caller handed it. Checked too: no such site may hand a shell a command it was given
  (``sh -c <text>``, ``shell=True``), the shape a written command takes.

A new spawn site reds this rail by name until it is classified, and a stale entry reds it too. The
falsification tests at the bottom run both checks on source written to fail them.

A site in ``_ASKS`` that runs with nobody answering asks a second check before it spawns, the
action denylist (``guardrails.denylist.check_command``, ``check_action`` for a command), under the
identity it judges the command under: among its rules, unattended work may not stop, restart,
update or reinstall the PersonalClaw it runs in. Every site in ``_ASKS`` is classified once more,
as such a site (``_ASKS_THE_ACTION_DENYLIST``) or with where that check is asked for it instead
(``_ACTION_DENYLIST_ELSEWHERE``), so a new command runner cannot skip it unclassified.
"""

from __future__ import annotations

import ast

from every_command_path_asks_the_denylist_shared import (
    _ASKS,
    _ASKS_THE_ACTION_DENYLIST,
    THE_UNATTENDED_CHECK,
    _asks,
    _first_ask,
    _functions,
    _module_strings,
    _runs_a_handed_shell_command,
    _spawn_lines,
)

# ── the sites that run a command someone wrote ─────────────────────────────────────────────────


#: The rest of ``_ASKS``, and where the action denylist is asked for each instead.
_ACTION_DENYLIST_ELSEWHERE: dict[str, str] = {
    "action_providers/bash_provider.py::BashActionProvider.execute::create_subprocess_limited": (
        "an action provider: each dispatch that runs one with nobody answering asks it first "
        "(tests/test_action_provider_chokepoints.py)"
    ),
    "apps/app_manager.py::_run_hook::subprocess.run": (
        "an app's lifecycle hook, which runs when you install, update, enable, disable or "
        "uninstall the app"
    ),
}


# ── every other site, and what it runs instead ─────────────────────────────────────────────────


# ── reading a function out of the tree ─────────────────────────────────────────────────────────


def _called(node: ast.AST) -> list[tuple[str, int]]:
    """Every name *node* calls (`f(…)`, `x.f(…)`), with its line."""
    out = []
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            f = call.func
            name = (
                f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            )
            if name:
                out.append((name, call.lineno))
    return out


# ── the rail ───────────────────────────────────────────────────────────────────────────────────


def test_every_site_that_runs_a_written_command_is_classified_for_the_action_denylist():
    asked, elsewhere = set(_ASKS_THE_ACTION_DENYLIST), set(_ACTION_DENYLIST_ELSEWHERE)
    assert not asked & elsewhere
    unclassified = sorted(set(_ASKS) - asked - elsewhere)
    assert not unclassified, (
        "A site that runs a command someone wrote, not classified for the action denylist. If "
        "it runs with nobody answering, it asks `guardrails.denylist.check_command` under the "
        "identity it judges the command under before it spawns, and joins "
        "_ASKS_THE_ACTION_DENYLIST; "
        "otherwise say where that check is asked for it (_ACTION_DENYLIST_ELSEWHERE):\n"
        + "\n".join(f"  {k}" for k in unclassified)
    )
    stale = sorted((asked | elsewhere) - set(_ASKS))
    assert not stale, "Not a site in _ASKS; remove:\n" + "\n".join(f"  {k}" for k in stale)


# ── falsification: each check, run on source written to fail it ────────────────────────────────

_BYPASS = """
async def run(cmd):
    return await create_subprocess_limited("/bin/sh", "-c", cmd)
"""

_ASKED = """
async def run(cmd):
    if denied_command(cmd) is not None:
        return None
    return await create_subprocess_limited("/bin/sh", "-c", cmd)
"""

_ASKED_TOO_LATE = """
async def run(cmd):
    proc = await create_subprocess_limited("/bin/sh", "-c", cmd)
    denied_command(cmd)
    return proc
"""

_ASKED_ONE_HOP = """
def refused(cmd):
    return denied_command(cmd)

async def run(cmd):
    argv = ["bash", "-lc", cmd]
    if refused(cmd):
        return None
    return await create_subprocess_limited(*argv)
"""

_FIXED = """
_SCRIPT = "uname -a"

def run():
    subprocess.run(["/bin/sh", "-c", "uname -a"])
    return subprocess.run(["/bin/sh", "-c", _SCRIPT])
"""

_SHELL_TRUE = """
def run(cmd):
    return subprocess.run(cmd, shell=True)
"""


_SKIPS_THE_ACTION_DENYLIST = """
async def run(cmd):
    if denied_command(cmd) is not None:
        return None
    return await create_subprocess_limited("/bin/sh", "-c", cmd)
"""

_ASKS_THE_ACTION_DENYLIST_THROUGH_A_HELPER = """
def held(cmd):
    return check_command(cmd, session_key=KEY)

async def run(cmd):
    if held(cmd).blocked or denied_command(cmd) is not None:
        return None
    return await create_subprocess_limited("/bin/sh", "-c", cmd)
"""

_ASKS_THE_ACTION_DENYLIST_TOO_LATE = """
async def run(cmd):
    proc = await create_subprocess_limited("/bin/sh", "-c", cmd)
    enforce_action("check", {"command": cmd}, session_key=KEY)
    return proc
"""

_ASKS_THE_ACTION_DENYLIST_AS_NOBODY = """
async def run(cmd):
    if check_action("check", {"command": cmd}).blocked:
        return None
    return await create_subprocess_limited("/bin/sh", "-c", cmd)
"""


def _action_denylist_asks(source: str) -> tuple[list[tuple[ast.Call, int]], int]:
    functions = _functions(ast.parse(source))
    spawned = min(_spawn_lines(functions["run"], "create_subprocess_limited"))
    return _asks("run", functions, THE_UNATTENDED_CHECK), spawned


def test_a_runner_that_skips_the_action_denylist_is_caught():
    asks, _spawned = _action_denylist_asks(_SKIPS_THE_ACTION_DENYLIST)
    assert asks == []


def test_a_runner_that_asks_the_action_denylist_through_a_helper_first_passes():
    asks, spawned = _action_denylist_asks(_ASKS_THE_ACTION_DENYLIST_THROUGH_A_HELPER)
    assert asks and min(line for _call, line in asks) < spawned
    assert all(any(k.arg == "session_key" for k in call.keywords) for call, _line in asks)


def test_a_runner_that_asks_too_late_or_as_nobody_is_caught():
    late, spawned = _action_denylist_asks(_ASKS_THE_ACTION_DENYLIST_TOO_LATE)
    assert late and min(line for _call, line in late) > spawned
    nobody, _spawned = _action_denylist_asks(_ASKS_THE_ACTION_DENYLIST_AS_NOBODY)
    assert nobody and not any(
        k.arg == "session_key" for call, _line in nobody for k in call.keywords
    )


def _check(source: str, qualname: str = "run") -> tuple[int | None, bool]:
    tree = ast.parse(source)
    functions = _functions(tree)
    return (
        _first_ask(qualname, functions),
        _runs_a_handed_shell_command(functions[qualname], _module_strings(tree)),
    )


def test_a_site_that_runs_a_handed_command_without_asking_is_caught():
    asked, shell = _check(_BYPASS)
    assert asked is None and shell


def test_a_site_that_asks_first_passes_and_one_that_asks_after_does_not():
    asked, _ = _check(_ASKED)
    assert asked is not None and asked < min(
        _spawn_lines(_functions(ast.parse(_ASKED))["run"], "create_subprocess_limited")
    )
    late, _ = _check(_ASKED_TOO_LATE)
    spawn = min(
        _spawn_lines(_functions(ast.parse(_ASKED_TOO_LATE))["run"], "create_subprocess_limited")
    )
    assert late is not None and late > spawn


def test_asking_through_a_helper_of_the_same_module_counts():
    asked, shell = _check(_ASKED_ONE_HOP)
    assert asked is not None and shell


def test_fixed_shell_text_is_no_handed_command_and_shell_true_always_is():
    assert _check(_FIXED) == (None, False)
    assert _check(_SHELL_TRUE)[1] is True

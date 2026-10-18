"""The source-tree checks split from
``tests/test_every_command_path_asks_the_denylist.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index
from every_command_path_asks_the_denylist_shared import (
    _ASKS,
    _ASKS_THE_ACTION_DENYLIST,
    _BASH_TOOL,
    _RUNS_NO_WRITTEN_COMMAND,
    THE_CHECK,
    THE_UNATTENDED_CHECK,
    _asks,
    _first_ask,
    _functions,
    _module_strings,
    _name_of,
    _parsed,
    _runs_a_handed_shell_command,
    _spawn_lines,
)
from spawn_ceiling_audit_shared import _census, _src_root


def test_every_site_that_runs_a_written_command_asks_the_denylist_before_it_spawns():
    for key in sorted(_ASKS):
        rel, qualname, callee = key.split("::")
        functions = _functions(_parsed(rel))
        asked = _first_ask(qualname, functions)
        spawned = min(_spawn_lines(functions[qualname], callee))
        assert asked is not None, f"{key} runs a written command and never asks {THE_CHECK}"
        assert asked < spawned, f"{key} asks {THE_CHECK} (line {asked}) after it spawns ({spawned})"


def test_every_unattended_command_runner_asks_the_action_denylist_before_it_spawns():
    for key in sorted(_ASKS_THE_ACTION_DENYLIST):
        rel, qualname, callee = key.split("::")
        functions = _functions(_parsed(rel))
        asks = _asks(qualname, functions, THE_UNATTENDED_CHECK)
        spawned = min(_spawn_lines(functions[qualname], callee))
        assert asks, f"{key} runs a command nobody answers and never asks the action denylist"
        asked = min(line for _call, line in asks)
        assert asked < spawned, f"{key} asks the action denylist (line {asked}) after it spawns"
        for call, _line in asks:
            assert any(k.arg == "session_key" for k in call.keywords), (
                f"{key} asks the action denylist with no session_key=: the session's profile is "
                "skipped, and a command is judged as nobody's"
            )


def test_the_bash_action_is_reached_only_as_a_provider():
    """The property that earns the bash action its place in _ACTION_DENYLIST_ELSEWHERE: nothing
    constructs it but the provider registry, so it runs only through an action dispatch, which
    asks the action denylist for a run nobody answers (``test_action_provider_chokepoints``)."""
    built = set()
    for path, tree in source_index.package_trees():
        for call in ast.walk(tree):
            if isinstance(call, ast.Call) and _name_of(call) == "BashActionProvider":
                built.add(path.relative_to(_src_root()).as_posix())
    assert built == {"action_providers/registry.py", "action_providers/bash_provider.py"}, built


def test_no_other_site_hands_a_shell_a_command_it_was_given():
    for key in sorted(_RUNS_NO_WRITTEN_COMMAND):
        rel, qualname, _callee_name = key.split("::")
        tree = _parsed(rel)
        functions = _functions(tree)
        assert not _runs_a_handed_shell_command(functions[qualname], _module_strings(tree)), (
            f"{key} hands a shell a command it was given, so it runs a written command: make it "
            f"ask `shell_denylist.denied_command` before it spawns, and move it to _ASKS"
        )


def test_the_shell_detector_sees_every_real_shell_site():
    """Vacuity control: the detector recognises the written-command shells the tree really has."""
    for key in (
        _BASH_TOOL,
        "loop/gates.py::run_verify_command::create_subprocess_limited",
        "action_providers/bash_provider.py::BashActionProvider.execute::create_subprocess_limited",
        "apps/app_manager.py::_run_hook::subprocess.run",
    ):
        rel, qualname, _ = key.split("::")
        tree = _parsed(rel)
        assert _runs_a_handed_shell_command(_functions(tree)[qualname], _module_strings(tree)), key


def test_the_census_is_not_empty_and_holds_every_asking_site():
    census = _census()
    assert len(census) > 100, len(census)
    assert set(_ASKS) <= set(census)
    assert source_index.text(Path(_src_root(), "shell_denylist.py")).count(f"def {THE_CHECK}(") == 1


def test_every_spawn_site_is_classified():
    census = set(_census())
    classified = set(_ASKS) | set(_RUNS_NO_WRITTEN_COMMAND)
    unmapped = sorted(census - classified)
    assert not unmapped, (
        "A spawn site nobody has classified. Decide whether it runs a command someone wrote (then "
        "it asks `shell_denylist.denied_command` before it spawns, and joins _ASKS) or what it "
        "runs instead (_RUNS_NO_WRITTEN_COMMAND):\n" + "\n".join(f"  {k}" for k in unmapped)
    )
    stale = sorted(classified - census)
    assert not stale, "No such spawn site any more; remove:\n" + "\n".join(f"  {k}" for k in stale)
    assert not set(_ASKS) & set(_RUNS_NO_WRITTEN_COMMAND)

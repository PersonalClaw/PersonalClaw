"""The source-tree checks split from
``tests/test_a_command_a_run_starts_keeps_to_its_egress_tier.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

#: The sandbox's launch of a command a run starts, which reads the run's tier: every site that runs
#: a command someone wrote calls one of these before it spawns.
_THE_LAUNCH = ("wrap_argv", "egress_bound_argv")


#: A site that runs a command someone wrote, but never for a run, and why.
_FOR_NO_RUN = {
    "apps/app_manager.py::_run_hook::subprocess.run": (
        "an app's lifecycle hook, run when the owner installs, updates, enables, disables or "
        "uninstalls the app"
    ),
}


#: A command a run starts that the denylist rail files under what its script's tool calls reach.
_ALSO_A_RUNS_COMMAND = {
    "schedule_script.py::run_script_sandboxed::subprocess.run": "a script action's script",
}


def _launch_lines(node, functions: dict) -> list[int]:
    """The lines where *node* asks the sandbox to launch, itself or through a function of its own
    module that does so directly (one hop)."""
    import ast

    def named(call: ast.Call) -> str:
        f = call.func
        return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""

    def launches(fn) -> bool:
        return any(isinstance(c, ast.Call) and named(c) in _THE_LAUNCH for c in ast.walk(fn))

    lines = []
    for call in (c for c in ast.walk(node) if isinstance(c, ast.Call)):
        helper = functions.get(named(call))
        if named(call) in _THE_LAUNCH or (
            helper is not None and helper is not node and launches(helper)
        ):
            lines.append(call.lineno)
    return lines


def test_every_command_a_run_starts_is_launched_by_the_sandbox():
    """🔴 Red on integration: a loop's and a workflow's check, a workflow's setup and teardown steps
    and an effect's teardown ran with no sandbox at all, so no tier could take their network."""
    from every_command_path_asks_the_denylist_shared import _ASKS, _functions, _parsed, _spawn_lines

    assert set(_FOR_NO_RUN) <= set(_ASKS), "a site this rail exempts is no longer a command site"
    for key in sorted((set(_ASKS) | set(_ALSO_A_RUNS_COMMAND)) - set(_FOR_NO_RUN)):
        rel, qualname, callee = key.split("::")
        functions = _functions(_parsed(rel))
        node = functions[qualname]
        launched = _launch_lines(node, functions)
        spawned = _spawn_lines(node, callee)
        assert launched, f"{key} runs a command a run starts without the sandbox's launch"
        assert min(launched) < min(spawned), f"{key} spawns before the sandbox launches it"

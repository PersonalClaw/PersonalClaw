"""The source-tree checks split from
``tests/test_app_worker_runtime.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import app_worker_runtime_shared

_isolated_home = app_worker_runtime_shared._isolated_home
_stub_background = app_worker_runtime_shared._stub_background


def test_the_worker_spawn_actually_goes_through_the_ceiling_shim():
    """The spawn-ceiling AUDIT pins the classification; this pins the behaviour.

    Measured, not assumed: deleting `spawn_shim_argv` from `_spawn` and spawning the bare
    command leaves `tests/test_spawn_ceiling_audit.py` fully GREEN (3 passed). That audit
    asserts every spawn site is *classified*, not that any site is *ceilinged* — so the entry
    reading "app background worker -> tool ceiling via spawn_shim_argv" is a claim nothing
    checked. An unceilinged worker matters more than an unceilinged one-shot: it is
    long-lived and unattended, which is the fork bomb nobody is watching.

    Asserted by AST on the call, not by grepping the file, because a mention in a docstring
    or a comment is not a call.
    """
    import ast
    import inspect
    import textwrap

    from personalclaw.apps import worker_runtime as wr

    # dedent first: `ast.parse` on an indented METHOD source raises IndentationError, which
    # would make this rail a permanent false RED rather than a check (found by running it).
    tree = ast.parse(textwrap.dedent(inspect.getsource(wr.WorkerSupervisor._spawn)))
    called = {
        (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", ""))
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    assert "spawn_shim_argv" in called, (
        "WorkerSupervisor._spawn no longer routes the command through spawn_shim_argv, so the "
        "worker spawns with no resource ceiling. The spawn-ceiling audit will not notice: it "
        "checks the classification table, not the call."
    )
    # Vacuity floor: the AST walk really does see this function's calls.
    assert "Popen" in called or "subprocess" in str(called), called


def test_every_teardown_goes_through_the_one_unload_and_it_stops_the_worker() -> None:
    """`disable`, `force_uninstall` and `update` each take the app down through
    `app_runtime.unload`, and the worker teardown lives there, beside the backend's.

    They used to carry their own copies of the teardown, and `update` had none: an update left
    the previous version's worker running its old code. MEASURED before the first form of this
    rail existed: deleting the worker teardown from both copies left the whole app-lifecycle +
    worker + audit set green. Asserted by AST rather than by grepping the modules, because a
    mention in a docstring is not a call.
    """
    import ast
    import inspect
    import textwrap

    from personalclaw.apps import app_manager, app_runtime

    def _calls(fn: object) -> set[str]:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))  # type: ignore[arg-type]
        return {
            (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", ""))
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
        }

    for fn in (app_manager.disable, app_manager.force_uninstall, app_manager.update):
        assert "unload" in _calls(fn), (
            f"app_manager.{fn.__name__} no longer takes the app down through app_runtime.unload; "
            "its background worker (and the rest of what it runs) would outlive it"
        )
    called = _calls(app_runtime.unload)
    assert "_stop_workers" in called, (
        "app_runtime.unload no longer tears down the app's background worker; the app goes "
        "away and its unattended process does not"
    )
    # Vacuity floor: the walk sees unload's calls, and the backend teardown is the precedent the
    # worker's is meant to sit beside.
    assert "_stop_backend" in called, called


def test_boot_starts_the_worker_watchdog_beside_the_backend_one() -> None:
    """Nothing sweeps unless boot starts the watchdog — and boot was asserted by nothing.

    Three of the five clauses (crash survival, stop-on-disable, policy pause) are delivered
    by the periodic sweep, and the sweep has exactly one production caller: the boot block in
    `providers/loader.py::load_all_extensions`. MEASURED before this rail existed: deleting
    `start_worker_watchdog()` from that block left the whole app-lifecycle + worker + audit set
    green (116 passed) — a gateway that revives nothing, pauses nothing and stops nothing on
    disable would have shipped without a red.

    The sibling backend watchdog IS railed (`test_spawn_hazard_audit.py`
    ::test_backend_respawn_is_reached_from_watchdog_thread); this is the missing half of that
    pair, asserted on the enclosing function by AST so a mention in the block's comment does not
    satisfy it.
    """
    import ast
    import inspect
    import textwrap

    from personalclaw.providers import loader

    tree = ast.parse(textwrap.dedent(inspect.getsource(loader.load_all_extensions)))
    called = {
        (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", ""))
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    assert "start_worker_watchdog" in called, (
        "boot no longer starts the app-worker watchdog, so nothing sweeps: a crashed worker is "
        "never revived, a disabled app keeps its worker, and a budget breach never pauses one"
    )
    # Vacuity floor: this really is the boot block, so the assertion is about the live call site
    # and not about an empty set of calls.
    assert "start_backend_watchdog" in called, called

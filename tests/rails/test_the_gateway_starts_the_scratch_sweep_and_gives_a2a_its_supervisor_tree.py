"""The gateway wires two things the behaviour tests cannot reach without a running gateway.

* It starts the sweep of idle scratch folders as it runs and stops it as it shuts down
  (``run_bounds.start_scratch_sweep`` / ``stop_scratch_sweep``), which
  ``tests/test_a_runs_scratch_folder_is_removed_when_its_work_ends.py`` drives directly.
* The A2A door starts its runs on ``request.app["state"].workflows``: the gateway sets that on
  the state its dashboard serves, and the door is registered on that dashboard's app.
  ``tests/test_an_a2a_retry_after_a_restart_returns_its_task.py`` drives the door on a state of
  its own.

Read from the source tree, so they run in the rails job.
"""

from __future__ import annotations

import ast

import source_index
from rails_repo import REPO

GATEWAY = REPO / "src" / "personalclaw" / "gateway.py"
ROUTES = REPO / "src" / "personalclaw" / "dashboard" / "routes.py"


def _orchestrator() -> ast.ClassDef:
    tree = source_index.tree(GATEWAY)
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GatewayOrchestrator"
    )


def _method(name: str) -> ast.AST:
    return next(
        node
        for node in _orchestrator().body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


def _calls(node: ast.AST) -> set[str]:
    """Each call under *node*, as written: ``run_bounds.start_scratch_sweep``."""
    return {ast.unparse(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)}


def test_the_gateway_starts_the_scratch_sweep_as_it_runs_and_stops_it_as_it_shuts_down():
    assert "run_bounds.start_scratch_sweep" in _calls(_method("run"))
    assert "run_bounds.stop_scratch_sweep" in _calls(_method("_shutdown"))


def test_the_dashboard_state_the_a2a_door_reads_holds_the_gateways_workflow_supervisor():
    assigned = {
        ast.unparse(node) for node in ast.walk(_orchestrator()) if isinstance(node, ast.Assign)
    }
    assert "self.dashboard_state.workflows = self.workflow_watchdog" in assigned
    assert "_register_a2a" in _calls(source_index.tree(ROUTES))

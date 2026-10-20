"""Every action provider reads whose work it is doing through one door, and only a workflow step's
dispatch opens it.

The engine stamps a step's dispatch with whose work the step is (`engine.RUN_IDENTITY_KEYS`: the
run, the step's node and instance, the run's project and folder, the attempt's key), and providers
confine, file and attribute what they do by those: the folder a proof bundle is sealed from, the run
whose artifacts a step may read, the run whose journal takes a step's rows, the project an item is
filed under. Every other dispatch hands its action a payload too, and that payload is what its
event carried: a trigger's fire, a lifecycle hook, a view's refresh, a Run now. So a provider
reads those keys through `action_providers.base.run_identity`, which answers only for a step's
dispatch, and never from the payload itself, where an event's own data could spell any of them.

🔴 Red before: providers read them straight from the payload, whatever had dispatched them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import personalclaw.action_providers as providers_package
from personalclaw.action_providers.base import WORKFLOW_STEP_EVENT, ActionContext, run_identity
from personalclaw.workflows.engine import RUN_IDENTITY_KEYS

PROVIDERS = Path(providers_package.__file__).parent

#: The dispatches that are not a step's, by the event each names: a clock's fire, a webhook's, a
#: file's, a chained fire, a lifecycle hook, a view's refresh, a Run now, an answer, the triage
#: digest's own.
NOT_A_STEP = (
    "trigger.fired",
    "webhook.fire",
    "file.changed",
    "trigger.chained",
    "Stop",
    "tile_refresh",
    "manual.run",
    "manual.answer",
    "triage_auto_execute",
    "",
)


def payload_reads(source: str) -> list[tuple[int, str]]:
    """``(line, key)`` for each read of a run identity key from an action's payload in *source*.

    A read is ``<receiver>.get("<key>", …)`` or ``<receiver>["<key>"]`` (a load, not a store) whose
    receiver is the payload itself: ``ctx.payload``, ``getattr(ctx, "payload", …)``, either of
    those ``or {}``, a ``dict()`` of one, or a name bound to one (``provenance = ctx.payload or
    {}``). The action's own config (``action_config``, ``cfg``) is not the payload, and is the
    action's to read; nor is what a call handed the payload returned.
    """
    tree = ast.parse(source)
    bound: set[str] = set()

    def _is_payload(expr: ast.AST) -> bool:
        if isinstance(expr, ast.Attribute):
            return expr.attr == "payload"
        if isinstance(expr, ast.Name):
            return expr.id in bound
        if isinstance(expr, ast.BoolOp):
            return any(_is_payload(value) for value in expr.values)
        if isinstance(expr, ast.IfExp):
            return _is_payload(expr.body) or _is_payload(expr.orelse)
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
            if expr.func.id == "getattr" and len(expr.args) >= 2:
                return isinstance(expr.args[1], ast.Constant) and expr.args[1].value == "payload"
            if expr.func.id == "dict" and expr.args:
                return _is_payload(expr.args[0])
        return False

    for _ in range(2):  # a name bound to a name bound to the payload
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and _is_payload(node.value):
                bound |= {target.id for target in node.targets if isinstance(target, ast.Name)}

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            key, receiver = node.args[0].value, node.func.value
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.slice, ast.Constant)
        ):
            key, receiver = node.slice.value, node.value
        else:
            continue
        if key in RUN_IDENTITY_KEYS and _is_payload(receiver):
            found.append((node.lineno, str(key)))
    return found


def test_no_action_provider_reads_whose_work_it_is_from_its_payload() -> None:
    """🔴 Red before: twelve providers read the run, its folder, its project or the step's instance
    from the payload of whatever dispatched them."""
    modules = sorted(PROVIDERS.glob("*.py"))
    assert len(modules) > 30, "the census must read the providers' own modules"

    found = {
        path.name: reads
        for path in modules
        if (reads := payload_reads(path.read_text(encoding="utf-8")))
    }

    assert found == {}, (
        "read whose work a step is through action_providers.base.run_identity, which answers only "
        f"for a step's dispatch, never from the payload: {found}"
    )


def test_the_census_finds_each_way_a_payload_is_read() -> None:
    """Control: each form a provider has read the payload in is found, and the action's own config,
    a write into a dict of its own and what a call handed the payload returned are not."""
    source = """
async def execute(self, action_config, ctx):
    a = ctx.payload.get("run_id", "")
    b = (ctx.payload or {}).get("workspace")
    c = (getattr(ctx, "payload", None) or {}).get("project_id")
    payload = getattr(ctx, "payload", None) or {}
    d = payload["instance_path"]
    provenance = ctx.payload or {}
    e = provenance.get("node_id")
    f = action_config.get("run_id")
    out = dict(action_config)
    out["run_id"] = run_identity(ctx, "run_id")
    result = await score(payload)
    g = result.get("run_id")
"""

    assert sorted(key for _line, key in payload_reads(source)) == sorted(
        ["run_id", "workspace", "project_id", "instance_path", "node_id"]
    )


@pytest.mark.parametrize("event", NOT_A_STEP)
def test_only_a_steps_dispatch_says_whose_work_it_is(event: str) -> None:
    """A step's dispatch carries what the engine stamped; any other names no run, project or
    folder, whatever its payload spells."""
    stamped = {
        "run_id": "run-own",
        "node_id": "evidence",
        "instance_path": "root.children[2]",
        "project_id": "project-own",
        "workspace": "/srv/own",
        "idempotency_key": "key-own",
    }
    assert set(stamped) == set(RUN_IDENTITY_KEYS)

    step = ActionContext(event=WORKFLOW_STEP_EVENT, payload=dict(stamped))
    other = ActionContext(event=event, payload=dict(stamped))

    assert {key: run_identity(step, key) for key in stamped} == stamped
    assert {key: run_identity(other, key) for key in stamped} == dict.fromkeys(stamped, "")

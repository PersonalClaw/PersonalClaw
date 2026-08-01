"""The run snapshot projection and its schema (WF2-R11).

The snapshot is the widget's foundation: `foldSnapshot` builds the whole view-model from it
and every subsequent event is a patch on top. A malformed snapshot therefore does not
degrade the widget, it corrupts it — a missing `nodes` renders a run with no steps, and a
`state` the FE's look-up table does not know renders an unstyled row with no icon. Worse,
both look like an engine bug rather than a projection bug, so the cost lands on whoever
debugs the wrong layer.

So the projection is **validated before transmission**. Not with a schema library (the
codebase has none in core, and adding a dependency for one shape is the wrong trade) but
with an explicit field table checked in one pass. What it buys:

* a missing or wrongly-typed field is caught HERE, where the fix is one line, instead of in
  a browser console;
* the field list is one readable declaration a reader can diff against the FE's
  `WorkflowRunDetailData`, rather than being implied by a dict literal three files away;
* an unknown enum value fails loudly in tests while degrading gracefully in production —
  a run that reached a state the FE cannot style should still render, because a user staring
  at an empty page learns nothing.

**Validation never blocks delivery.** :func:`project` returns the projection plus its
issues; the caller ships the projection and logs the issues. A snapshot withheld because one
optional field was the wrong type would be strictly worse than a slightly wrong one — the
widget would show nothing at all.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.workflows.models import InstanceState, RunStatus

logger = logging.getLogger(__name__)

#: `(field, types, required)` — the run-level contract, mirroring the FE's
#: `WorkflowRunDetailData`. Kept as data so the test can assert the two agree.
RUN_FIELDS: tuple[tuple[str, tuple[type, ...], bool], ...] = (
    ("run_id", (str,), True),
    ("workflow", (str,), True),
    ("status", (str,), True),
    ("spec_version", (int,), True),
    ("error", (str,), False),
    ("attention", (dict, type(None)), False),
    ("tokens", (int,), False),
    ("elapsed_secs", (int, float), False),
    ("nodes", (list,), True),
)

#: The per-node contract. `node_id` is required-but-possibly-empty: an anonymous node has no
#: id, and the FE falls back to the instance path for its label.
NODE_FIELDS: tuple[tuple[str, tuple[type, ...], bool], ...] = (
    ("instance_path", (str,), True),
    ("node_id", (str,), True),
    ("state", (str,), True),
    ("attempt", (int, type(None)), False),
    ("degraded_reason", (str,), False),
    ("failure", (dict, type(None)), False),
)

_RUN_STATUSES = frozenset(s.value for s in RunStatus)
_NODE_STATES = frozenset(s.value for s in InstanceState)


def validate_snapshot(snap: Any) -> list[str]:
    """Return a list of human-readable issues; empty means valid.

    Issues, not exceptions: the caller ships the snapshot regardless (see the module note),
    and a list is what a test can assert on and a log line can carry whole.
    """
    if not isinstance(snap, dict):
        return [f"snapshot must be an object, got {type(snap).__name__}"]

    issues: list[str] = []
    for name, types, required in RUN_FIELDS:
        if name not in snap:
            if required:
                issues.append(f"missing required field {name!r}")
            continue
        if not isinstance(snap[name], types) or isinstance(snap[name], bool):
            issues.append(
                f"{name!r} must be {'|'.join(t.__name__ for t in types)}, "
                f"got {type(snap[name]).__name__}"
            )

    status = snap.get("status")
    if isinstance(status, str) and status not in _RUN_STATUSES:
        issues.append(f"unknown run status {status!r}")

    nodes = snap.get("nodes")
    if isinstance(nodes, list):
        for i, node in enumerate(nodes):
            issues.extend(f"nodes[{i}]: {msg}" for msg in _validate_node(node))
    return issues


def _validate_node(node: Any) -> list[str]:
    if not isinstance(node, dict):
        return [f"must be an object, got {type(node).__name__}"]
    issues: list[str] = []
    for name, types, required in NODE_FIELDS:
        if name not in node:
            if required:
                issues.append(f"missing required field {name!r}")
            continue
        if not isinstance(node[name], types) or isinstance(node[name], bool):
            issues.append(
                f"{name!r} must be {'|'.join(t.__name__ for t in types)}, "
                f"got {type(node[name]).__name__}"
            )
    state = node.get("state")
    if isinstance(state, str) and state not in _NODE_STATES:
        issues.append(f"unknown node state {state!r}")
    return issues


def project(run_id: str) -> tuple[dict[str, Any], list[str]]:
    """Build a run's wire snapshot and validate it. Returns `(snapshot, issues)`.

    The `ok` flag is stripped: it is the service layer's in-process success signal, and an
    HTTP 200 with a full body already carries that information. Leaving it in would put a
    field on the wire that means nothing to the client and invite it to branch on it.
    """
    from personalclaw.workflows import service

    result = service.status(run_id)
    if not result.get("ok"):
        # A read failure is not a projection: the caller (the SSE handler) has already
        # checked the run exists, so reaching here means it vanished between the two reads.
        return {}, [str(result.get("code") or "unavailable")]

    snap = {k: v for k, v in result.items() if k != "ok"}
    issues = validate_snapshot(snap)
    if issues:
        # Logged, not raised: a widget with a slightly wrong snapshot beats no widget, and
        # the test suite is where this is supposed to fail.
        logger.warning("workflow %s snapshot projection invalid: %s", run_id, "; ".join(issues))
    return snap, issues

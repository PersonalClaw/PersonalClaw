"""The run snapshot projection and its schema validation (WF2-R11).

The snapshot is the widget's foundation: the FE builds its whole view-model from this one
frame and every later event is a patch on top. So a malformed field does not degrade the
widget, it corrupts it — and the symptom (a run with no steps, an unstyled row) reads as an
engine bug rather than a projection bug, which sends the debugging to the wrong layer.

Validation therefore runs before transmission, and the tests pin both halves of the
contract:

* **the shape** — required fields present and typed, enums recognized, and the field table
  agreeing with the FE's `WorkflowRunDetailData` (the two drifting is the actual risk);
* **the delivery rule** — validation NEVER blocks the snapshot. A widget with a slightly
  wrong snapshot beats no widget, so `project` reports issues and ships anyway.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.models import InstanceState, NodeInstance, RunStatus, WorkflowRun
from personalclaw.workflows.projection import (
    NODE_FIELDS,
    RUN_FIELDS,
    project,
    validate_snapshot,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


SPEC = {
    "name": "proj",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [{"kind": "transform", "id": "a", "config": {"expr": {"n": 1}}}],
    },
}


def _valid() -> dict:
    return {
        "run_id": "abc12345",
        "workflow": "proj",
        "status": "running",
        "spec_version": 1,
        "error": "",
        "attention": None,
        "tokens": 0,
        "elapsed_secs": 0.0,
        "nodes": [
            {
                "instance_path": "root.children[0]",
                "node_id": "a",
                "state": "done",
                "attempt": 1,
                "degraded_reason": "",
                "failure": None,
            }
        ],
    }


class TestValidShapes:
    def test_a_well_formed_snapshot_has_no_issues(self) -> None:
        assert validate_snapshot(_valid()) == []

    def test_optional_fields_may_be_absent(self) -> None:
        """A projection that only carries the required core is still usable — the FE defaults
        tokens/elapsed to zero. Demanding them would make the validator reject snapshots the
        widget renders fine."""
        snap = {k: v for k, v in _valid().items() if k in {f[0] for f in RUN_FIELDS if f[2]}}
        assert validate_snapshot(snap) == []

    def test_an_anonymous_node_keeps_an_empty_id(self) -> None:
        """`node_id` is required-but-possibly-empty: a node without an id is legal, and the
        FE falls back to the instance path for its label. Absent is the bug, empty is not."""
        snap = _valid()
        snap["nodes"][0]["node_id"] = ""
        assert validate_snapshot(snap) == []

    def test_every_real_run_status_is_accepted(self) -> None:
        for status in RunStatus:
            snap = _valid()
            snap["status"] = status.value
            assert validate_snapshot(snap) == [], status

    def test_every_real_node_state_is_accepted(self) -> None:
        for state in InstanceState:
            snap = _valid()
            snap["nodes"][0]["state"] = state.value
            assert validate_snapshot(snap) == [], state


class TestRejections:
    def test_a_missing_required_field_is_reported(self) -> None:
        snap = _valid()
        del snap["nodes"]
        issues = validate_snapshot(snap)
        assert any("nodes" in i for i in issues)

    def test_a_wrongly_typed_field_is_reported(self) -> None:
        snap = _valid()
        snap["spec_version"] = "1"
        assert any("spec_version" in i for i in validate_snapshot(snap))

    def test_a_bool_is_not_accepted_as_an_int(self) -> None:
        """`isinstance(True, int)` is True in Python. Without the explicit bool check a
        `tokens: true` would pass and the widget would render "true tokens"."""
        snap = _valid()
        snap["tokens"] = True
        assert any("tokens" in i for i in validate_snapshot(snap))

    def test_an_unknown_run_status_is_reported(self) -> None:
        """Loud in tests, graceful in production: the issue is recorded but the snapshot still
        ships, because a run that reached an unstyled state should still render."""
        snap = _valid()
        snap["status"] = "quantum_superposition"
        assert any("quantum_superposition" in i for i in validate_snapshot(snap))

    def test_an_unknown_node_state_is_reported_with_its_index(self) -> None:
        snap = _valid()
        snap["nodes"].append({"instance_path": "b", "node_id": "b", "state": "vibing"})
        issues = validate_snapshot(snap)
        assert any("nodes[1]" in i and "vibing" in i for i in issues)

    def test_a_non_object_node_is_reported_not_crashed(self) -> None:
        snap = _valid()
        snap["nodes"] = ["just a string"]
        assert any("nodes[0]" in i for i in validate_snapshot(snap))

    def test_a_non_object_snapshot_is_reported(self) -> None:
        assert validate_snapshot(["not", "an", "object"])
        assert validate_snapshot(None)


class TestProject:
    def test_a_real_run_projects_cleanly(self) -> None:
        run = store.create(WorkflowRun(id="", workflow_name="proj"))
        store.write_spec(run.id, SPEC)
        store.write_state(run.id, {"root.children[0]": NodeInstance(path="root.children[0]")})
        snap, issues = project(run.id)
        assert issues == []
        assert snap["run_id"] == run.id
        assert snap["nodes"][0]["node_id"] == "a"

    def test_the_ok_flag_never_reaches_the_wire(self) -> None:
        """`ok` is the service layer's in-process success signal. An HTTP 200 with a full body
        already says that, and leaving it in would invite a client to branch on it."""
        run = store.create(WorkflowRun(id="", workflow_name="proj"))
        store.write_spec(run.id, SPEC)
        snap, _issues = project(run.id)
        assert "ok" not in snap

    def test_a_run_that_vanished_projects_empty_with_a_reason(self) -> None:
        snap, issues = project("nonexistent")
        assert snap == {}
        assert issues == ["WF_RUN_NOT_FOUND"]

    def test_a_run_with_no_instances_yet_is_still_valid(self) -> None:
        """A run created but not yet ticked has zero nodes. The widget shows 0/0 rather than
        an error — an empty list is a legitimate projection, not a malformed one."""
        run = store.create(WorkflowRun(id="", workflow_name="proj"))
        store.write_spec(run.id, SPEC)
        snap, issues = project(run.id)
        assert issues == []
        assert snap["nodes"] == []

    def test_an_invalid_projection_is_still_returned(self, monkeypatch) -> None:
        """The delivery rule: validation reports, it does not block. Withholding a snapshot
        over one bad optional field would show the user nothing at all — strictly worse than
        showing them something slightly wrong."""
        from personalclaw.workflows import service

        monkeypatch.setattr(
            service, "status", lambda run_id: {"ok": True, "run_id": run_id, "nodes": "broken"}
        )
        snap, issues = project("abc12345")
        assert issues, "expected the malformed projection to be reported"
        assert snap["run_id"] == "abc12345"  # …and shipped anyway


def test_the_field_table_matches_the_frontend_type() -> None:
    """The real drift risk: the backend adds a field the FE type does not know, or renames one
    the FE still reads. Both are silent — TypeScript checks the FE against ITSELF, and no
    Python test sees the .ts file. So the projection's field table is compared to
    `WorkflowRunDetailData` directly.
    """
    root = Path(__file__).resolve().parents[1]
    api = (root / "web/src/lib/api.ts").read_text(encoding="utf-8")

    run_iface = re.search(r"export interface WorkflowRunDetailData \{(.*?)\n\}", api, re.S)
    node_iface = re.search(r"export interface WorkflowNodeState \{(.*?)\n\}", api, re.S)
    assert run_iface and node_iface, "couldn't find the FE workflow snapshot types in api.ts"

    def fields(block: str) -> set[str]:
        # The FE declares several fields per line, separated by `;`. Nested object literals
        # are stripped first so an inner `class?:` cannot masquerade as a top-level field and
        # mask a genuinely missing one.
        flat = re.sub(r"\{[^{}]*\}", "", block)
        return set(re.findall(r"(?:^|[;{])\s*([a-z_]+)\s*\??:", flat, re.M))

    for label, table, block in (
        ("run", RUN_FIELDS, run_iface.group(1)),
        ("node", NODE_FIELDS, node_iface.group(1)),
    ):
        backend = {f[0] for f in table}
        frontend = fields(block)
        missing = backend - frontend
        assert not missing, f"{label}: projected fields the FE type does not declare: {missing}"

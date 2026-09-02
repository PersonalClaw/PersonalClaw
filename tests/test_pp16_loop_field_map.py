"""The `Loop` → `WorkflowRun` + `SupervisorPolicy` field map is exhaustive and RESOLVES (PP-16).

`workflows/loop_run_map.py` is a declaration with no runtime consumer — the honesty marker this
program established in `WF2LOO-12` and reused for `PP-14`. A declaration nobody executes rots
silently, so this rail is what makes it a measured map rather than a claim:

* **Both drift directions.** A new `Loop` field with no row reds it (that field is the "feature
  about to be dropped silently" the plan warns about); a row naming a field that no longer exists
  reds it too.
* **Every path resolves.** A `RUN`/`POLICY`/`DEF`/`INTENT` destination is resolved attribute-by-
  attribute against the real dataclass, so renaming `WorkflowRun.elapsed_seconds` — or deleting
  `SupervisorPolicy.idle_secs` — fails HERE instead of at the migration.
* **Every `RUN_INPUT` names a real template input.** The rail re-reads the shipped
  `bundled/*/workflow.json` files and checks the row's parameter against the UNION of what they
  declare — so a parameter no template declares any more reds the row that promised it. (The union,
  not per-template: `task` is declared by four of the six loop-kind templates and two of them name
  it differently, which is the finding the `task` row records.)
* **The homeless list is a ratchet.** `_EXPECTED_HOMELESS` is pinned exactly: a NEW field with no
  home is a deliberate owner decision, and the set must SHRINK as PP-16 lands, never grow quietly.
* **The status delta is computed, not trusted.** `STATUS_VOCABULARY_DELTA` is checked against the
  two enums, so it cannot drift into a comfortable fiction.

Vacuity floors throughout: the resolver is proved to REJECT a bogus path (a resolver that silently
returns `None` would pass every row), and each census asserts it found rows before concluding
anything about them.
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

import pytest

from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.workflows import loop_run_map
from personalclaw.workflows.intent import Intent
from personalclaw.workflows.loop_run_map import (
    DEF,
    DEST_KINDS,
    DIRECT_PATH_KINDS,
    INTENT,
    LOOP_FIELD_MAP,
    NODE_CONFIG,
    NONE,
    POLICY,
    PROJECTION,
    RUN,
    RUN_INPUT,
    STATUS_VOCABULARY_DELTA,
)
from personalclaw.workflows.models import Node, RunStatus, WorkflowDef, WorkflowRun
from personalclaw.workflows.supervisor_policy import SupervisorPolicy

_BUNDLED = Path(__file__).resolve().parent.parent / "src" / "personalclaw" / "workflows" / "bundled"

#: The fields PP-16 must decide about before `loop/store.py` can be retired. Pinned EXACTLY: this
#: set shrinks as the atom lands. Growing it is a deliberate act that has to be argued for here.
_EXPECTED_HOMELESS = {
    "name",
    "provider_agent",
    "strategy_id",
    "strategy_config",
    "auto_teardown_on_complete",
    "tasks_project_id",
    # `AG-14` ceilings/classification: v2 has no run-wide time budget and no closed run-level
    # stop reason (see the two NONE rows' consequence notes). Argued in the AG-14 change itself.
    "deadline_secs",
    "stop_reason",
}


def _resolve(root: type, path: str) -> Any:
    """Resolve a dotted `Cls.field.subfield` path against dataclass declarations.

    Declaration-only on purpose: `Loop` and friends take required args, so constructing them is not
    an option (the same reason `supervisor_policy._field_default` reads `fields()`). Raises
    `AttributeError` on anything it cannot resolve — a resolver that shrugged would make every row
    pass.
    """
    head, _, rest = path.partition(".")
    assert head == root.__name__, f"{path} does not start at {root.__name__}"
    cur: Any = root
    for part in rest.split("."):
        if not is_dataclass(cur):
            raise AttributeError(f"{path}: {cur!r} is not a dataclass, cannot resolve {part!r}")
        match = [f for f in fields(cur) if f.name == part]
        if not match:
            raise AttributeError(f"{path}: {cur.__name__} has no field {part!r}")
        cur = match[0].type
        # Dataclass field types come back as strings under `from __future__ import annotations`;
        # resolve the ones we need to walk into through the declaring module's namespace.
        if isinstance(cur, str):
            cur = _ROOTS_BY_NAME.get(cur.split("[")[0].strip(), cur)
    return cur


_ROOT_FOR_KIND: dict[str, type] = {
    RUN: WorkflowRun,
    POLICY: SupervisorPolicy,
    DEF: WorkflowDef,
    INTENT: Intent,
}

#: Nested dataclasses the resolver walks into (field types are strings under PEP 563).
_ROOTS_BY_NAME: dict[str, type] = {}


def _load_nested_types() -> None:
    from personalclaw.guardrails.policy import SafetyProfile
    from personalclaw.workflows.models import RunBudget, RunDefaults

    _ROOTS_BY_NAME.update(
        {
            "RunDefaults": RunDefaults,
            "RunBudget": RunBudget,
            "SafetyProfile": SafetyProfile,
        }
    )


_load_nested_types()


def _template_inputs() -> dict[str, set[str]]:
    """Every bundled template's declared input parameter names."""
    out: dict[str, set[str]] = {}
    for spec in sorted(_BUNDLED.glob("*/workflow.json")):
        data = json.loads(spec.read_text(encoding="utf-8"))
        out[spec.parent.name] = set((data.get("inputs") or {}).keys())
    return out


def test_the_map_covers_every_loop_field_exactly_once():
    rows = {r.field for r in LOOP_FIELD_MAP}
    assert len(rows) == len(LOOP_FIELD_MAP), "duplicate rows in LOOP_FIELD_MAP"
    declared = set(Loop.__dataclass_fields__)
    assert declared, "Loop declares no fields — import drift?"
    missing = declared - rows
    stale = rows - declared
    assert not missing, (
        f"Loop fields with no row in LOOP_FIELD_MAP: {sorted(missing)}. An unmapped field is a "
        "feature about to be dropped silently when loop/store.py is retired — give it a home or "
        "record it as NONE with the consequence."
    )
    assert not stale, f"LOOP_FIELD_MAP rows naming no Loop field: {sorted(stale)}"


def test_every_declared_destination_resolves():
    resolved = 0
    for row in LOOP_FIELD_MAP:
        assert row.dest_kind in DEST_KINDS, f"{row.field}: unknown dest_kind {row.dest_kind!r}"
        if row.dest_kind in DIRECT_PATH_KINDS:
            _resolve(_ROOT_FOR_KIND[row.dest_kind], row.dest)
            resolved += 1
        elif row.dest_kind in {PROJECTION, NONE}:
            assert not row.dest, f"{row.field}: {row.dest_kind} rows carry no destination path"
    # Vacuity floor: if a refactor turned every row into a PROJECTION this test would pass while
    # resolving nothing. Nineteen paths resolve today (11 run + 5 policy + 2 def + 1 intent).
    assert resolved >= 15, f"only {resolved} destination paths resolved — has the map gone inert?"


def test_the_resolver_rejects_a_path_that_names_nothing():
    # Negative control for the test above: without this, a resolver that swallowed a bad attribute
    # would make every row pass forever.
    with pytest.raises(AttributeError):
        _resolve(WorkflowRun, "WorkflowRun.no_such_field")
    with pytest.raises(AttributeError):
        _resolve(SupervisorPolicy, "SupervisorPolicy.autonomy.no_such_subfield")


def test_every_run_input_destination_is_a_real_template_input():
    inputs = _template_inputs()
    assert inputs, f"no bundled templates found under {_BUNDLED} — parser drift?"
    every = set().union(*inputs.values())
    rows = [r for r in LOOP_FIELD_MAP if r.dest_kind == RUN_INPUT]
    assert rows, "no RUN_INPUT rows — has the map stopped naming template inputs?"
    for row in rows:
        assert row.dest in every, (
            f"{row.field} maps to template input {row.dest!r}, which no bundled template declares. "
            f"Declared inputs: {sorted(every)}"
        )


def test_node_config_destinations_are_config_keys_not_declared_fields():
    # `Node` deliberately keeps kind-specific fields in `config` (models.py: "Kind-specific fields
    # live in `config` rather than in a subclass"), so these rows cannot be attribute-resolved —
    # this pins the reason rather than leaving them unchecked.
    assert any(f.name == "config" for f in fields(Node)), "Node no longer has a `config` field"
    rows = [r for r in LOOP_FIELD_MAP if r.dest_kind == NODE_CONFIG]
    assert rows, "no NODE_CONFIG rows — parser drift?"
    for row in rows:
        assert (
            row.dest and "." not in row.dest
        ), f"{row.field}: a NODE_CONFIG destination is a bare config key, got {row.dest!r}"


def test_the_homeless_fields_are_pinned_and_explained():
    homeless = {r.field for r in LOOP_FIELD_MAP if r.dest_kind == NONE}
    assert homeless == _EXPECTED_HOMELESS, (
        f"the set of Loop fields with NO home changed: {sorted(homeless)} vs the pinned "
        f"{sorted(_EXPECTED_HOMELESS)}. It must SHRINK as PP-16 lands; a new homeless field is an "
        "owner decision, not a detail."
    )
    for row in LOOP_FIELD_MAP:
        if row.dest_kind == NONE:
            assert row.note.startswith(
                "NO HOME"
            ), f"{row.field}: a homeless row must open with NO HOME and name the consequence"
        assert len(row.note) > 40, f"{row.field}: the note is too thin to be evidence"


def test_the_status_delta_is_computed_not_asserted():
    loop_values = {s.value for s in LoopStatus}
    run_values = {s.value for s in RunStatus}
    assert set(STATUS_VOCABULARY_DELTA["loop_only"]) == loop_values - run_values, (
        "STATUS_VOCABULARY_DELTA['loop_only'] no longer matches LoopStatus - RunStatus: "
        f"{sorted(loop_values - run_values)}"
    )
    assert set(STATUS_VOCABULARY_DELTA["run_only"]) == run_values - loop_values, (
        "STATUS_VOCABULARY_DELTA['run_only'] no longer matches RunStatus - LoopStatus: "
        f"{sorted(run_values - loop_values)}"
    )
    # Vacuity floor: an empty delta would satisfy the equalities above if both enums converged, and
    # that is exactly the day this map's status row stops being a decision — so say so out loud.
    assert loop_values & run_values, "the two status vocabularies now share nothing — impossible?"


def test_the_declaration_says_it_is_inert():
    # The WF2LOO-12 honesty marker: a control with no caller must SAY it has no caller. If someone
    # wires this map at runtime, this test is the reminder to delete the claim in the same change.
    assert "Deliberately inert" in (loop_run_map.__doc__ or "")

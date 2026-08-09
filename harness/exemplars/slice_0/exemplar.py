"""Slice 0 exemplar — data model, spec-ingestion validator, and the binding grammar.

Slice 0 is the foundation the engine stands on: the `Node`/`WorkflowRun` model, the
never-throw structural validator (typed issues + Kahn level grouping), and the binding
resolver. This exemplar drives all three WITHOUT the engine — Slice 0 is pre-engine, so its
mechanism is exercised directly rather than through a run:

1. a well-formed spec validates clean and the validator returns its concurrency levels;
2. a spec that references a non-existent node id is FLAGGED (a typed issue, not an
   exception) — the validator never throws, because its output goes straight back to an
   author;
3. the binding grammar threads a value: `{{nodes.a.output.n}}` resolves against a context,
   and whole-value refs preserve their source type.

Runnable standalone: `python -m harness.exemplars.slice_0.exemplar` (or `smoke.sh`).
`main()` self-asserts and returns 0 when all three hold, non-zero otherwise.
"""

from __future__ import annotations

from typing import Any

from personalclaw.workflows.bindings import BindingContext, resolve
from personalclaw.workflows.validator import validate_spec

#: A valid two-node sequence: `gather` produces a value, `report` binds it. Structurally
#: sound and acyclic, so the validator returns concurrency levels and no errors.
VALID_SPEC: dict[str, Any] = {
    "name": "slice0-valid",
    "root": {
        "kind": "sequence",
        "id": "root",
        "children": [
            {"kind": "transform", "id": "gather", "config": {"expr": {"n": 3}}},
            {
                "kind": "transform",
                "id": "report",
                "config": {"expr": "n={{nodes.gather.output.n}}"},
            },
        ],
    },
}

#: The same shape, but `report` binds a node id that does not exist. A run would fail at
#: ready-time with a BindingError; the validator catches it now as `WF_UNKNOWN_NODE_REF`.
INVALID_SPEC: dict[str, Any] = {
    "name": "slice0-dangling-ref",
    "root": {
        "kind": "sequence",
        "id": "root",
        "children": [
            {"kind": "transform", "id": "gather", "config": {"expr": {"n": 3}}},
            {"kind": "transform", "id": "report", "config": {"expr": "{{nodes.ghost.output}}"}},
        ],
    },
}


def main() -> int:
    # 1. The valid spec validates clean and yields concurrency levels.
    good = validate_spec(VALID_SPEC)
    if not good.ok:
        print(f"FAIL: expected the valid spec to pass, got issues: {good.summary()}")
        return 1
    if not good.levels:
        print("FAIL: a valid acyclic spec should produce Kahn concurrency levels")
        return 1

    # 2. The dangling reference is flagged — a typed issue, and the validator did not throw.
    bad = validate_spec(INVALID_SPEC)
    if bad.ok:
        print("FAIL: expected the dangling node reference to be flagged")
        return 1
    if not any(i.code == "WF_UNKNOWN_NODE_REF" for i in bad.errors):
        print(f"FAIL: expected a WF_UNKNOWN_NODE_REF error, got: {bad.summary()}")
        return 1

    # 3. The binding grammar threads a value and preserves the source type on a whole-value ref.
    ctx = BindingContext(node_outputs={"a": {"n": 42, "items": [1, 2, 3]}})
    interpolated = resolve("count is {{nodes.a.output.n}}", ctx)
    if interpolated != "count is 42":
        print(f"FAIL: expected interpolation 'count is 42', got {interpolated!r}")
        return 1
    whole = resolve("{{nodes.a.output.items}}", ctx)
    if whole != [1, 2, 3]:
        print(f"FAIL: a whole-value binding must preserve the list type, got {whole!r}")
        return 1

    print(
        "PASS slice_0: the validator passed a sound spec (with concurrency levels), flagged "
        "a dangling node reference as a typed issue without throwing, and the binding grammar "
        "threaded a value while preserving whole-value types."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

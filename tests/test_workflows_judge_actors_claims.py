"""The rail for `judge_actors`' enforcement claim (WF2LOO-15).

`judge_actors` opens by describing two invariants. Until this rail, it said the engine
"ENFORCES" both — and one of them had no production caller at all, because the state
machine has no ACTOR at a node transition to check. A module that claims a guardrail it
does not run is worse than one that admits the gap: an auditor reading it stops looking.

This test pins the claim to the code IN BOTH DIRECTIONS. It fails when:

* the isolation seam loses its live caller (the half that IS enforced), or
* `check_transition` / `resolve_transition` GAIN one and the docstring still says they
  have none — i.e. someone wires the rule and forgets to correct the text.

It deliberately does NOT assert that the transition rule stays unwired. Wiring it is
`WF2LOO-13`'s scope; this rail only requires the prose and the call graph to agree.
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"
MODULE = SRC / "workflows" / "judge_actors.py"

#: Functions whose live-caller count the docstring makes a claim about.
_ENFORCED = ("plan_judge_session", "validate_judge_model")
_AUTHORED = (
    "check_transition",
    "resolve_transition",
    "blind_provenance",
    "assemble_judge_evidence",
)

#: The phrase the docstring must carry while `_AUTHORED` has no caller.
_UNWIRED_MARKER = "AUTHORED, NOT ENFORCED"


def _production_callers(name: str) -> set[str]:
    """Files under `src/` that reference `name`, excluding its own module.

    A reference is enough: this rail asks "does production reach this symbol at all",
    which is exactly the question the docstring answers. Import-and-call in one
    function-local statement is the shape the engine actually uses, so a call-graph
    walk would have to follow it anyway.
    """
    hits: set[str] = set()
    for path in SRC.rglob("*.py"):
        if path == MODULE:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - unreadable file is not a claim
            continue
        if name in text:
            hits.add(str(path.relative_to(SRC)))
    return hits


def test_the_module_is_parseable_and_the_symbols_exist() -> None:
    """Vacuity floor: every symbol this rail reasons about must be defined here.

    Without it, a rename would make every assertion below trivially true — the rail
    would pass by measuring nothing, which is the failure mode it exists to prevent
    elsewhere.
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    defined = {
        n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in _ENFORCED + _AUTHORED:
        assert name in defined, f"{name} is no longer defined in judge_actors"


def test_the_enforced_invariant_still_has_a_live_caller() -> None:
    """The isolation half is the one the docstring calls ENFORCED — prove it."""
    for name in _ENFORCED:
        callers = _production_callers(name)
        assert callers, (
            f"judge_actors claims {name} is enforced, but nothing under src/ references it. "
            "Either the seam regressed or the docstring's ENFORCED bullet is now false."
        )


def test_the_authored_invariant_and_the_docstring_agree() -> None:
    """The unwired half: prose and call graph must not drift apart.

    If a caller appears, the marker must go — that is the direction this rail exists for,
    because the pleasant version of this bug is someone wiring the rule and leaving a
    docstring that still calls it unwired.
    """
    doc = MODULE.read_text(encoding="utf-8")
    wired = {name: _production_callers(name) for name in _AUTHORED}
    any_wired = {n: c for n, c in wired.items() if c}
    if any_wired:
        assert _UNWIRED_MARKER not in doc, (
            "judge_actors still says "
            f"{_UNWIRED_MARKER!r} while these now have production callers: {any_wired}. "
            "Update the docstring: the rule is enforced now."
        )
    else:
        assert _UNWIRED_MARKER in doc, (
            f"{_AUTHORED} have no production caller, so the docstring must say "
            f"{_UNWIRED_MARKER!r} rather than claiming the engine enforces them."
        )

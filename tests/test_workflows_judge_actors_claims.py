"""The rail for `judge_actors`' enforcement claim (WF2LOO-15 measured it; WF2LOO-13 wired it).

`judge_actors` opens by describing two invariants. It used to say the engine "ENFORCES" both
while one of them had no production caller at all, because nothing carried an ACTOR to a node
transition. A module that claims a guardrail it does not run is worse than one that admits the
gap: an auditor reading it stops looking.

WF2LOO-13 wired the missing half — the judge gate rules on the actor behind its own terminal
transition, and assembles the judge's evidence through `assemble_judge_evidence` /
`blind_provenance` — so this rail now holds the OPPOSITE claim in place: every function here has
a live caller, and the docstring must not carry the unwired disclaimer any more. It stays a rail
rather than becoming a deletion because "authored, never run" is a state this module has been in
once already, and from inside the module it is invisible.
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

#: The phrase the docstring carried while `_AUTHORED` had no caller. It must be GONE now — if it
#: reappears, either the wiring regressed or someone copied the old notice back in, and both mean
#: the prose and the call graph have parted company again.
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


def test_the_once_authored_invariant_is_wired_and_the_docstring_agrees() -> None:
    """The formerly-unwired half: it must have a caller, and the disclaimer must be gone.

    Both halves matter. Without the caller check the module could strand the rule again and read
    as enforced; without the docstring check someone could wire it and leave prose that tells the
    next auditor not to bother looking.
    """
    doc = MODULE.read_text(encoding="utf-8")
    stranded = [name for name in _AUTHORED if not _production_callers(name)]
    assert not stranded, (
        f"{stranded} has no production caller — the worker-transition rule or the evidence "
        "blinding is authored-and-unrun again, which is what WF2LOO-15 measured and WF2LOO-13 "
        "fixed. Re-wire it, or delete the mechanism rather than leaving a rule nothing applies."
    )
    assert _UNWIRED_MARKER not in doc, (
        f"judge_actors says {_UNWIRED_MARKER!r} while every function here has a production "
        "caller. Describe what enforces what instead."
    )

"""The source-tree checks split from
``tests/test_workflows_judge_contract.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

import source_index
from workflows_judge_contract_shared import _repo_src

#: The ENFORCEMENT entry points production must reach DIRECTLY. The TYPES are deliberately
#: absent: `judge_actors` imports `Isolation` and `judge_pretier` imports `FallbackCheck`, and
#: importing a type enforces nothing.
_ENFORCEMENT_ENTRY_POINTS = (
    "validate_verdict",
    "hints_from_dict",
    "aggregate_samples",
    "judge_instruction",
    "parse_judge_json",
)


_OWNER = "workflows/judge_contract.py"


def _production_callers() -> dict[str, list[str]]:
    """Production call sites of the enforcement entry points, keyed by name.

    Substring matching on `name(` / `import name` on purpose: it over-reports rather than
    under-reports, and an over-report on THIS rail means a human re-reads the docstring.
    The owning module is excluded — a function calling its own helpers is not wiring.
    """
    found: dict[str, list[str]] = {name: [] for name in _ENFORCEMENT_ENTRY_POINTS}
    for path in source_index.package_files():
        rel = path.as_posix()
        if rel.endswith(_OWNER):
            continue
        text = source_index.text(path)
        for name in _ENFORCEMENT_ENTRY_POINTS:
            if f"{name}(" in text or f"import {name}" in text:
                found[name].append(rel)
    return {name: sites for name, sites in found.items() if sites}


def test_every_enforcement_entry_point_has_a_production_caller():
    """The INVERTED rail: it used to prove the contract was stranded, now it holds the
    wiring in place.

    Nothing in `src/` used to call this module's enforcement, and the docstring said so; now
    all six are wired through `engine.dispatch_gate`'s judge branch, the
    `apply_judge_contract` seam and the controller's `runtime_hints.judge` threading. The rail was
    inverted rather than deleted, because "the contract is authored but nothing runs it" is a state
    this module has already been in once, and it is invisible from inside the module.
    """
    source = source_index.text(_repo_src() / "personalclaw/workflows/judge_contract.py")
    # Vacuity floor: a rail scanning for names that no longer exist passes forever on an
    # empty match set. Every entry point must still be a function in the owning module.
    missing = [name for name in _ENFORCEMENT_ENTRY_POINTS if f"def {name}(" not in source]
    assert not missing, (
        f"this rail scans for enforcement functions judge_contract no longer defines: {missing}"
        " — retarget the list, do not let it match nothing"
    )

    wired = _production_callers()
    stranded = [name for name in _ENFORCEMENT_ENTRY_POINTS if name not in wired]
    assert not stranded, (
        f"{stranded} lost every production caller — the judge contract is authored-and-unrun "
        "again, which is the exact defect that was measured and fixed. Re-wire it, or "
        "if the mechanism is genuinely gone, delete it rather than leaving a rule nothing applies."
    )

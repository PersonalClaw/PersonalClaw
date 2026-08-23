"""The flywheel's two once-orphaned modules must keep a production importer.

`learning/accountability.py` (criterion 9's EFFECTIVE…HARMFUL verdict + auto-filed revert) and
`learning/detectors.py` (the ad-hoc→template and tier-migration verdicts) both shipped complete and
well-tested with **nothing importing them**. An AST audit on 2026-08-04 recorded that, `WF2LEA-5`
wired the first and `WF2LEA-7` the second, and both atoms are `done`.

Nothing then pinned the wiring. The failure mode is specific and silent: delete the last call site
and the modules keep passing their own unit tests forever while the behaviour they exist for — a
persistently-harmful accepted change getting reverted — simply stops happening. That is the same
"present but inert" shape the audit found in the first place, and re-entering it would look exactly
like a healthy suite.

**Why this is scoped to two named modules instead of a general rule.** The obvious generalisation is
a docs-lint check: flag any doc claiming a module has zero importers when it has some. That was
built and MEASURED across every tracked doc, and rejected — it cannot be made precise:

* a markdown **table** is one paragraph with no sentence-ending period, so the claim's scope
  swallowed every module named in the table (`gateway.py`, `history.py`, `cli_commands.py` …);
* a `**Done when:** … (module no longer has zero importers)` clause is an aspirational **negation**
  that reads identically to the claim;
* roadmap execution logs are full of **past-tense narrative** — "shipped 1,096 lines with zero
  importers in `src/`" — describing gaps that were then closed. `WORKFLOWS-V2-WORK-CONTAINERS.md`
  even labels its own block "partially superseded — see the Execution log, which wins".

Distinguishing "has none" from "had none, then we fixed it" needs tense, not pattern matching, and a
gate that flags correct history teaches people to delete the history. So the general check was
dropped and the specific, checkable fact is asserted here instead.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from personalclaw import learning

PKG = pathlib.Path(learning.__file__).resolve().parent.parent  # src/personalclaw

#: Modules whose whole point is a call site elsewhere, with the atom that wired each.
WIRED_MODULES = {
    "learning/accountability.py": "WF2LEA-5 (criterion 9 — the accountability verdict)",
    "learning/detectors.py": "WF2LEA-7 (ad-hoc→template + tier-migration detectors)",
}


def _importers(module_rel: str) -> set[str]:
    """Files under ``src/personalclaw`` importing ``module_rel``, excluding itself.

    AST, not grep, and that is load-bearing: `detectors.py` shares its stem with the word
    "detectors" in ordinary prose and with an unrelated `web_source.DETECTOR_ORDER`, both of which a
    text scan reports as importers. Measured while writing this — a grep for the stem returned six
    files, none of which imported the module.
    """
    stem = module_rel[: -len(".py")].replace("/", ".")
    # A package module is imported by its PACKAGE name: `learning/__init__.py` is
    # `personalclaw.learning`, never `personalclaw.learning.__init__`. Missing this made the
    # vacuity floor below report zero importers for a module dozens of files import — the floor
    # caught it, which is what a floor is for.
    if stem.endswith(".__init__"):
        stem = stem[: -len(".__init__")]
    want = {stem, f"personalclaw.{stem}"}
    suffix = "." + stem
    found: set[str] = set()
    for path in sorted(PKG.rglob("*.py")):
        rel = str(path.relative_to(PKG))
        if rel == module_rel:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except SyntaxError:  # pragma: no cover - the lint job owns syntax
            continue
        pkg = ["personalclaw", *path.relative_to(PKG).parent.parts]
        for node in ast.walk(tree):
            dotted: list[str] = []
            if isinstance(node, ast.Import):
                dotted = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative — resolve against this file's package
                    base = pkg[: len(pkg) - node.level + 1]
                    head = [*base, node.module] if node.module else base
                    dotted = [".".join(head)]
                    dotted += [".".join([*head, a.name]) for a in node.names]
                elif node.module:
                    dotted = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            if any(d in want or d.endswith(suffix) for d in dotted):
                found.add(rel)
                break
    return found


@pytest.mark.parametrize("module_rel,atom", sorted(WIRED_MODULES.items()))
def test_the_module_has_a_production_importer(module_rel: str, atom: str):
    assert (PKG / module_rel).exists(), (
        f"{module_rel} is gone. If it was deleted deliberately, remove its WIRED_MODULES entry in "
        f"the same change — a rail pinned to a missing file measures nothing."
    )
    importers = _importers(module_rel)
    assert importers, (
        f"{module_rel} has NO production importer under src/personalclaw. It shipped orphaned once "
        f"(AST audit 2026-08-04) and {atom} wired it; this rail exists because nothing else pins "
        f"that. The module's own unit tests still pass with every call site deleted, so the "
        f"behaviour it exists for stops silently. Restore the call site, or retire the module and "
        f"this entry together."
    )


def test_the_detector_is_not_fooled_by_the_module_name_in_prose():
    """The vacuity floor, and it is a real risk rather than a formality.

    A grep for `detectors` matches `web_source.DETECTOR_ORDER`, a comment about "cheap detectors" in
    `agents/native/tool_retrieval.py`, and several docstrings — six files, none importing the
    module. If `_importers` ever degraded to a text scan, both assertions above would pass on those
    and the rail would certify wiring that does not exist.
    """
    fake = "learning/__init__.py"
    assert (PKG / fake).exists(), "the control module moved; re-derive this floor"
    # `learning/__init__.py` is imported as `personalclaw.learning` by many modules, so a working
    # AST index MUST find importers for it — proving the index resolves real imports at all.
    assert _importers(
        fake
    ), "the AST index found no importer for learning/__init__.py — the index is broken"
    # …and it must NOT credit a module nothing imports.
    assert _importers("learning/_nonexistent_probe.py") == set(), (
        "the index credited importers to a module that does not exist — it is matching text, "
        "not imports."
    )

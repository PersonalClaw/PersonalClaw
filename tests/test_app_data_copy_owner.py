"""ONE owner for "is there an earlier copy of this app's data still on disk?" (#2585).

``app_manager`` keeps the user's app ``data/`` in two places that are not the live app
tree: the parked copy at ``apps/.{name}.data`` and the keep-data uninstall's quarantine
stage at ``apps/.quarantine/{name}.data.staged``. Neither carries any label. Every bug in
the #2574/#2579/#2585 family is the same mistake about them — a site decided which of the
two was authoritative, or which was garbage, from the ORDER functions happen to be called
in rather than by asking. Four distinct data-loss shapes came out of that one habit, three
of them returning ``True``.

The fix is structural, so the rail has to be structural too. Two claims are pinned here:

1. **The census.** Every call in ``app_manager`` that destroys or overwrites one of those
   two paths is DERIVED from the source by AST, not hand-listed, and the derived set must
   equal the reviewed one exactly. A new site reds (someone taught a fifth place to delete
   a copy); a vanished site reds too (the guarantee
   :func:`~personalclaw.apps.app_manager._restore_preserved_data` relies on — that a
   parked dir exists only if the whole copy landed — is exactly "``uninstall_keep_data``
   writes that path once, with a rename", and it stops holding the moment that line
   changes shape).
2. **No second module** reaches those paths at all. They are module-private, and the way
   this family would grow a fifth member is a new caller elsewhere importing the helper.

A census that matched nothing would satisfy both claims vacuously, so its own floor is
asserted (``test_the_census_is_not_vacuous``) and its firing is proven BY EXECUTION on
planted bypasses every run (``test_the_census_reds_on_a_planted_bypass``,
``test_the_census_reds_on_a_planted_bypass_behind_a_local_alias``) — including one hidden
behind a local variable, which is the only form any real site actually takes, and two more
for the one construct that can hide a site from the census without deleting it: a
module-private helper the path is passed INTO
(``test_a_declared_helper_is_seen_at_its_call_site``,
``test_a_declared_helpers_path_parameter_is_really_tracked``).
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "apps" / "app_manager.py"
SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: Names whose value IS one of the two copy paths (or, for ``_restore_preserved_data``,
#: returns one). An expression mentioning any of them taints what it is assigned to.
TAINT_SOURCES = frozenset(
    {
        "_preserved_data_dir",
        "_data_stage_dir",
        "_PRESERVED_DATA_SUFFIX",
        "_DATA_STAGE_SUFFIX",
        "_restore_preserved_data",
    }
)

#: Module-level callees that destroy or overwrite, and WHICH argument they do it to.
#: ``shutil.move``/``os.rename`` consume argument 0 as well as overwriting argument 1, so
#: both count; ``copytree``'s destination is argument 1 while its source is only read.
DESTRUCTIVE_CALLS = {
    "shutil.rmtree": {0},
    "shutil.move": {0, 1},
    "shutil.copytree": {1},
    "os.rename": {0, 1},
    "os.replace": {0, 1},
    "os.rmdir": {0},
    "os.remove": {0},
    "os.unlink": {0},
}
#: Module-private helpers that receive one of the two copy paths as a PARAMETER, and in
#: which argument position. Declared, never inferred, because a helper is exactly how a
#: reviewed site stops being derivable: the census keys on ``(enclosing function, callee,
#: argument position)`` and tracks taint only WITHIN a function, so a path that arrives as a
#: parameter is untainted and every primitive the helper runs on it goes unseen (#3324 moved
#: ``uninstall_keep_data``'s ``copytree`` into ``_copy_live_tree`` and the census went from
#: six sites to five with nothing added — a still-present copy reading as a deleted one).
#:
#: Each entry buys TWO things, and both are needed to replace what the inlined call gave us:
#:
#: * the CALL is a destroy/overwrite of that argument, so a caller that hands the helper a
#:   different path — or the same paths in the other order — reds at the call site;
#: * inside the helper that parameter name is a taint source, so the primitives it actually
#:   runs (``copytree`` onto it, ``rmtree`` of a partial attempt) are enumerated like any
#:   other site, rather than being trusted because they are one call deeper.
#:
#: ``_copy_live_tree`` takes a copy path as its DESTINATION only; its source is always the
#: live app tree (``live_data`` / ``old_data``), which is not one of the two paths.
PATH_TAKING_HELPERS = {"_copy_live_tree": {1: "dst"}}

DESTRUCTIVE_CALLS.update({fn: set(params) for fn, params in PATH_TAKING_HELPERS.items()})

#: Methods that destroy or overwrite their RECEIVER.
DESTRUCTIVE_METHODS = frozenset({"rename", "replace", "unlink", "rmdir"})

#: The functions allowed to hold one of these decisions at all.
OWNERS = frozenset({"_discard_preserved_data", "install", "uninstall_keep_data", "_copy_live_tree"})

#: The reviewed census. ``(function, callee, roles) -> how many times``.
#:
#: * ``_discard_preserved_data`` / ``rmtree(target)`` — the deliberate eradicate, called by
#:   ``force_uninstall``. The one site whose job IS to destroy a parked copy. Reaching it
#:   from the keep-data rung while an unconsumed park existed was the third shape in the
#:   family; ``uninstall_keep_data``'s refusal, not a change here, is what stops that.
#: * ``install`` / ``rmtree(parked)`` — GC after the restore is past rollback. Safe because
#:   the copy has just been reproduced inside the app tree; a FAILED restore returns
#:   ``None`` instead, so this never runs on a copy that was not consumed.
#: * ``uninstall_keep_data`` / ``_copy_live_tree(live_data, staged)`` — mints the stage. The
#:   only writer of that path in the codebase. It delegates to ``_copy_live_tree`` rather
#:   than calling ``copytree`` inline because a LIVE app's ``data/`` can change under the
#:   walk (#3324); the two entries below are that helper's own primitives, so the delegation
#:   and what it delegates to are BOTH reviewed and neither is trusted by being one call
#:   deeper. This entry is what reds if the arguments are ever swapped.
#: * ``_copy_live_tree`` / ``copytree(src, dst)`` — the copy itself. Writes only the
#:   destination it was handed; the source is the live tree and is read-only here.
#: * ``_copy_live_tree`` / ``rmtree(dst)`` — clears a PARTIAL destination between retries.
#:   Safe because it acts on a tree this same call created moments earlier and abandoned,
#:   never on a last copy: the only path in is a ``copytree`` that raised, and the caller's
#:   ``live_data`` is still on disk throughout. On the final attempt it re-raises instead,
#:   so the caller's fail-closed branch is unchanged.
#: * ``uninstall_keep_data`` / ``rmtree(staged)`` ×2 — the two fail-closed cleanups
#:   (preserve failed; ``force_uninstall`` refused). Both act on a stage THIS call created
#:   while ``live_data`` is still on disk, which is only true because the rung refuses when
#:   a stage already exists — that is what makes them redundant duplicates rather than last
#:   copies (#2574).
#: * ``uninstall_keep_data`` / ``.rename`` — the atomic park. Destination provably absent,
#:   same filesystem, so a parked copy is complete BY CONSTRUCTION (#2585).
EXPECTED = Counter(
    {
        ("_discard_preserved_data", "shutil.rmtree", ("arg0",)): 1,
        ("install", "shutil.rmtree", ("arg0",)): 1,
        ("uninstall_keep_data", "_copy_live_tree", ("arg1",)): 1,
        ("_copy_live_tree", "shutil.copytree", ("arg1",)): 1,
        ("_copy_live_tree", "shutil.rmtree", ("arg0",)): 1,
        ("uninstall_keep_data", "shutil.rmtree", ("arg0",)): 2,
        ("uninstall_keep_data", ".rename", ("recv",)): 1,
    }
)


def _idents(expr: ast.AST) -> set[str]:
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", ast.unparse(expr)))


def census(source: str) -> list[tuple[tuple[str, str, tuple[str, ...]], str, int]]:
    """Every destroy/overwrite of a data-copy path in *source*, derived by AST.

    Returns ``(key, how_the_path_was_named, lineno)``. ``how`` is ``"literal"`` when the
    argument spells a taint source itself and ``"alias"`` when it is a local name assigned
    from one — the distinction the vacuity floor leans on, since every real site is an
    alias and a tracker that only matched literals would find nothing.
    """
    tree = ast.parse(source)
    found: list[tuple[tuple[str, str, tuple[str, ...]], str, int]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Intra-function taint: names bound to an expression naming a taint source, then
        # transitively to those names. Bounded and order-dependent on purpose — it models
        # "the local variable holding this path", which is all any site here does. Seeded
        # with a declared helper's path PARAMETERS, the one case where the path does not
        # arrive through an assignment at all.
        tainted: set[str] = set(PATH_TAKING_HELPERS.get(fn.name, {}).values())
        for node in ast.walk(fn):
            value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
            if value is None:
                continue
            if not _idents(value) & (TAINT_SOURCES | tainted):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                tainted |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}

        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            spelling = ast.unparse(node.func)
            roles: list[str] = []
            how = ""
            if spelling in DESTRUCTIVE_CALLS:
                for pos in sorted(DESTRUCTIVE_CALLS[spelling]):
                    if pos >= len(node.args):
                        continue
                    names = _idents(node.args[pos])
                    if names & TAINT_SOURCES:
                        roles.append(f"arg{pos}")
                        how = "literal"
                    elif names & tainted:
                        roles.append(f"arg{pos}")
                        how = how or "alias"
            elif isinstance(node.func, ast.Attribute) and node.func.attr in DESTRUCTIVE_METHODS:
                spelling = f".{node.func.attr}"
                names = _idents(node.func.value)
                if names & TAINT_SOURCES:
                    roles, how = ["recv"], "literal"
                elif names & tainted:
                    roles, how = ["recv"], "alias"
            if roles:
                found.append(((fn.name, spelling, tuple(roles)), how, node.lineno))
    return found


def test_every_destroy_of_a_data_copy_is_a_reviewed_site():
    """The derived census equals the reviewed one — no additions, no disappearances."""
    sites = census(SRC.read_text(encoding="utf-8"))
    got = Counter(key for key, _how, _line in sites)
    assert got == EXPECTED, (
        "the set of places that destroy or overwrite a copy of an app's data/ changed.\n"
        f"  new/extra: {got - EXPECTED}\n  missing:   {EXPECTED - got}\n"
        "A NEW entry means a site is deciding on its own that a copy of the user's data "
        "is redundant; route it through _unconsumed_data_copies instead. A MISSING entry "
        "means the guarantee _restore_preserved_data documents (a parked dir exists only "
        "if the whole copy landed, because the park is one rename) may no longer hold.\n"
        f"  derived from: {[(k, ln) for k, _h, ln in sites]}"
    )


def test_every_such_site_lives_in_a_function_declared_to_own_the_decision():
    sites = census(SRC.read_text(encoding="utf-8"))
    strays = sorted({key[0] for key, _how, _line in sites} - OWNERS)
    assert not strays, (
        f"{strays} destroy or overwrite a copy of an app's data/ but are not declared "
        "owners of that decision"
    )


def test_the_census_is_not_vacuous():
    """The floor. A census that matched nothing would pass both claims above.

    ``EXPECTED`` is not the floor — it is derived from the same parse, so an ``EXPECTED``
    edited down to ``{}`` alongside a broken parser would still be "equal". These bounds
    are absolute: at least the eight known real sites, and at least three of them resolved
    through the ALIAS path, because every real one is a local variable (or a declared
    helper's path parameter) and a tracker that only matched literal
    ``_preserved_data_dir(...)`` in an argument would score zero.

    The floor is a RATCHET, so it moves only upward and only with a reviewed site behind
    each step: it was six until #3324 moved the stage's ``copytree`` into
    ``_copy_live_tree``, and it is eight because that helper's delegation plus its two own
    primitives are now all enumerated. Lowering it to match a census that stopped matching
    something is how this rail dies green.
    """
    sites = census(SRC.read_text(encoding="utf-8"))
    assert len(sites) >= 8, f"the census found only {len(sites)} sites; it has gone blind"
    aliased = [s for s in sites if s[1] == "alias"]
    assert len(aliased) >= 3, (
        f"only {len(aliased)} sites resolved through the alias tracker; a literal-only "
        "match would silently miss every real call site"
    )
    assert {k[0] for k, _h, _l in sites} == OWNERS, (
        "the census no longer reaches every owning function, so it cannot be reading the "
        "real file"
    )


#: The planting site for a bypass: a real function that is NOT an owner, so a plant reds the
#: owner claim as well as the census one.
ENABLE_MARKER = 'def enable(name: str, *, caller: str = "app_manager") -> bool:'
#: The planting site for proving a declared helper's path parameter is really tracked.
HELPER_MARKER = "def _copy_live_tree(src: Path, dst: Path) -> None:"


def _plant(source: str, statement: str, marker: str = ENABLE_MARKER) -> str:
    """Insert *statement* as the first line of *marker*'s body (default ``enable``)."""
    assert marker in source, "the planting site moved; pick another non-owner function"
    head, _, tail = source.partition(marker)
    body_start = tail.index("\n") + 1
    planted = head + marker + tail[:body_start] + f"    {statement}\n" + tail[body_start:]
    assert len(planted) > len(source), "the plant did not change the source"
    ast.parse(planted)  # a bypass the rail cannot see because it does not parse is no proof
    return planted


@pytest.mark.parametrize(
    "statement,label",
    [
        ("shutil.rmtree(_preserved_data_dir(name))", "literal"),
        ("_leftover = _preserved_data_dir(name); shutil.rmtree(_leftover)", "alias"),
    ],
    ids=["literal", "behind-a-local-alias"],
)
def test_the_census_reds_on_a_planted_bypass(statement, label):
    """Proven by EXECUTION, every run: a fifth deleter is seen and reds both claims.

    The alias case is the one that matters — it is the shape every real site takes, so a
    tracker that only saw literals would pass this file while missing the whole codebase.
    """
    planted = census(_plant(SRC.read_text(encoding="utf-8"), statement))
    new = Counter(key for key, _how, _line in planted) - EXPECTED
    assert new == Counter(
        {("enable", "shutil.rmtree", ("arg0",)): 1}
    ), f"the planted {label} bypass was invisible to the census: {new}"
    hows = {how for key, how, _line in planted if key[0] == "enable"}
    assert hows == {label}, f"the bypass was found, but by the wrong route: {hows}"
    assert "enable" not in OWNERS  # so the owner claim reds too


def test_a_declared_helper_is_seen_at_its_call_site():
    """The first half of a ``PATH_TAKING_HELPERS`` entry, proven by execution.

    A helper that overwrites the path it is handed has to red at the CALL, or swapping its
    arguments — handing the live tree where the copy belongs — would be invisible: the
    helper's own body is keyed on parameter names and looks identical either way.
    """
    planted = census(
        _plant(SRC.read_text(encoding="utf-8"), "_copy_live_tree(name, _preserved_data_dir(name))")
    )
    new = Counter(key for key, _how, _line in planted) - EXPECTED
    assert new == Counter(
        {("enable", "_copy_live_tree", ("arg1",)): 1}
    ), f"a declared path-taking helper was invisible at its call site: {new}"
    assert "enable" not in OWNERS  # so the owner claim reds too


def test_a_declared_helpers_path_parameter_is_really_tracked():
    """The second half, proven by execution: a new destroy INSIDE the helper is seen.

    This is the hole #3324 opened — the census tracks taint within a function, so a path
    arriving as a parameter is untainted and every primitive run on it is unseen. Seeding
    the declared parameter closes it, and this plant is what proves the seeding is live
    rather than the entry merely being spelled in a dict.
    """
    planted = census(
        _plant(SRC.read_text(encoding="utf-8"), "shutil.rmtree(dst)", marker=HELPER_MARKER)
    )
    new = Counter(key for key, _how, _line in planted) - EXPECTED
    assert new == Counter(
        {("_copy_live_tree", "shutil.rmtree", ("arg0",)): 1}
    ), f"a destroy of the copy path inside a declared helper was invisible: {new}"
    hows = {how for key, how, _line in planted if key[0] == "_copy_live_tree"}
    assert hows == {"alias"}, f"the helper's sites were found, but by the wrong route: {hows}"


def test_no_other_module_reaches_the_data_copy_paths():
    """The paths are module-private, and a fifth family member would start by importing one.

    A count floor comes with it: ``app_manager`` itself must mention every token, so the
    scan cannot be green because the names were renamed out from under it.
    """
    tokens = sorted(TAINT_SOURCES - {"_restore_preserved_data"}) + ["_restore_preserved_data"]
    own = SRC.read_text(encoding="utf-8")
    missing = [t for t in tokens if t not in own]
    assert not missing, f"{missing} no longer exist in app_manager; this scan is checking air"

    offenders: dict[str, list[str]] = {}
    scanned = 0
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path == SRC:
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = [t for t in tokens if t in text]
        if hits:
            offenders[str(path.relative_to(SRC_ROOT))] = hits
    assert scanned > 100, f"only {scanned} modules scanned; the walk is not finding the tree"
    assert not offenders, (
        "a module outside app_manager reaches an app-data copy path directly: "
        f"{offenders}. Route the decision through app_manager._unconsumed_data_copies "
        "instead of deciding about the copy from another module."
    )

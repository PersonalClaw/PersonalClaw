"""No decision layer in ``gate_policy`` may be unreachable from production (#375).

``gate_policy`` accumulated a whole documented contract nobody called:
``evaluate_event_gate`` plus ``HoldState``, ``HoldVerdict`` and ``DEFAULT_EVENT_HOLD_LIMIT``
were written, described in the module docstring as a load-bearing rule, unit-tested at five
cases — and the only thing that ever named them outside the module was an annotation on a
dict the controller allocated and never touched. A unit suite over an unreachable function
is a rail that certifies nothing, which is why the deletion needed a *reachability* rail
rather than another behaviour test.

**Why this is not "assert the function is gone".** A test that asserted
``not hasattr(gate_policy, "evaluate_event_gate")`` would pass forever and catch nothing:
it names one symbol, so the next dead policy layer added to this module walks straight past
it. This computes the property instead — which public names production can actually reach —
so it fires on a name that does not exist yet.

## How reachability is computed

1. **Roots**: public names of ``gate_policy`` that some *other* module under
   ``src/personalclaw`` actually references. Collected by AST, never by grep, so a mention
   in a comment or a docstring cannot make a dead name look live (that is one of the three
   ways a grep rail goes vacuous; the other two are covered by the falsification cases at
   the bottom).
2. **Candidate files are prefiltered on the literal ``gate_policy``**, which is COMPLETE
   rather than heuristic: every way to reach a name in this module —
   ``from personalclaw.workflows import gate_policy``, ``... import gate_policy as gp``,
   ``from personalclaw.workflows.gate_policy import X``, ``import_module("...gate_policy")``
   — puts that substring in the importing file.
3. **Transitive closure inside the module**: a name referenced from a reachable
   definition's own body is reachable. This is what keeps ``PolicyVerdict`` and ``Decision``
   — legitimately never named by a caller, because they are what ``decide`` *returns*.

## Shrink-only, and seeded at the MEASURED population

``KNOWN_UNREACHABLE`` is one name, not zero, and that is deliberate: PHF-6's ruling is that
a never-run gate given teeth at zero reds every pre-existing case at once, which is an
outage. Adding a name here is FORBIDDEN — the fix for a red is to wire the code or delete
it, exactly as #375 was resolved. Removing one is the point.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

MODULE_REL = "src/personalclaw/workflows/gate_policy.py"
#: Any file that can reach a name in the module contains this literal. See §2 above.
IMPORT_TOKEN = "gate_policy"

#: Public names with no production reader, measured on this commit. FORBIDDEN TO GROW.
#:
#: ``remote_timeout_decision`` — WF2-R7's "an unanswered REMOTE gate DENIES; silence is not
#: consent". Written, documented in the module docstring as a security property, and
#: referenced only by ``tests/test_workflows_gate_policy.py:179``. Found while resolving
#: #375 and left standing rather than silently wired: making a remote gate deny on timeout
#: changes what happens to a live run, which is a behaviour decision and not this deletion's
#: to make. It fails SAFE today (an unanswered gate times the run out) — but the module
#: claims a DENY verdict that nothing produces.
KNOWN_UNREACHABLE = frozenset({"remote_timeout_decision"})


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _public_top_level(tree: ast.Module) -> dict[str, ast.AST]:
    """Public module-level definitions, name -> the node that defines it."""
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                out[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    out[target.id] = node
    return out


def _defined_names(tree: ast.AST) -> set[str]:
    """Every name this file DEFINES (at any nesting depth), including attributes it sets.

    The read-side scan cannot see a definition — ``def evaluate_event_gate`` is a
    ``FunctionDef``, not a ``Name`` — so a deletion check needs both halves or a leftover
    definition with no callers reads as absent.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                found.add(node.target.id)
            elif isinstance(node.target, ast.Attribute):
                found.add(node.target.attr)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.add(target.id)
                elif isinstance(target, ast.Attribute):
                    found.add(target.attr)
    return found


def _names_referenced(node: ast.AST) -> set[str]:
    """Every identifier this subtree reads, by AST — so comments and strings cannot count.

    Both ``Name`` (a bare ``HoldState``) and ``Attribute`` (``gate_policy.HoldState``) are
    collected, because a caller reaches the module either way.
    """
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
        elif isinstance(child, ast.ImportFrom):
            for alias in child.names:
                found.add(alias.name)
    return found


def _production_files(root: Path, module_path: Path) -> list[Path]:
    return [
        path
        for path in sorted((root / "src" / "personalclaw").rglob("*.py"))
        if path != module_path and IMPORT_TOKEN in path.read_text(encoding="utf-8")
    ]


def unreachable_public_names(root: Path, module_rel: str = MODULE_REL) -> tuple[set[str], dict]:
    """Public names of ``module_rel`` no production module can reach, plus a census.

    The census rides along so the vacuity assertions can key on this scan's OWN numbers
    rather than a parallel re-walk that could drift from it.
    """
    module_path = (root / module_rel).resolve()
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    public = _public_top_level(tree)

    external: set[str] = set()
    candidates = _production_files(root, module_path)
    for path in candidates:
        external |= _names_referenced(ast.parse(path.read_text(encoding="utf-8")))

    # Transitive closure from the externally-named roots, inside the module.
    reachable = {name for name in public if name in external}
    frontier = list(reachable)
    while frontier:
        current = frontier.pop()
        for name in _names_referenced(public[current]) & set(public):
            if name not in reachable:
                reachable.add(name)
                frontier.append(name)

    census = {
        "public": len(public),
        "roots": len({n for n in public if n in external}),
        "candidate_files": len(candidates),
    }
    return set(public) - reachable, census


class TestGatePolicyHasNoUnreachableDecisionLayer:
    def test_every_public_name_is_reachable_from_production(self) -> None:
        unreachable, census = unreachable_public_names(_repo_root())
        assert unreachable <= KNOWN_UNREACHABLE, (
            "these public names in gate_policy have no production reader:\n  "
            + "\n  ".join(sorted(unreachable - KNOWN_UNREACHABLE))
            + "\n\nWire them or delete them (#375's resolution). Do NOT add them to "
            "KNOWN_UNREACHABLE — that set is shrink-only."
        )

    def test_the_known_unreachable_set_is_still_accurate(self) -> None:
        """A stale allowlist entry is a rail hiding its own success — drop it instead."""
        unreachable, _ = unreachable_public_names(_repo_root())
        assert KNOWN_UNREACHABLE <= unreachable, (
            "KNOWN_UNREACHABLE names something that IS now reachable: "
            f"{sorted(KNOWN_UNREACHABLE - unreachable)} — remove it from the set."
        )

    def test_the_deleted_event_hold_machinery_is_gone_from_the_whole_tree(self) -> None:
        """The #375 deletion, checked as an artifact rather than by absence of an attribute.

        Deleting the definitions but leaving a caller behind (or vice versa) is the "third
        state" the issue warned about, so this walks every Python file in the repo and looks
        at both halves: is the name still DEFINED anywhere, and is it still READ anywhere.

        By AST, not substring — a substring version of this test was written first and
        failed on the module docstring that *explains the deletion*, which is exactly the
        "a grep rail matches a comment" failure in its false-positive direction.
        """
        root = _repo_root()
        dead = {"evaluate_event_gate", "_event_holds", "HoldState", "HoldVerdict"}
        offenders: list[str] = []
        for area in ("src", "tests", "harness", "scripts"):
            for path in sorted((root / area).rglob("*.py")):
                if path.resolve() == Path(__file__).resolve():
                    continue  # this file names them as literals in `dead`
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                except SyntaxError:  # pragma: no cover - a deliberately broken fixture
                    continue
                live = (_names_referenced(tree) | _defined_names(tree)) & dead
                offenders += [f"{path.relative_to(root)}: {name}" for name in sorted(live)]
        assert not offenders, "the event-hold machinery is still in the tree:\n  " + "\n  ".join(
            offenders
        )


class TestTheScanCanActuallyFail:
    """Falsification. A reachability scan that flags nothing looks identical to a clean tree."""

    @staticmethod
    def _fixture(tmp_path: Path, module_src: str, caller_src: str) -> Path:
        root = tmp_path
        mod = root / MODULE_REL
        mod.parent.mkdir(parents=True, exist_ok=True)
        mod.write_text(module_src, encoding="utf-8")
        caller = root / "src" / "personalclaw" / "caller.py"
        caller.write_text(caller_src, encoding="utf-8")
        return root

    def test_a_dead_public_function_is_flagged(self, tmp_path) -> None:
        root = self._fixture(
            tmp_path,
            "def live():\n    return 1\n\n\ndef dead():\n    return 2\n",
            "from personalclaw.workflows import gate_policy\n\ngate_policy.live()\n",
        )
        unreachable, census = unreachable_public_names(root)
        assert unreachable == {"dead"}
        assert census["public"] == 2 and census["roots"] == 1

    def test_a_name_mentioned_only_in_a_COMMENT_does_not_count_as_reachable(self, tmp_path) -> None:
        """Vacuity mode two: a grep rail passes on its own documentation."""
        root = self._fixture(
            tmp_path,
            "def live():\n    return 1\n\n\ndef dead():\n    return 2\n",
            "from personalclaw.workflows import gate_policy\n"
            "# gate_policy.dead() used to be called here\n"
            '"""and gate_policy.dead is named in this docstring too."""\n'
            "gate_policy.live()\n",
        )
        unreachable, _ = unreachable_public_names(root)
        assert unreachable == {"dead"}

    def test_a_name_reachable_only_TRANSITIVELY_is_not_flagged(self, tmp_path) -> None:
        """The case that would otherwise make the rail red on `PolicyVerdict`."""
        root = self._fixture(
            tmp_path,
            "class Verdict:\n    pass\n\n\ndef decide():\n    return Verdict()\n",
            "from personalclaw.workflows import gate_policy\n\ngate_policy.decide()\n",
        )
        unreachable, _ = unreachable_public_names(root)
        assert unreachable == set()

    def test_a_module_whose_names_are_ALL_dead_is_fully_flagged(self, tmp_path) -> None:
        """Vacuity mode one: prove the scan is not silently matching nothing."""
        root = self._fixture(
            tmp_path,
            "LIMIT = 5\n\n\nclass State:\n    pass\n\n\ndef hold(s: State):\n    return LIMIT\n",
            "import os\n\nos.getcwd()\n",  # no gate_policy reference at all
        )
        unreachable, census = unreachable_public_names(root)
        assert unreachable == {"LIMIT", "State", "hold"}
        assert census["candidate_files"] == 0

    def test_the_real_scan_is_not_vacuous(self) -> None:
        """Keyed on the scan's own census, not a parallel re-walk that could drift."""
        _, census = unreachable_public_names(_repo_root())
        assert census["public"] >= 8, census
        assert census["roots"] >= 3, census
        assert census["candidate_files"] >= 2, census


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])

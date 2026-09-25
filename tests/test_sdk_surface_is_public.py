"""The published app surface exports public names only, and declares what it exports.

`personalclaw.sdk.*` is the ONLY import path an app is allowed to use, and every sdk
module's docstring makes the same promise: core can move its internals without breaking
apps. `sdk/channel.py` broke that promise in the most literal way available —

    from personalclaw.dashboard.chat import _run_chat, _save_session_to_history

— two underscore-prefixed core internals on a versioned surface, resolvable at runtime and
imported by three bundled channel apps. A name whose spelling says "private, may change
without notice" cannot also be a contract, so one of the two claims had to give.

`sdk/channel.py` was also the only sdk module with no `__all__`, which is not a
coincidence: with 45 scattered `# noqa: F401` suppressions and no declared surface, there
was nowhere for a reviewer to notice the leak. Both halves are asserted here — no private
re-exports, and every module says what it publishes — because either one alone leaves the
other free to drift.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from scripts.sdk_surface_closure import surface_closure

SDK = pathlib.Path(__file__).resolve().parent.parent / "src/personalclaw/sdk"


def _modules() -> list[pathlib.Path]:
    return sorted(p for p in SDK.glob("*.py") if p.name != "__init__.py")


def _reexported(path: pathlib.Path) -> list[str]:
    """Every name `from x import y` / `import y` binds in this module."""
    out: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                out.append(alias.asname or alias.name.split(".")[0])
    return out


def test_no_sdk_module_reexports_a_private_name():
    """The defect: `_run_chat` and `_save_session_to_history` on the app surface."""
    leaked = {
        p.name: [n for n in _reexported(p) if n.startswith("_") and not n.startswith("__")]
        for p in _modules()
    }
    leaked = {k: v for k, v in leaked.items() if v}
    assert not leaked, (
        "these sdk modules re-export underscore-prefixed core internals, so an app must "
        "import a name that declares itself unstable — promote the symbol at its "
        f"definition site or drop it from the surface: {leaked}"
    )


def test_the_scan_is_not_vacuous():
    """It must actually be reading a large surface. A parser that silently yields [] passes
    the test above for every module in the tree."""
    mods = _modules()
    assert len(mods) >= 20, f"the sdk module scan found only {len(mods)} files"
    total = sum(len(_reexported(p)) for p in mods)
    assert total >= 200, f"the re-export scan found only {total} names — it is not reading"


def test_the_scan_can_actually_fail(tmp_path):
    """The guard's own falsification: feed it the deleted line and require a hit.

    Without this, `_reexported` could return [] on any parse quirk and the rail above would
    be green forever — the exact "control exists and does not fire" shape.
    """
    probe = tmp_path / "leaky.py"
    probe.write_text(
        "from personalclaw.dashboard.chat import _run_chat, _save_session_to_history\n",
        encoding="utf-8",
    )
    found = [n for n in _reexported(probe) if n.startswith("_")]
    assert found == ["_run_chat", "_save_session_to_history"], found


def _declares_all(path: pathlib.Path) -> bool:
    """A real module-level `__all__ = [...]` binding.

    An AST walk, not `"__all__" in text`: the substring version passed with this very
    module's `__all__` deleted, because the comment above the deleted assignment still
    said the word. A rail satisfied by prose about itself is the defect class this file
    exists to catch, found by falsifying the rail rather than by reading it.
    """
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            return True
    return False


def test_every_sdk_module_declares_its_surface():
    """`channel.py` was the only module without `__all__`, and the only one that leaked."""
    missing = [p.name for p in _modules() if not _declares_all(p)]
    assert not missing, (
        "these sdk modules publish an undeclared surface, so nothing distinguishes a "
        f"deliberate export from an incidental import: {missing}"
    )


def test_channel_declares_every_name_it_reexports():
    """An `__all__` that drifts from the imports is worse than none — it reads as reviewed.

    Checked on `channel.py` specifically because it is a pure 100+ name facade whose whole
    job is the surface; the other modules define most of what they publish.
    """
    from personalclaw.sdk import channel

    declared = set(channel.__all__)
    actual = set(_reexported(SDK / "channel.py"))
    assert not (actual - declared), f"re-exported but undeclared: {sorted(actual - declared)}"
    assert not (declared - actual), f"declared but not re-exported: {sorted(declared - actual)}"


def test_the_sdk_surface_is_closed_under_its_own_signatures():
    """A published type whose own fields/signatures name UNIMPORTABLE types is not published.

    This is the defect `StructuredOutput` was one instance of: it was absent from
    `sdk.model`, so the Ollama provider recovered it as

        type(ProviderCapability.__dataclass_fields__["structured_output"].default)

    which works at runtime and defeats type checking completely. Re-exporting that ONE name
    fixed that ONE app; the measurement that mattered was how many siblings it had, and the
    answer was 25 across seven core modules — including `ToolResult.agent_error` /
    `ActionResult.agent_error` (so no app could emit a structured failure at all),
    `Task.transition(to=TaskState)` (a method no app could call type-safely) and sixteen of
    the twenty-one `apps.manifest` types on a facade whose docstring offers itself as "the
    typed contract for tooling that validates or generates a manifest".

    Stated as a rail rather than a one-time cleanup because the gap reopens silently: adding
    a field to an already-exported dataclass is a normal core change that quietly makes the
    app surface unusable, and nothing else in this file would notice.

    The walk lives in `scripts/sdk_surface_closure.py` because the inert-surface census needs
    the OTHER direction of the same relation — which of the exports a published signature
    NAMES, its only mechanically-checkable reader for a facade whose real consumers are in
    another repository. One walk, so the two halves cannot drift into two definitions of
    "published signature"; that module's docstring carries the rulings.
    """
    gaps = surface_closure().gaps
    assert not gaps, (
        "these core types are reachable from the published app surface but are not exported "
        "from any personalclaw.sdk module, so an app must derive or re-declare them:\n"
        + "\n".join(f"  {t}\n      required by {sorted(w)[0]}" for t, w in sorted(gaps.items()))
        + "\nRe-export each from the sdk submodule that already faces its owning core module."
    )


def test_the_closure_scan_is_not_vacuous():
    """Both counters, because either one at zero passes the assertion above trivially.

    `get_type_hints` raises on an unresolvable forward reference and the walk swallows that,
    so a resolution regression would empty the requirement graph rather than fail loudly —
    the `edges` floor is what makes that visible.
    """
    closure = surface_closure()
    assert closure.roots >= 100, f"the closure scan found only {closure.roots} exported classes"
    assert closure.edges >= 200, f"the closure scan resolved only {closure.edges} type references"


@pytest.mark.parametrize(
    "dropped,expected_reason",
    [
        ("personalclaw.task.TaskState", "Task."),
        ("personalclaw.errors.AgentError", "Result."),
        ("personalclaw.apps.manifest.UIConfig", "AppManifest."),
    ],
)
def test_the_closure_scan_can_actually_fail(dropped, expected_reason):
    """The falsification, run against REAL code rather than a synthetic tree.

    Un-export one name and the walk must name it again, with the field or signature that
    requires it. Three cases because the three shapes reach the walk by different paths: an
    enum used as a method parameter, a dataclass field on two different result types, and a
    nested schema type two hops from the root (`AppManifest.ui` → `UIConfig`).

    A synthetic-class control could not do this job: `core_types_in` filters on
    ``__module__.startswith("personalclaw")``, so a stand-in defined in this test file is
    invisible to it — which is precisely how a control that cannot fire gets written.
    """
    gaps = surface_closure(pretend_missing=frozenset({dropped})).gaps
    assert dropped in gaps, f"un-exporting {dropped} did not make the closure scan fail"
    assert any(expected_reason in why for why in gaps[dropped]), (
        f"{dropped} was reported, but not attributed to a {expected_reason}* "
        f"field or signature: {sorted(gaps[dropped])}"
    )


@pytest.mark.parametrize("name", ["save_session_to_history"])
def test_the_promoted_names_resolve_and_the_private_ones_are_gone(name):
    """Clean break: the public name works and the old spelling is not left behind.

    One channel app calls `save_session_to_history`, so a rename that left the underscore
    alias in place would have shipped both spellings forever — which is how the private
    name got onto the surface to begin with.

    `run_chat` was promoted here by the same change and is STILL on the facade. EA-7 wants
    it gone — it is a second route past the sender-trust gate, and the chokepoint
    (`personalclaw.channel_inbound`) is only a chokepoint if it is the only route — but four
    shipping channel apps import it from this path, and the apps repo cannot land
    atomically with core, so the removal is sequenced after they migrate. That gap is
    asserted deliberately in `tests/test_channel_inbound_chokepoint.py`, not here: this test
    is about the underscore-alias clean break, and a name whose export is on its way out
    would make the parametrize mean two different things at once.
    """
    from personalclaw.sdk import channel

    assert callable(getattr(channel, name)), f"{name} is not importable from the sdk facade"
    assert not hasattr(channel, f"_{name}"), f"_{name} is still exported alongside {name}"

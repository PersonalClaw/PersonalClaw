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


@pytest.mark.parametrize("name", ["run_chat", "save_session_to_history"])
def test_the_promoted_names_resolve_and_the_private_ones_are_gone(name):
    """Clean break: the public name works and the old spelling is not left behind.

    Three channel apps call `run_chat` and one calls `save_session_to_history`, so a
    rename that left the underscore alias in place would have shipped both spellings
    forever — which is how the private name got onto the surface to begin with.
    """
    from personalclaw.sdk import channel

    assert callable(getattr(channel, name)), f"{name} is not importable from the sdk facade"
    assert not hasattr(channel, f"_{name}"), f"_{name} is still exported alongside {name}"

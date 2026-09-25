"""``personalclaw.agent`` must not freeze a real-home path at IMPORT time (#3463).

``AGENTS_DIR = _user_dir() / "agents"`` was evaluated when the module was first imported, and
``_user_dir()`` is ``config.loader.config_dir()``. So if ``personalclaw.agent`` was imported
*before* a test established isolation — ``PERSONALCLAW_HOME``, a ``config_dir`` monkeypatch, a
``tmp_path`` home — the constant froze to the real ``~/.personalclaw`` and no later isolation
could move it. Whether that happened was a function of pytest's collection order, not of any
test's own correctness.

The consequence is the worst shape a gate can have: ``148 passed, 0 failed`` with **exit 1**,
because the suite's real-home rail saw ``modified agents/personalclaw.json``. The count says
clean, the status says dirty, and the natural reading is "my diff touched the real home".

🔑 MEASURED CLEANER THAN THE REPORT. A run whose selector resolved to ``collected 0 items``
— so **no test body executed at all** — still produced ``modified agents/personalclaw.json``.
Residue on a zero-collection run cannot be caused by a test; it is produced while modules are
imported, which is exactly what an import-time constant means.

🪤 AND THIS IS NOT ``DASHBOARD_PORT``. ``config.loader.DASHBOARD_PORT`` is also import-time and
that is *documented as safe*: ``PERSONALCLAW_PORT`` is validated at CLI entry, so the value is
settled before ``loader.py`` is imported. ``AGENTS_DIR`` has no such guarantee — relocating the
home **after** import is the supported way to isolate a test — so the same shape is a defect
here and correct there. The distinction is what decides the fix: a resolver, not an earlier
assignment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_the_agents_dir_is_a_resolver_not_a_frozen_constant() -> None:
    """The shape, asserted first so nothing destructive runs on a tree that still freezes it."""
    import personalclaw.agent as agent

    assert callable(getattr(agent, "agents_dir", None)), (
        "personalclaw.agent must expose agents_dir() — a module-level AGENTS_DIR is evaluated "
        "at import time and freezes whatever the home was then (#3463)"
    )
    assert not hasattr(agent, "AGENTS_DIR"), (
        "AGENTS_DIR still exists. A constant kept beside the resolver is the dual path the "
        "clean-break rule forbids, and every consumer that keeps importing it keeps the bug."
    )


def test_it_follows_a_home_established_AFTER_import(tmp_path: Path, monkeypatch) -> None:
    """The defect itself.

    ``personalclaw.agent`` is already imported by the time any test runs (conftest imports the
    package), so a constant is already frozen here — which is why establishing the home *now*
    is the whole test. Both supported isolation seams are exercised, because a fix that only
    honoured one would leave the other class of test exposed.
    """
    import personalclaw.agent as agent

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    assert agent.agents_dir() == tmp_path / "agents"

    other = tmp_path / "second"
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: other)
    assert agent.agents_dir() == other / "agents"


def test_the_config_write_lands_in_the_isolated_home(tmp_path: Path, monkeypatch) -> None:
    """The write that produced the rail failure, driven.

    ``rebuild_agent_config`` does ``AGENTS_DIR.mkdir(...)`` then writes
    ``AGENTS_DIR / AGENT_FILENAME`` — so on a tree that freezes the constant this writes into
    the developer's real home. The resolver assertion above runs first and fails there, so this
    body is never reached on an unfixed tree: a test for a real-home leak must not cause one.
    """
    import personalclaw.agent as agent

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    assert agent.agents_dir() == tmp_path / "agents"  # precondition, not the assertion

    agent.rebuild_agent_config()
    written = tmp_path / "agents" / agent.AGENT_FILENAME
    assert written.is_file(), f"the config did not land under the isolated home: {tmp_path}"
    assert json.loads(written.read_text(encoding="utf-8")), "the written config is empty"


def test_the_dead_mcp_json_constant_is_gone() -> None:
    """``_USER_MCP_JSON`` had the same defect and **zero readers**.

    The issue asks for it to be resolved lazily too, on the ground that fixing only
    ``AGENTS_DIR`` leaves ``mcp.json`` exposed. Measured: nothing in ``src/``, ``tests/``,
    ``harness/``, ``web/`` or ``scripts/`` ever read it, and the path it named is separately
    read as ``_USER_DIR / "mcp.json"`` inside ``rebuild_agent_config``. So it is deleted rather
    than made lazy — a constant that does not exist cannot be exposed, and a lazy resolver with
    no callers would be the inert-surface shape this repo has a ratchet for.
    """
    import personalclaw.agent as agent

    assert not hasattr(agent, "_USER_MCP_JSON")
    src = Path(agent.__file__).resolve().parents[1]
    offenders = [
        f"{p.relative_to(src)}"
        for p in src.rglob("*.py")
        if "_USER_MCP_JSON" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"_USER_MCP_JSON came back: {offenders}"


@pytest.mark.parametrize(
    "module,symbol",
    [
        ("personalclaw.session", "agents_dir"),
        ("personalclaw.cli_doctor", "agents_dir"),
        ("personalclaw.dashboard.chat_persistence", "agents_dir"),
    ],
)
def test_every_consumer_resolves_per_call(module: str, symbol: str) -> None:
    """A resolver nobody calls fixes nothing.

    Three consumers imported ``AGENTS_DIR`` at their own **module top level**
    (``cli_doctor``, ``chat_persistence``) or inside a function (``session``), and a top-level
    ``from personalclaw.agent import AGENTS_DIR`` re-freezes the value in a second module even
    after the first is fixed. This asserts each now holds the resolver, not a path.
    """
    import importlib

    mod = importlib.import_module(module)
    found = getattr(mod, symbol, None)
    if found is None:
        # a function-local import — assert the source names the resolver and not the constant
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "agents_dir" in source, f"{module} does not reach the resolver"
        assert "AGENTS_DIR" not in source, f"{module} still imports the frozen constant"
        return
    assert callable(found), f"{module}.{symbol} must be the resolver, not a frozen Path"


def test_no_module_level_constant_is_built_from_the_home_in_agent_py() -> None:
    """The residue check the issue asks for, scoped to the module that had the defect.

    🪤 IT IS DELIBERATELY NOT A TREE-WIDE BAN. `service/macos.py`'s `PLIST_DIR` and
    `skills/marketplace.py`'s discovery paths are built from `Path.home()` and are correct —
    a launch agent really does live in the user's `Library`, and `~/.agents/skills` is not the
    PersonalClaw home at all. The defect is specifically a **`config_dir()`-derived** path
    frozen at import, because `config_dir()` is the seam isolation moves.

    🔴 AND IT NAMES WHAT IS STILL FROZEN. `_USER_DIR` (and the `_USER_PROMPT` /
    `_USER_OVERRIDES` pair derived from it) and `_DEFAULT_HOOKS_DIR` have the identical shape
    and are NOT converted here: between them ~17 test sites monkeypatch the constants directly
    — which is the symptom, since patching a frozen constant is the only way to redirect one —
    and rewriting those is a second change with its own blast radius. They are READ paths, so
    they do not produce the rail failure this issue is about; the writer does, and the writer
    is `agents_dir()`. This assertion holds the line at the one that writes.
    """
    import ast

    import personalclaw.agent as agent

    tree = ast.parse(Path(agent.__file__).read_text(encoding="utf-8"))
    frozen = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        if node.value is None:
            continue
        text = ast.unparse(node.value)
        if "_user_dir()" in text or "config_dir()" in text:
            frozen.extend(names)
    assert (
        "AGENTS_DIR" not in frozen and "_USER_MCP_JSON" not in frozen
    ), f"a home-derived path is frozen at import again: {frozen}"
    # Non-vacuous: the two still-frozen ones are present, so this is a LINE and not a green
    # over an empty scan. When they are converted, this expectation must shrink deliberately.
    assert sorted(frozen) == ["_DEFAULT_HOOKS_DIR", "_USER_DIR"], (
        "the set of import-time home paths in agent.py changed — convert the new one or record "
        f"why it is safe (the DASHBOARD_PORT distinction): {sorted(frozen)}"
    )

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


def _session_reads(_capsys) -> str:
    from personalclaw.session import SessionManager

    # Its answers are kept for the process, and a process serves one home: each read starts
    # clean, and what the process kept before is put back after.
    kept = SessionManager.__dict__.get("_agent_model_cache")
    SessionManager._agent_model_cache = {}  # type: ignore[attr-defined]
    try:
        return SessionManager._resolve_agent_model("helper")
    finally:
        if kept is None:
            del SessionManager._agent_model_cache  # type: ignore[attr-defined]
        else:
            SessionManager._agent_model_cache = kept  # type: ignore[attr-defined]


def _chat_persistence_reads(_capsys) -> str:
    from personalclaw.dashboard.chat_persistence import _build_agent_model_map

    return _build_agent_model_map().get("helper", "")


def _doctor_reads(capsys) -> str:
    from test_cli_doctor_reads_only import _run_doctor, _section

    _code, out = _run_doctor(capsys)
    return _section(out, "Agent").splitlines()[0]


#: The three consumers that imported ``AGENTS_DIR``, at their own module top level
#: (``cli_doctor``, ``chat_persistence``) or inside a function (``session``), and what each reads
#: of the agents folder: the model an agent's file names, or the doctor's agent-config row.
_CONSUMERS = {
    "session": (_session_reads, "model-{home}"),
    "chat_persistence": (_chat_persistence_reads, "model-{home}"),
    "cli_doctor": (_doctor_reads, "  config:      ✅ {path}"),
}


@pytest.mark.parametrize(("read", "expected"), _CONSUMERS.values(), ids=list(_CONSUMERS))
def test_every_consumer_resolves_per_call(tmp_path: Path, monkeypatch, capsys, read, expected):
    """A resolver nobody calls fixes nothing.

    A top-level ``from personalclaw.agent import AGENTS_DIR`` re-freezes the value in a second
    module even after the first is fixed. So each consumer reads two homes, both established
    after every module was imported, by each isolation seam in turn, each home's agents folder
    naming its own model: each read is that home's answer.
    """
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.setenv(var, str(user / var.lower()))
    for name in ("first", "second"):
        agents = tmp_path / name / "agents"
        agents.mkdir(parents=True)
        (agents / "personalclaw.json").write_text(
            json.dumps({"name": "helper", "model": f"model-{name}"}), encoding="utf-8"
        )

    answers = []
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "first"))
    answers.append(read(capsys))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path / "second")
    answers.append(read(capsys))

    assert answers == [
        expected.format(home=name, path=tmp_path / name / "agents" / "personalclaw.json")
        for name in ("first", "second")
    ]


def _frozen_home_constants(source: str) -> list[str]:
    """Module-level names whose value is built from ``_user_dir()`` / ``config_dir()``."""
    import ast

    frozen = []
    for node in ast.parse(source).body:
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
    return frozen


def test_no_module_level_constant_is_built_from_the_home_in_agent_py() -> None:
    """The residue check the issue asks for, scoped to the module that had the defect.

    🪤 IT IS DELIBERATELY NOT A TREE-WIDE BAN. `service/macos.py`'s `PLIST_DIR` and
    `skills/marketplace.py`'s install path are built from `Path.home()` and are correct — a
    launch agent really does live in the user's `Library`, and `~/.agents/skills` is not the
    PersonalClaw home at all. The defect is specifically a **`config_dir()`-derived** path
    frozen at import, because `config_dir()` is the seam isolation moves. The tree-wide line is
    `tests/test_importing_personalclaw_touches_no_home.py`, which imports every module.

    `_USER_DIR` (with the `_USER_PROMPT` / `_USER_OVERRIDES` pair derived from it) and
    `_DEFAULT_HOOKS_DIR` were the last two; they are call-time resolvers now, so none is left.
    """
    import personalclaw.agent as agent

    frozen = _frozen_home_constants(Path(agent.__file__).read_text(encoding="utf-8"))
    assert frozen == [], f"a home-derived path is frozen at import again: {frozen}"


def test_the_residue_scan_sees_a_frozen_constant() -> None:
    """Non-vacuous: an empty result above is a line only if the scan finds the shape it bans."""
    assert _frozen_home_constants('_DEFAULT_HOOKS_DIR = _user_dir() / "hooks"\n') == [
        "_DEFAULT_HOOKS_DIR"
    ]
    assert _frozen_home_constants("HOME: Path = config_dir()\n") == ["HOME"]

"""WF2LEA-6 — accepted skill refinements apply as sidecar overlays.

The load-bearing properties: accepting a refinement writes exactly ONE overlay file (the base
``SKILL.md`` is never touched), reverting removes exactly that ONE file, and an
``install_guarded`` skill's ``.pclaw-lock.json`` — and therefore ``verify_skill_integrity`` —
is unchanged by BOTH the apply and the revert. That last property is why the overlay lives
outside the locked directory rather than inside it.
"""

from __future__ import annotations

import hashlib
import json
import time

import pytest

from personalclaw.skills import loader as loader_mod
from personalclaw.skills import overlays, proposals
from personalclaw.skills.loader import SkillsLoader
from personalclaw.skills.marketplace import verify_skill_integrity


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(loader_mod, "config_dir", lambda: tmp_path)
    import personalclaw.skills.marketplace as mp

    monkeypatch.setattr(mp, "SKILL_DISCOVERY_PATHS", [])
    return tmp_path


def _install_locked_skill(home, name: str, body: str):
    """Seed a skill with a correct ``.pclaw-lock.json`` — the shape ``install_guarded`` leaves.

    Returns ``(skill_dir, lock_path)``. ``verify_skill_integrity`` reads it as ``intact``.
    """
    skill_dir = home / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = skill_dir / "SKILL.md"
    md.write_text(body, encoding="utf-8")
    lock = {
        "id": name,
        "source": "skills.sh",
        "trust_tier": "community",
        "verdict": "clean",
        "sha256": {"SKILL.md": hashlib.sha256(md.read_bytes()).hexdigest()},
        "installed_at": int(time.time()),
    }
    lock_path = skill_dir / ".pclaw-lock.json"
    lock_path.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    return skill_dir, lock_path


def _overlay_files(home):
    d = home / "skills" / ".overlays"
    return sorted(p for p in d.rglob("*.json")) if d.is_dir() else []


def test_apply_writes_one_file_and_load_merges_it(home):
    _install_locked_skill(home, "release-flow", "---\nname: release-flow\n---\n\nBase steps.\n")
    overlays.apply_overlay(
        "release-flow", description="Prefer uv", procedure_md="Use uv, never pip.", created_at=""
    )
    assert len(_overlay_files(home)) == 1
    loaded = SkillsLoader(install_builtins=False).load_skill("release-flow")
    assert "Base steps." in loaded and "Use uv, never pip." in loaded


def test_no_overlay_leaves_the_body_untouched(home):
    _install_locked_skill(home, "release-flow", "---\nname: release-flow\n---\n\nBase steps.\n")
    loaded = SkillsLoader(install_builtins=False).load_skill("release-flow")
    assert loaded == "---\nname: release-flow\n---\n\nBase steps.\n"


def test_revert_removes_exactly_one_file_and_keeps_the_lock_intact(home):
    """The falsification target: apply then revert leaves the base skill and its lock exactly
    as install_guarded wrote them, and revert deletes exactly ONE file."""
    skill_dir, lock_path = _install_locked_skill(
        home, "release-flow", "---\nname: release-flow\n---\n\nBase steps.\n"
    )
    lock_before = lock_path.read_bytes()
    md_before = (skill_dir / "SKILL.md").read_bytes()
    assert verify_skill_integrity(skill_dir).ok  # precondition: intact

    overlays.apply_overlay("release-flow", procedure_md="Use uv, never pip.")
    assert len(_overlay_files(home)) == 1
    # Apply touched neither the base file nor the lock — still verifiable.
    assert verify_skill_integrity(skill_dir).ok
    assert lock_path.read_bytes() == lock_before
    assert (skill_dir / "SKILL.md").read_bytes() == md_before

    before = len(_overlay_files(home))
    removed = overlays.revert_overlay("release-flow")
    after = len(_overlay_files(home))

    assert removed == 1
    assert before - after == 1  # exactly one file gone
    assert after == 0
    # Revert touched neither the base file nor the lock.
    assert verify_skill_integrity(skill_dir).ok
    assert lock_path.read_bytes() == lock_before
    assert (skill_dir / "SKILL.md").read_bytes() == md_before


def test_revert_of_a_skill_with_no_overlay_is_a_noop(home):
    _install_locked_skill(home, "release-flow", "---\nname: release-flow\n---\n\nBase steps.\n")
    assert overlays.revert_overlay("release-flow") == 0


def test_accept_of_a_refine_proposal_applies_the_overlay(home):
    """End to end: accepting a refine proposal against a locked skill writes one overlay file
    and leaves the lock intact — the mechanism a reviewer actually drives."""
    skill_dir, lock_path = _install_locked_skill(
        home, "task-and-project", "---\nname: task-and-project\n---\n\nOriginal body.\n"
    )
    lock_before = lock_path.read_bytes()
    p = proposals.enqueue(
        slug="task-and-project",
        description="Always link the design doc",
        triggers="task",
        procedure_md="Attach the design doc link.",
        session_key="s",
        created_at="2026-08-15T00:00:00+00:00",
        kind="refine",
        refine_target="task-and-project",
    )
    proposals.accept(p.id)
    assert len(_overlay_files(home)) == 1
    assert verify_skill_integrity(skill_dir).ok
    assert lock_path.read_bytes() == lock_before
    loaded = SkillsLoader(install_builtins=False).load_skill("task-and-project")
    assert "Attach the design doc link." in loaded


def test_overlay_refuses_a_traversal_name(home):
    assert overlays.overlay_path("../escape") is None
    with pytest.raises(ValueError):
        overlays.apply_overlay("../escape", procedure_md="x")

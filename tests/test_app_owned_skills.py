"""App-owned SKILL.md skills — an app SHIPS and OWNS the skills it declares (§4.1).

Mirrors :mod:`test_app_owned_prompts` for the skills half of Platform-Legibility
S4: an app declares skill DIRECTORIES in its manifest's ``skills`` list; on enable
(and startup discovery) each dir is seeded into the user skills tree THROUGH the
supply-chain chokepoint (:func:`personalclaw.skills.marketplace.install_scanned` →
quarantine → scan at the app's trust tier → ``.pclaw-lock.json`` provenance). The
one rule the prompt path doesn't need: **a skill never bypasses the gate just
because it arrived inside an app.** Seeding is idempotent + non-clobbering; removal
is provenance-keyed (``source == app:<name>``) so a user's own skill — or another
app's — is never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager
from personalclaw.apps.manifest import AppManifest
from personalclaw.apps.skill_seed import remove_app_skills, seed_app_skills


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Isolated config dir → the skills tree lands under tmp_path, never the real home."""
    import personalclaw.config.loader as cfg
    import personalclaw.skills.loader as skloader

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    # skills.loader binds ``config_dir`` into its own namespace at import.
    monkeypatch.setattr(skloader, "config_dir", lambda: tmp_path)
    # Seeding is intentionally exercised here, so the skip flag must be OFF.
    monkeypatch.delenv("PERSONALCLAW_SKIP_SKILL_SEED", raising=False)
    yield tmp_path


def _skill_md(name: str, body: str = "A benign app helper.") -> str:
    return f"---\nname: {name}\ndescription: {body}\n---\n\n# {name}\n\n{body}\n"


def _skill_app(tmp_path: Path, *, name: str = "skilly", skill_dir: str = "deploy") -> Path:
    """A fixture app that ships ONE benign SKILL.md skill dir."""
    d = tmp_path / "src" / name
    sk = d / "skills" / skill_dir
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text(_skill_md(skill_dir), encoding="utf-8")
    (d / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": name.title(),
                "description": "ships its own SKILL.md skill",
                "skills": [{"path": f"skills/{skill_dir}/"}],
            }
        ),
        encoding="utf-8",
    )
    return d


def test_install_seeds_app_skill_through_the_chokepoint(tmp_path):
    """Enabling an app seeds its declared skill with app-provenance in the lock."""
    from personalclaw.skills.loader import skills_dir

    res = app_manager.install(_skill_app(tmp_path), confirm=True)
    assert res.ok, res.error

    dest = skills_dir() / "deploy"
    assert (dest / "SKILL.md").is_file(), "app skill not seeded into the user tree"
    lock = dest / ".pclaw-lock.json"
    assert lock.is_file(), "seed must pass through install_scanned (writes .pclaw-lock.json)"
    recorded = json.loads(lock.read_text(encoding="utf-8"))
    # Provenance proves the supply-chain chokepoint ran (not a bypassing copy).
    assert recorded["source"] == "app:skilly"
    assert recorded["sha256"], "lock records a per-file integrity baseline"


def test_reseed_is_idempotent_and_non_clobbering(tmp_path):
    """A user-edited app skill is NOT overwritten on a re-seed (mirrors prompts)."""
    from personalclaw.skills.loader import skills_dir

    res = app_manager.install(_skill_app(tmp_path), confirm=True)
    assert res.ok, res.error
    dest = skills_dir() / "deploy"

    # Simulate a user edit, then re-seed (e.g. a gateway restart) — it must survive.
    (dest / "SKILL.md").write_text("EDITED BY USER\n", encoding="utf-8")
    manifest = AppManifest.from_json_file(manager.app_dir("skilly") / "app.json")
    seed_app_skills(manifest, manager.app_dir("skilly"), origin="local")
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "EDITED BY USER\n"


def test_removal_is_provenance_keyed(tmp_path):
    """Removal drops ONLY this app's skills — a user skill + another app's are safe."""
    from personalclaw.skills.loader import skills_dir

    assert app_manager.install(
        _skill_app(tmp_path, name="skilly", skill_dir="deploy"), confirm=True
    ).ok
    assert app_manager.install(
        _skill_app(tmp_path, name="other", skill_dir="probe"), confirm=True
    ).ok

    tree = skills_dir()
    # A user's own skill: no lock at all → never provenance-owned by any app.
    user_skill = tree / "mine"
    user_skill.mkdir()
    (user_skill / "SKILL.md").write_text(_skill_md("mine"), encoding="utf-8")

    assert (tree / "deploy" / "SKILL.md").is_file()
    assert (tree / "probe" / "SKILL.md").is_file()

    # Disabling skilly removes ONLY skilly's skill.
    assert app_manager.disable("skilly") is True
    assert not (tree / "deploy").exists(), "the app's own skill is removed on disable"
    assert (tree / "probe" / "SKILL.md").is_file(), "another app's skill is untouched"
    assert (user_skill / "SKILL.md").is_file(), "a user's own skill is untouched"


def test_removal_leaves_a_same_named_user_skill_untouched(tmp_path):
    """A user skill whose dir NAME collides with the app's is not removed (no matching lock)."""
    from personalclaw.skills.loader import skills_dir

    tree = skills_dir()
    tree.mkdir(parents=True, exist_ok=True)
    # A user's own "deploy" skill exists BEFORE the app installs — the non-clobber
    # rule means the app never seeds over it, so its (absent-app) lock stays user-owned.
    user_deploy = tree / "deploy"
    user_deploy.mkdir()
    (user_deploy / "SKILL.md").write_text("USER OWNED\n", encoding="utf-8")

    assert app_manager.install(
        _skill_app(tmp_path, name="skilly", skill_dir="deploy"), confirm=True
    ).ok
    # Non-clobbering: the app did not overwrite the user's dir.
    assert (user_deploy / "SKILL.md").read_text(encoding="utf-8") == "USER OWNED\n"

    manifest = AppManifest.from_json_file(manager.app_dir("skilly") / "app.json")
    remove_app_skills(manifest, manager.app_dir("skilly"))
    # No app-provenance lock → removal leaves the user's skill in place.
    assert (user_deploy / "SKILL.md").read_text(encoding="utf-8") == "USER OWNED\n"


def test_skip_flag_suppresses_app_skill_seeding(tmp_path, monkeypatch):
    """PERSONALCLAW_SKIP_SKILL_SEED suppresses seeding (mirrors the prompt skip flag)."""
    from personalclaw.skills.loader import skills_dir

    monkeypatch.setenv("PERSONALCLAW_SKIP_SKILL_SEED", "1")
    app_dir = _skill_app(tmp_path)
    manifest = AppManifest.from_json_file(app_dir / "app.json")
    seed_app_skills(manifest, app_dir, origin="local")
    assert not (skills_dir() / "deploy").exists()


def test_no_declared_skills_is_a_no_op(tmp_path):
    """An app that declares no skills seeds nothing — robust when empty."""
    from personalclaw.skills.loader import skills_dir

    d = tmp_path / "src" / "plain"
    d.mkdir(parents=True)
    (d / "app.json").write_text(
        json.dumps(
            {
                "name": "plain",
                "version": "1.0.0",
                "displayName": "Plain",
                "description": "no skills",
            }
        ),
        encoding="utf-8",
    )
    assert app_manager.install(d, confirm=True).ok
    tree = skills_dir()
    assert not tree.exists() or not any(tree.iterdir())

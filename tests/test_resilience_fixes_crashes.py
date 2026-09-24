"""Confirm-gated fixes + crash capture + surfacing-simulator tests
(PLATFORM-RESILIENCE §2, §6.5, §3.1)."""

from __future__ import annotations

import json

from personalclaw.resilience import crashes, fixes
from personalclaw.skills.surfacing import surface_skills

# ── §2 confirm-gated fixes ────────────────────────────────────────────────────


def test_builtin_fixes_registered():
    ids = {f.id for f in fixes.all_fixes()}
    assert {
        "serving-fs.symlink-repair",
        "serving-fs.orphan-prune",
        "model-providers.prune-bindings",
    } <= ids


def test_every_fix_has_a_readonly_preview():
    # dry_preview must be callable and return a string without mutating anything.
    for fx in fixes.all_fixes():
        preview = fx.dry_preview()
        assert isinstance(preview, str) and preview


def test_apply_unknown_fix_is_safe():
    result = fixes.apply_fix("no-such-fix")
    assert result["ok"] is False and "unknown" in result["error"]


def test_symlink_repair_preview_on_copy(tmp_path, monkeypatch):
    """When static/dist is a real-directory COPY, the preview describes a repair.
    `_dist_paths` derives repo_root as pkg_dir.parent.parent (<repo>/src/personalclaw),
    so lay the fake package out that way and put web/dist at the repo root."""
    import personalclaw

    pkg = tmp_path / "src" / "personalclaw"
    (pkg / "static" / "dist").mkdir(parents=True)
    (pkg / "static" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "web" / "dist").mkdir(parents=True)
    (tmp_path / "web" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(personalclaw, "__file__", str(pkg / "__init__.py"))
    fx = fixes.get_fix("serving-fs.symlink-repair")
    assert fx is not None
    preview = fx.dry_preview()
    assert "symlink" in preview and "copy" in preview.lower()


def test_symlink_repair_apply_backs_up_and_links(tmp_path, monkeypatch):
    import personalclaw

    pkg = tmp_path / "src" / "personalclaw"
    dist = pkg / "static" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("STALE", encoding="utf-8")
    built = tmp_path / "web" / "dist"
    built.mkdir(parents=True)
    (built / "index.html").write_text("FRESH", encoding="utf-8")
    monkeypatch.setattr(personalclaw, "__file__", str(pkg / "__init__.py"))

    # Apply directly (bypassing the SEL wrapper's config load) via the fix's apply().
    fx = fixes.get_fix("serving-fs.symlink-repair")
    result = fx.apply()  # type: ignore[union-attr]
    assert "Repaired" in result
    assert dist.is_symlink()
    assert dist.resolve() == built.resolve()
    # The shadow copy was backed up, not deleted.
    assert (pkg / "static" / "dist.shadow" / "index.html").read_text() == "STALE"


# ── §6.5 crash capture ────────────────────────────────────────────────────────


def test_record_and_read_crash_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.resilience.crashes.config_dir", lambda: tmp_path)
    assert crashes.crash_count() == 0
    path = crashes.record_crash(
        "turn",
        RuntimeError("boom in the turn"),
        session_key="dashboard:abc",
        last_turns=["hello", "world"],
        active_model="bedrock:claude",
        uptime_secs=42.5,
        now=1000.0,
    )
    assert path is not None and path.exists()
    assert crashes.crash_count() == 1
    recent = crashes.recent_crashes()
    assert len(recent) == 1
    assert recent[0]["kind"] == "turn"
    assert recent[0]["exception_type"] == "RuntimeError"
    # Full read by filename.
    full = crashes.read_crash(path.name)
    assert full is not None
    assert full["session_key"] == "dashboard:abc"
    assert full["exception"]["message"] == "boom in the turn"


def test_crash_redacts_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.resilience.crashes.config_dir", lambda: tmp_path)
    secret = "AKIA" + "Z" * 16  # AWS-key shape → redact() masks it
    crashes.record_crash(
        "gateway",
        RuntimeError(f"auth failed with {secret}"),
        now=2000.0,
    )
    recent = crashes.recent_crashes()
    assert secret not in json.dumps(recent)  # the raw credential must not survive


def test_crash_dir_capped(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.resilience.crashes.config_dir", lambda: tmp_path)
    for i in range(25):
        crashes.record_crash("turn", RuntimeError(f"e{i}"), now=1000.0 + i)
    # Capped at 20 — oldest pruned.
    assert crashes.crash_count() <= 20


def test_read_crash_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.resilience.crashes.config_dir", lambda: tmp_path)
    assert crashes.read_crash("../config.json") is None
    assert crashes.read_crash("a/b.json") is None


# ── §3.1 surfacing simulator (explain mode) ───────────────────────────────────

_SKILLS = [
    {
        "key": "deploy",
        "description": "deploy to kubernetes",
        "triggers": "deploy,ship",
        "path": "/x",
        "use_count": 5,
    },
    {
        "key": "vetoed",
        "description": "never for greetings",
        "triggers": "!hello",
        "path": "/y",
        "use_count": 0,
    },
    {
        "key": "archived-one",
        "description": "old",
        "triggers": "deploy",
        "path": "/z",
        "status": "archived",
        "use_count": 0,
    },
]


def test_explain_returns_per_candidate_breakdown():
    rows = surface_skills("please deploy the service", _SKILLS, max_skills=5, explain=True)
    assert isinstance(rows, list) and all(isinstance(r, dict) for r in rows)
    by_key = {r["key"]: r for r in rows}
    # deploy matches on keyword → included with a reason.
    assert by_key["deploy"]["included"] is True
    assert by_key["deploy"]["kw_score"] >= 0.7
    assert "included" in by_key["deploy"]["reason"]
    # archived is surfaced (why-excluded) with a clear reason, not silently dropped.
    assert by_key["archived-one"]["included"] is False
    assert "archived" in by_key["archived-one"]["reason"]


def test_explain_shows_negative_trigger_veto():
    rows = surface_skills("hello there", _SKILLS, max_skills=5, explain=True)
    veto = next(r for r in rows if r["key"] == "vetoed")
    assert veto["negated"] is True and veto["included"] is False


def test_non_explain_still_returns_keys():
    keys = surface_skills("please deploy the service", _SKILLS, max_skills=5)
    assert keys == ["deploy"]  # unchanged behavior — explain is opt-in


def test_explain_empty_query_returns_empty():
    assert surface_skills("", _SKILLS, max_skills=5, explain=True) == []

"""Tests for the SV-10 evidence bundle mechanics and the optional fix-branch stage.

Covers the five things the atom's own `done_when` and the plan's Success Criteria #7/#8 turn on:
manifest hashing computed from bytes, the bundle registering as exactly ONE Artifact, the
required-kinds completion gate blocking on a missing kind and passing when complete, ffmpeg-absent
degradation staying typed rather than crashing, and the fix branch being created only when enabled
(and never pushed).
"""

from __future__ import annotations

import hashlib
import subprocess
import types
from pathlib import Path

import pytest

from personalclaw.selfqa import evidence as ev
from personalclaw.selfqa import fix_branch as fb


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture()
def bundle(tmp_path: Path) -> Path:
    """A bundle dir with a screenshot, a recording, and a log — deterministic bytes."""
    _write(tmp_path / "screenshots" / "step1.png", b"\x89PNG\r\n\x1a\n-one")
    _write(tmp_path / "screenshots" / "step2.png", b"\x89PNG\r\n\x1a\n-two")
    _write(tmp_path / "recording.mp4", b"\x00\x00\x00\x18ftypmp42-recording-bytes")
    _write(tmp_path / "run.log", b"scenario drove the UI and it did not persist\n")
    return tmp_path


# ── manifest hashing (Criterion #7: "under one SHA256'd manifest") ──────────────


def test_manifest_hashes_are_computed_from_the_bytes_on_disk(bundle: Path) -> None:
    manifest = ev.build_manifest(bundle, scenario_id="s1", sha="a" * 40, passed=False)

    by_name = {e.name: e for e in manifest.files}
    assert "manifest.json" not in by_name, "the manifest must not list itself"

    for rel in ("screenshots/step1.png", "screenshots/step2.png", "recording.mp4", "run.log"):
        entry = by_name[rel]
        raw = (bundle / rel).read_bytes()
        assert entry.sha256 == hashlib.sha256(raw).hexdigest()
        assert entry.size == len(raw)

    assert by_name["screenshots/step1.png"].kind == ev.KIND_SCREENSHOT
    assert by_name["recording.mp4"].kind == ev.KIND_RECORDING
    assert by_name["run.log"].kind == ev.KIND_LOG
    assert manifest.schema_version == ev.MANIFEST_SCHEMA_VERSION


def test_a_changed_byte_changes_the_manifest_digest(bundle: Path) -> None:
    before = {e.name: e.sha256 for e in ev.build_manifest(bundle).files}
    (bundle / "recording.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42-DIFFERENT")
    after = {e.name: e.sha256 for e in ev.build_manifest(bundle).files}
    assert before["recording.mp4"] != after["recording.mp4"]
    assert before["run.log"] == after["run.log"]


def test_write_manifest_roundtrips(bundle: Path) -> None:
    manifest = ev.build_manifest(bundle, scenario_id="s1", sha="b" * 40, passed=True)
    ev.write_manifest(bundle, manifest)
    loaded = ev.load_manifest(bundle)
    assert loaded is not None
    assert loaded.scenario_id == "s1"
    assert loaded.passed is True
    assert loaded.kinds() == manifest.kinds()


# ── ffmpeg-absent graceful degradation (Criterion: "never a crash") ─────────────


def test_ffmpeg_absent_degrades_typed_and_writes_no_file(bundle: Path, monkeypatch) -> None:
    monkeypatch.setattr(ev, "ffmpeg_available", lambda **_: False)

    sheet = ev.derive_contact_sheet(bundle)
    gif = ev.derive_gif(bundle)

    for deriv, kind in ((sheet, ev.KIND_CONTACT_SHEET), (gif, ev.KIND_GIF)):
        assert deriv.kind == kind
        assert deriv.produced is False
        assert deriv.degraded_reason  # non-empty reason, not an exception
        assert "ffmpeg" in deriv.degraded_reason.lower()
    assert not (bundle / ev.CONTACT_SHEET_NAME).exists()
    assert not (bundle / ev.GIF_NAME).exists()


def test_missing_recording_degrades_typed(bundle: Path, monkeypatch) -> None:
    monkeypatch.setattr(ev, "ffmpeg_available", lambda **_: True)
    (bundle / "recording.mp4").unlink()
    deriv = ev.derive_contact_sheet(bundle)
    assert deriv.produced is False
    assert "recording" in deriv.degraded_reason.lower()


def test_degradation_reasons_are_recorded_in_the_manifest(bundle: Path, monkeypatch) -> None:
    monkeypatch.setattr(ev, "ffmpeg_available", lambda **_: False)
    derivations = (ev.derive_contact_sheet(bundle), ev.derive_gif(bundle))
    manifest = ev.build_manifest(bundle, degradations=derivations)
    degraded_kinds = {d["kind"] for d in manifest.degraded}
    assert degraded_kinds == {ev.KIND_CONTACT_SHEET, ev.KIND_GIF}
    assert all(d["reason"] for d in manifest.degraded)


def test_probe_uses_a_none_sentinel_not_a_zero_time() -> None:
    ev.reset_probe_cache()
    assert ev._probe_cache is None
    ev.ffmpeg_available()
    # After a probe the cache is a (monotonic, bool) pair, never the 0.0 "never yet" the rule
    # forbids — the None above is what distinguishes "unchecked" from "checked at t≈0".
    assert isinstance(ev._probe_cache, tuple) and len(ev._probe_cache) == 2
    ev.reset_probe_cache()


# ── required-kinds completion gate (Criterion #7) ───────────────────────────────


def test_gate_passes_when_the_required_kinds_are_present(bundle: Path) -> None:
    manifest = ev.build_manifest(bundle)
    result = ev.check_required_kinds(manifest)  # DEFAULT = screenshot, recording, manifest
    assert result.complete is True
    assert result.missing == []
    assert ev.KIND_MANIFEST in result.present


def test_gate_blocks_and_names_the_missing_kind(bundle: Path) -> None:
    (bundle / "recording.mp4").unlink()
    manifest = ev.build_manifest(bundle)
    result = ev.check_required_kinds(manifest)
    assert result.complete is False
    assert result.missing == [ev.KIND_RECORDING]


def test_gate_treats_the_manifest_kind_as_present_by_construction() -> None:
    empty = ev.Manifest()  # no files at all
    result = ev.check_required_kinds(empty, required_kinds=(ev.KIND_MANIFEST,))
    assert result.complete is True


def test_gate_honours_a_configured_required_kind(bundle: Path) -> None:
    # ffmpeg-derived kinds are NOT in the default set; a caller can still require one.
    manifest = ev.build_manifest(bundle)
    result = ev.check_required_kinds(manifest, required_kinds=(ev.KIND_GIF,))
    assert result.complete is False
    assert result.missing == [ev.KIND_GIF]


# ── single-Artifact registration (Criterion #7: "a single Artifact") ────────────


class _FakeProvider:
    """Records what register_bundle asks of a provider, so 'exactly one Artifact' is assertable."""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.stored: list[tuple[str, str]] = []

    def create(self, **kwargs) -> object:
        self.created.append(kwargs)
        return types.SimpleNamespace(slug="self-qa-evidence-s1")

    def store_version_file(self, slug: str, filename: str, data: bytes) -> bool:
        self.stored.append((slug, filename))
        return True


def test_register_bundle_creates_exactly_one_artifact(bundle: Path, monkeypatch) -> None:
    fake = _FakeProvider()
    monkeypatch.setattr("personalclaw.artifacts.registry.get_provider", lambda name=None: fake)
    manifest = ev.build_manifest(bundle, scenario_id="s1", sha="c" * 40, passed=False)

    registered = ev.register_bundle(bundle, manifest=manifest, scenario_id="s1", sha="c" * 40)

    assert len(fake.created) == 1, "the bundle must register as exactly ONE Artifact"
    created = fake.created[0]
    assert created["kind"] == "json"
    assert created["content"] == manifest.to_json()  # the manifest IS the artifact content
    assert "self-qa" in created["tags"] and "evidence" in created["tags"]
    assert registered.ref == "artifact:self-qa-evidence-s1"
    # every manifest file was stored under the artifact dir, content-addressed
    assert registered.stored_files == len(manifest.files)
    assert len(fake.stored) == len(manifest.files)


def test_stored_companion_names_are_content_addressed(bundle: Path, monkeypatch) -> None:
    from personalclaw.artifacts.native import _MEDIA_NAME_RE

    fake = _FakeProvider()
    monkeypatch.setattr("personalclaw.artifacts.registry.get_provider", lambda name=None: fake)
    manifest = ev.build_manifest(bundle, sha="d" * 40)
    ev.register_bundle(bundle, manifest=manifest)
    for _slug, filename in fake.stored:
        assert _MEDIA_NAME_RE.fullmatch(filename), f"{filename!r} is not a content-addressed name"


# ── optional fix branch (Criterion #8) ──────────────────────────────────────────


@pytest.fixture()
def git_repo(tmp_path: Path) -> tuple[Path, str]:
    """A throwaway git repo with one commit; returns (repo_path, HEAD sha)."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def _run(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    _run("init", "-q")
    _run("config", "user.email", "t@example.invalid")
    _run("config", "user.name", "Test")
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    _run("add", "-A")
    _run("commit", "-q", "-m", "initial")
    return repo, _run("rev-parse", "HEAD")


def test_fix_branch_name_uses_sha8() -> None:
    assert fb.fix_branch_name("abcdef1234567890") == "pclaw/selfqa-abcdef12"


def test_fix_branch_not_created_when_disabled(git_repo) -> None:
    repo, sha = git_repo
    result = fb.create_fix_branch(repo, sha, enabled=False)
    assert result.created is False
    assert result.branch == f"pclaw/selfqa-{sha[:8]}"
    assert "off" in result.reason.lower()
    # and nothing was actually created
    listed = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", result.branch],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert listed == ""


def test_fix_branch_created_when_enabled_and_never_pushed(git_repo) -> None:
    repo, sha = git_repo
    result = fb.create_fix_branch(repo, sha, enabled=True)
    assert result.created is True
    assert result.branch == f"pclaw/selfqa-{sha[:8]}"

    # the branch exists locally, pointing at the commit under test
    listed = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", result.branch],
        capture_output=True,
        text=True,
    ).stdout
    assert result.branch in listed
    tip = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", result.branch],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert tip == sha
    # no remote was configured, and nothing pushed one into being
    remotes = subprocess.run(
        ["git", "-C", str(repo), "remote"], capture_output=True, text=True
    ).stdout.strip()
    assert remotes == ""


def test_fix_branch_is_idempotent(git_repo) -> None:
    repo, sha = git_repo
    first = fb.create_fix_branch(repo, sha, enabled=True)
    assert first.created is True
    second = fb.create_fix_branch(repo, sha, enabled=True)
    assert second.created is False
    assert second.already_existed is True


def test_fix_branch_refuses_a_non_hex_ref(git_repo) -> None:
    repo, _sha = git_repo
    result = fb.create_fix_branch(repo, "--output=/tmp/pwned", enabled=True)
    assert result.created is False
    assert "refused" in result.reason.lower()

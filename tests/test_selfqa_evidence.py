"""Tests for the SV-10 evidence bundle mechanics and the optional fix-branch stage.

Covers the five things the atom's own `done_when` and the plan's Success Criteria #7/#8 turn on:
manifest hashing computed from bytes, the bundle registering as exactly ONE Artifact, the
required-kinds completion gate blocking on a missing kind and passing when complete, ffmpeg-absent
degradation staying typed rather than crashing, and the fix branch being created only when enabled
(and never pushed).

Two tiers, and the split is load-bearing. The mocked tier stubs `ffmpeg_available` and proves the
degradation contract on any host. The real-ffmpeg tier at the bottom derives from a generated
recording and asserts bytes on disk, because the mocked tier alone is blind by construction: it
never runs a filter graph, which is how a permanently invalid `tile` layout stayed green.
"""

from __future__ import annotations

import hashlib
import math
import shutil
import subprocess
import types
import warnings
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


# ── the tile grid arithmetic (host-independent) ─────────────────────────────────


def test_rows_are_derived_and_never_zero() -> None:
    """ffmpeg reads ``tile``'s layout as one WxH image size, so rows must be a real dimension.

    A literal ``0`` — which is what shipped — makes ffmpeg refuse the whole filter graph with
    ``Unable to parse "layout" option value "4x0" as image size`` on every host and every input.
    """
    # the two frame counts the SV-10 report measured, plus the awkward cases around them
    assert ev.contact_sheet_rows(3) == 1, "fewer frames than one row still needs one row"
    assert ev.contact_sheet_rows(12) == 3, "12 frames at 4 wide is exactly 3 rows"
    assert ev.contact_sheet_rows(1) == 1
    assert ev.contact_sheet_rows(4) == 1
    assert ev.contact_sheet_rows(5) == 2, "a count that is not a multiple of 4 rounds up"
    assert ev.contact_sheet_rows(9) == 3
    # zero frames is the one case with no grid at all — the caller degrades instead of tiling
    assert ev.contact_sheet_rows(0) == 0
    assert ev.contact_sheet_rows(-1) == 0
    # every positive frame count yields a positive row count, so no filter can carry a zero
    for frames in range(1, 40):
        rows = ev.contact_sheet_rows(frames)
        assert rows >= 1
        assert rows * ev.CONTACT_SHEET_COLUMNS >= frames, "the grid must hold every frame"


def test_frame_count_never_undercounts_what_ffmpeg_samples() -> None:
    """The estimate must be an UPPER bound on ffmpeg's own ``fps`` rounding.

    ``fps=1/N`` rounds to nearest, so a grid sized from a smaller number would make
    ``-frames:v 1`` emit only the first full tile and silently drop the tail of the recording.
    Over-counting costs one row of padding; under-counting costs evidence.
    """
    interval = ev.CONTACT_SHEET_FRAME_INTERVAL_SECS
    # measured against ffmpeg 9.0.1 at a 5s interval: duration -> frames it actually sampled
    measured = {1: 0, 2: 0, 3: 1, 5: 1, 7: 1, 8: 2, 10: 2, 12: 2, 13: 3, 20: 4, 21: 4, 60: 12}
    for duration, actual in measured.items():
        estimate = ev.sampled_frame_count(float(duration))
        assert (
            estimate >= actual
        ), f"{duration}s: {estimate} would drop frames (ffmpeg took {actual})"
        assert estimate == math.ceil(duration / interval)
    # an unknown/absent duration samples nothing, which the caller degrades on
    assert ev.sampled_frame_count(0.0) == 0
    assert ev.sampled_frame_count(-5.0) == 0


def test_an_unprobeable_duration_degrades_instead_of_tiling_a_zero(bundle: Path, monkeypatch):
    """The ZERO-frame branch: no duration means no honest row count, so it degrades typed.

    This is the arithmetic case that produced the shipped bug — a row count that could not be
    derived being written into the filter anyway. It is host-independent on purpose: the whole
    point is that no ffmpeg runs, so no ``tile=4x0`` can reach one. Without this the
    ``rows <= 0`` branch has no rail at all, and the only other zero-ish case (a real recording
    too short to sample) exercises a *different* branch further down.
    """
    monkeypatch.setattr(ev, "ffmpeg_available", lambda **_: True)
    monkeypatch.setattr(ev, "probe_duration_secs", lambda _p: 0.0)
    # if the guard were missing, this would spawn ffmpeg with the invalid layout instead
    monkeypatch.setattr(
        ev, "_run_ffmpeg", lambda argv: pytest.fail(f"ran ffmpeg on an unsized grid: {argv}")
    )

    deriv = ev.derive_contact_sheet(bundle)

    assert deriv.produced is False
    assert deriv.degraded_reason == ev._REASON_NO_DURATION
    assert deriv.path == "" and deriv.name == ""
    assert not (bundle / ev.CONTACT_SHEET_NAME).exists()


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


# ── integration (real ffmpeg) ───────────────────────────────────────────────────
#
# Every ffmpeg test ABOVE this line mocks `ffmpeg_available`, so none of them ever runs an ffmpeg
# filter graph. That is exactly how `tile=4x0` — a layout ffmpeg rejects on every host, for every
# input — sat behind a fully green suite: the only case that set `ffmpeg_available=True`
# (`test_missing_recording_degrades_typed`) unlinks the recording FIRST, so it returns before
# reaching the ffmpeg branch. A mock-only rail cannot see a defect in the argv it never runs.
#
# These tests close that blind spot the only way it can be closed: derive from a real recording
# with the real binary and assert real bytes on disk. When the host tools are missing they SKIP
# loudly — reported as a skip AND warned about — because a silent pass here would rebuild the
# same blind spot one layer out.
_HAS_FFMPEG = shutil.which("ffmpeg") is not None and ev.ffmpeg_available(refresh=True)
_HAS_FFPROBE = shutil.which("ffprobe") is not None
_UNPROVEN = (
    "UNPROVEN, NOT PASSED: ffmpeg/ffprobe absent, so the contact-sheet tile geometry and the GIF "
    "filter graph were never executed on this host"
)
_real_ffmpeg_only = pytest.mark.skipif(not (_HAS_FFMPEG and _HAS_FFPROBE), reason=_UNPROVEN)

if not (_HAS_FFMPEG and _HAS_FFPROBE):  # pragma: no cover - host-dependent
    warnings.warn(
        f"selfqa evidence rail is INCOMPLETE on this host: {_UNPROVEN}. No contact sheet or GIF "
        "was actually produced by this run.",
        stacklevel=1,
    )

#: The generated recording's frame size — each contact-sheet cell is one frame this size.
_CELL_W, _CELL_H = 320, 240


def _generate_recording(path: Path, duration_secs: int) -> None:
    """Write a real H.264 recording of ``duration_secs`` using ffmpeg's synthetic test source."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"testsrc=size={_CELL_W}x{_CELL_H}:rate=10:duration={duration_secs}",
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        capture_output=True,
        timeout=120,
        check=True,
    )  # fmt: skip


def _png_size(path: Path) -> tuple[int, int]:
    """The pixel dimensions of an image, read back with ffprobe."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout  # fmt: skip
    w, h = (int(v) for v in out.strip().split(","))
    return w, h


@_real_ffmpeg_only
@pytest.mark.parametrize(
    ("duration_secs", "expected_frames", "expected_rows"),
    [
        (13, 3, 1),  # fewer frames than one row of four
        (25, 5, 2),  # a frame count that is not a multiple of the column count
        (60, 12, 3),  # the exactly-3-rows case the SV-10 report measured
    ],
)
def test_real_ffmpeg_writes_a_contact_sheet_with_bytes(
    tmp_path: Path, duration_secs: int, expected_frames: int, expected_rows: int
) -> None:
    """A real recording yields a real contact sheet, whose GRID matches the derived row count.

    This is the test the shipped `tile=4x0` could never pass: ffmpeg refuses that layout
    outright, so `produced` was False and no file was written, on every host.
    """
    _generate_recording(tmp_path / ev.RECORDING_NAME, duration_secs)

    deriv = ev.derive_contact_sheet(tmp_path)

    assert deriv.produced is True, f"no contact sheet: {deriv.degraded_reason}"
    assert deriv.degraded_reason == ""
    sheet = tmp_path / ev.CONTACT_SHEET_NAME
    assert sheet.is_file(), "the derivation claimed a file that is not on disk"
    assert sheet.stat().st_size > 0, "a zero-byte sheet is not a sheet"
    assert Path(deriv.path) == sheet

    # the derived rows are the ones ffmpeg actually tiled: the sheet is COLUMNS cells wide and
    # `expected_rows` cells tall, which is only true if the row count was computed, not assumed
    assert ev.contact_sheet_rows(expected_frames) == expected_rows
    assert _png_size(sheet) == (ev.CONTACT_SHEET_COLUMNS * _CELL_W, expected_rows * _CELL_H)

    # and it lands in the manifest as a contact_sheet kind, not as a generic screenshot
    manifest = ev.build_manifest(tmp_path)
    assert ev.KIND_CONTACT_SHEET in manifest.kinds()


@_real_ffmpeg_only
def test_real_ffmpeg_degrades_when_the_recording_is_too_short_to_sample(tmp_path: Path) -> None:
    """A recording shorter than one sampling interval degrades — it never claims a missing file.

    ffmpeg exits 0 with an EMPTY stderr when `fps` selected no frame at all, writing nothing. A
    derivation that trusted the exit code would report `produced=True` with a path to a file that
    does not exist, which is a swallowed write recorded in the manifest as a success.
    """
    _generate_recording(tmp_path / ev.RECORDING_NAME, 2)

    deriv = ev.derive_contact_sheet(tmp_path)

    assert deriv.produced is False
    # the SPECIFIC reason, not merely a non-empty one: a truthy-reason assertion also passes when
    # ffmpeg rejected the filter graph outright, which is how this case stayed green against the
    # shipped `tile=4x0`. Pinning the reason makes it a rail for the swallowed write instead.
    assert deriv.degraded_reason == ev._REASON_EMPTY_SHEET
    assert "could not build" not in deriv.degraded_reason, "an ffmpeg failure is a different bug"
    assert deriv.path == ""
    assert not (tmp_path / ev.CONTACT_SHEET_NAME).exists()
    # the honesty contract: the manifest records the reason rather than a phantom file
    manifest = ev.build_manifest(tmp_path, degradations=(deriv,))
    assert ev.KIND_CONTACT_SHEET not in manifest.kinds()
    assert [d["kind"] for d in manifest.degraded] == [ev.KIND_CONTACT_SHEET]


@_real_ffmpeg_only
def test_real_ffmpeg_writes_a_gif_with_bytes(tmp_path: Path) -> None:
    """The SIBLING derivation, run for real: its palettegen graph and its trim window.

    `derive_gif` had the same mock-only coverage as the contact sheet — only the ffmpeg-ABSENT
    path was ever exercised — so neither its two-stage `split`/`palettegen`/`paletteuse` graph
    nor its `-ss`/`-t` trim arithmetic had ever been executed by a test.
    """
    _generate_recording(tmp_path / ev.RECORDING_NAME, 13)

    whole = ev.derive_gif(tmp_path)
    assert whole.produced is True, f"no GIF: {whole.degraded_reason}"
    gif = tmp_path / ev.GIF_NAME
    assert gif.is_file() and gif.stat().st_size > 0

    # the trim window is a real ffmpeg argument, not just a formatted string
    trimmed = ev.derive_gif(tmp_path, out_name="trimmed.gif", start_secs=4.0, window_secs=2.0)
    assert trimmed.produced is True, f"no trimmed GIF: {trimmed.degraded_reason}"
    small = tmp_path / "trimmed.gif"
    assert small.is_file() and small.stat().st_size > 0
    assert small.stat().st_size < gif.stat().st_size, "a 2s window must be smaller than 13s"

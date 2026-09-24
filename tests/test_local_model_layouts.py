"""On-disk layout probing for local models (LOCAL-MODEL-MANAGER-V2 §4.4).

The bug class: a downloaded model lands on disk in several shapes, and every consumer was
guessing at one. Guess wrong and you get either a `downloaded` flag reading False for a model
sitting right there (the user re-downloads gigabytes) or a `delete` that reports success while
leaving the weights behind (the disk never frees). Both were observed.

**Every fs-touching test here writes under `tmp_path` only** — the plan makes that a
test-suite invariant because a destructive test with no path isolation once deleted the
developer's real bound model. `test_the_suite_never_touches_a_real_models_dir` asserts it.
"""

from __future__ import annotations

import pytest

from personalclaw.local_models import layouts

# ── The HF hub name mangling ────────────────────────────────────────────


@pytest.mark.parametrize(
    "model,expected",
    [
        ("openai/whisper-large-v3", "models--openai--whisper-large-v3"),
        ("bert-base-uncased", "models--bert-base-uncased"),
        ("org/sub/name", "models--org--sub--name"),
        ("/leading-slash", "models--leading-slash"),
        ("trailing/", "models--trailing"),
    ],
)
def test_hf_repo_dirname(model, expected):
    """Mirrors the hub's escaping without importing it — the headless case must still work."""
    assert layouts.hf_repo_dirname(model) == expected


# ── is_downloaded across every layout ───────────────────────────────────


def test_finds_an_hf_snapshot(tmp_path):
    """The layout most often guessed wrong: the model id never appears literally."""
    snap = tmp_path / "models--openai--whisper-large-v3" / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / "model.safetensors").write_bytes(b"weights")
    assert layouts.is_downloaded(tmp_path, "openai/whisper-large-v3") is True


def test_finds_a_provider_native_directory(tmp_path):
    d = tmp_path / "tiny-en"
    d.mkdir()
    (d / "model.bin").write_bytes(b"w")
    assert layouts.is_downloaded(tmp_path, "tiny-en") is True


def test_finds_a_nested_provider_native_path(tmp_path):
    d = tmp_path / "rhasspy" / "piper-voices"
    d.mkdir(parents=True)
    (d / "voice.onnx").write_bytes(b"w")
    assert layouts.is_downloaded(tmp_path, "rhasspy/piper-voices") is True


@pytest.mark.parametrize("ext", ["", ".onnx", ".gguf", ".bin", ".safetensors", ".pt"])
def test_finds_a_direct_file_with_any_known_extension(tmp_path, ext):
    (tmp_path / f"en_US-amy-medium{ext}").write_bytes(b"voice")
    assert layouts.is_downloaded(tmp_path, "en_US-amy-medium") is True


def test_finds_a_direct_file_for_a_slashed_id(tmp_path):
    """ "org/model" fetched by URL lands as just "model.onnx"."""
    (tmp_path / "model.onnx").write_bytes(b"w")
    assert layouts.is_downloaded(tmp_path, "some-org/model") is True


def test_finds_an_ollama_style_tagged_model(tmp_path):
    """ "llama3:8b" can land as the untagged base name."""
    d = tmp_path / "llama3"
    d.mkdir()
    (d / "blob").write_bytes(b"w")
    assert layouts.is_downloaded(tmp_path, "llama3:8b") is True


def test_absent_model_is_not_downloaded(tmp_path):
    assert layouts.is_downloaded(tmp_path, "nope/not-here") is False


def test_missing_cache_root_is_not_downloaded(tmp_path):
    assert layouts.is_downloaded(tmp_path / "does-not-exist", "x") is False


@pytest.mark.parametrize("model", ["", "   ", "/"])
def test_empty_model_id_is_not_downloaded(tmp_path, model):
    assert layouts.is_downloaded(tmp_path, model) is False


# ── Partials are NOT downloaded ─────────────────────────────────────────


@pytest.mark.parametrize("suffix", [".part", ".tmp", ".incomplete", ".download"])
def test_a_partial_file_is_not_downloaded(tmp_path, suffix):
    """Reporting a partial as present gives a model that fails at load with no explanation."""
    (tmp_path / f"tiny-en{suffix}").write_bytes(b"half")
    assert layouts.is_downloaded(tmp_path, "tiny-en") is False


def test_an_hf_dir_with_only_incomplete_blobs_is_not_downloaded(tmp_path):
    """The real interrupted-hub-fetch shape."""
    blobs = tmp_path / "models--openai--whisper-large-v3" / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "deadbeef.incomplete").write_bytes(b"partial")
    assert layouts.is_downloaded(tmp_path, "openai/whisper-large-v3") is False


def test_a_zero_byte_file_is_not_downloaded(tmp_path):
    """A touched placeholder is not a model."""
    (tmp_path / "tiny-en.onnx").write_bytes(b"")
    assert layouts.is_downloaded(tmp_path, "tiny-en") is False


def test_a_dir_with_one_real_file_beside_partials_is_downloaded(tmp_path):
    """A resumed fetch that finished one file is genuinely partly present."""
    d = tmp_path / "models--org--m" / "blobs"
    d.mkdir(parents=True)
    (d / "a.incomplete").write_bytes(b"x")
    (d / "b").write_bytes(b"real")
    assert layouts.is_downloaded(tmp_path, "org/m") is True


def test_an_empty_directory_is_not_downloaded(tmp_path):
    (tmp_path / "models--org--m").mkdir(parents=True)
    assert layouts.is_downloaded(tmp_path, "org/m") is False


# ── Multiple layouts (the disk-leak shape) ──────────────────────────────


def test_reports_every_layout_that_holds_the_model(tmp_path):
    """Two copies means two fetch paths — surfaced, not hidden."""
    snap = tmp_path / "models--org--m" / "snapshots" / "r1"
    snap.mkdir(parents=True)
    (snap / "w.bin").write_bytes(b"w")
    native = tmp_path / "org" / "m"
    native.mkdir(parents=True)
    (native / "w.bin").write_bytes(b"w")
    found = layouts.downloaded_layouts(tmp_path, "org/m")
    assert len(found) == 2


def test_downloaded_layouts_is_empty_when_absent(tmp_path):
    assert layouts.downloaded_layouts(tmp_path, "org/m") == []


# ── Deleting is greedy ──────────────────────────────────────────────────


def test_delete_removes_every_layout(tmp_path):
    """Freeing one of two copies is the disk-never-frees bug in a different costume."""
    snap = tmp_path / "models--org--m" / "snapshots" / "r1"
    snap.mkdir(parents=True)
    (snap / "w.bin").write_bytes(b"w")
    (tmp_path / "m.onnx").write_bytes(b"w")
    removed = layouts.delete_all_layouts(tmp_path, "org/m")
    assert len(removed) == 2
    assert layouts.is_downloaded(tmp_path, "org/m") is False


def test_delete_also_removes_partials(tmp_path):
    """A cancelled download must not linger invisibly."""
    (tmp_path / "tiny-en.part").write_bytes(b"half")
    removed = layouts.delete_all_layouts(tmp_path, "tiny-en")
    assert [p.name for p in removed] == ["tiny-en.part"]


def test_delete_of_an_absent_model_removes_nothing(tmp_path):
    assert layouts.delete_all_layouts(tmp_path, "nope") == []


def test_delete_never_raises_on_an_unremovable_path(tmp_path, monkeypatch):
    """Best-effort per path; the return value says what actually went."""
    (tmp_path / "tiny-en.onnx").write_bytes(b"w")

    def _boom(path):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.unlink", lambda self, **k: _boom(self))
    assert layouts.delete_all_layouts(tmp_path, "tiny-en") == []


def test_delete_does_not_touch_a_different_model(tmp_path):
    (tmp_path / "keep.onnx").write_bytes(b"w")
    (tmp_path / "drop.onnx").write_bytes(b"w")
    layouts.delete_all_layouts(tmp_path, "drop")
    assert (tmp_path / "keep.onnx").exists()


# ── Cleanup candidates (§4.2) ───────────────────────────────────────────


def test_cleanup_candidates_finds_partials(tmp_path):
    (tmp_path / "a.part").write_bytes(b"12345")
    nested = tmp_path / "models--org--m" / "blobs"
    nested.mkdir(parents=True)
    (nested / "b.incomplete").write_bytes(b"1234567890")
    cands = layouts.cleanup_candidates(tmp_path)
    assert {c["path"].rsplit("/", 1)[-1] for c in cands} == {"a.part", "b.incomplete"}


def test_cleanup_candidates_ignores_finished_files(tmp_path):
    (tmp_path / "model.onnx").write_bytes(b"real")
    assert layouts.cleanup_candidates(tmp_path) == []


def test_cleanup_candidates_are_largest_first(tmp_path):
    """The UI shows what's worth reclaiming, so order matters."""
    (tmp_path / "small.part").write_bytes(b"1")
    (tmp_path / "big.part").write_bytes(b"1" * 100)
    assert [c["path"].rsplit("/", 1)[-1] for c in layouts.cleanup_candidates(tmp_path)] == [
        "big.part",
        "small.part",
    ]


def test_cleanup_candidates_on_a_missing_root_is_empty(tmp_path):
    assert layouts.cleanup_candidates(tmp_path / "nope") == []


def test_reclaimable_bytes_sums_the_candidates(tmp_path):
    (tmp_path / "a.part").write_bytes(b"1" * 10)
    (tmp_path / "b.tmp").write_bytes(b"1" * 5)
    assert layouts.reclaimable_bytes(tmp_path) == 15


def test_reclaimable_bytes_is_zero_when_clean(tmp_path):
    (tmp_path / "model.onnx").write_bytes(b"real")
    assert layouts.reclaimable_bytes(tmp_path) == 0


# ── The runner's second opinion ─────────────────────────────────────────


def test_runner_trusts_the_probe_when_the_provider_says_no(tmp_path, monkeypatch):
    """The asymmetry that matters: a false NO costs the user gigabytes.

    A provider checking only its own `save()` layout misses a model the HF hub fetched into
    `models--{org}--{name}/`, where the id never appears literally.
    """
    from personalclaw.dashboard import model_downloads as md

    class _Model:
        name = "openai/whisper-large-v3"
        downloaded = False

    monkeypatch.setattr(md, "_list_models_for_provider", lambda n: [_Model()])
    monkeypatch.setattr(md, "_cache_root", lambda n: tmp_path)
    snap = tmp_path / "models--openai--whisper-large-v3" / "snapshots" / "r1"
    snap.mkdir(parents=True)
    (snap / "w.safetensors").write_bytes(b"weights")

    assert md._is_downloaded("whisper", "openai/whisper-large-v3") is True


def test_runner_keeps_saying_no_when_nothing_is_on_disk(tmp_path, monkeypatch):
    """The probe only ever ADDS a yes — a false YES fails at load with no explanation."""
    from personalclaw.dashboard import model_downloads as md

    class _Model:
        name = "tiny-en"
        downloaded = False

    monkeypatch.setattr(md, "_list_models_for_provider", lambda n: [_Model()])
    monkeypatch.setattr(md, "_cache_root", lambda n: tmp_path)
    assert md._is_downloaded("whisper", "tiny-en") is False


def test_runner_trusts_a_provider_yes_without_probing(tmp_path, monkeypatch):
    """Only the provider knows backend-specific layouts, so its YES is authoritative."""
    from personalclaw.dashboard import model_downloads as md

    class _Model:
        name = "tiny-en"
        downloaded = True

    monkeypatch.setattr(md, "_list_models_for_provider", lambda n: [_Model()])
    monkeypatch.setattr(
        md, "_cache_root", lambda n: (_ for _ in ()).throw(AssertionError("should not probe"))
    )
    assert md._is_downloaded("whisper", "tiny-en") is True


def test_runner_survives_a_probe_failure(tmp_path, monkeypatch):
    """A probe error must not break the download decision."""
    from personalclaw.dashboard import model_downloads as md

    monkeypatch.setattr(md, "_list_models_for_provider", lambda n: [])
    monkeypatch.setattr(md, "_cache_root", lambda n: (_ for _ in ()).throw(RuntimeError("boom")))
    assert md._is_downloaded("whisper", "x") is False


# ── The test-suite invariant ────────────────────────────────────────────


def test_every_fs_test_uses_tmp_path():
    """Asserted, not assumed: a destructive test once deleted the developer's real model.

    Rather than grep for path literals (a scan that trips over its own explanatory prose),
    this checks the structural property that actually protects the developer: every test in
    this module that touches the filesystem takes pytest's `tmp_path`, so it cannot resolve a
    real home. A new test that hard-codes a path would not have the fixture.
    """
    import inspect

    import tests.test_local_model_layouts as mod

    fs_verbs = ("write_bytes", "mkdir", "delete_all_layouts", "cleanup_candidates")
    offenders = []
    for name, fn in vars(mod).items():
        if not name.startswith("test_") or not callable(fn):
            continue
        if name == "test_every_fs_test_uses_tmp_path":
            continue  # names the verbs it scans for; excluding it keeps the guard honest
        try:
            src = inspect.getsource(fn)
        except OSError:  # pragma: no cover
            continue
        if (
            any(verb in src for verb in fs_verbs)
            and "tmp_path" not in inspect.signature(fn).parameters
        ):
            offenders.append(name)
    assert not offenders, f"fs-touching tests without tmp_path isolation: {offenders}"

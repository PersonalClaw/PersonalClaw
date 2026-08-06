"""The declarative catalog.json loader + contract (LOCAL-MODEL-MANAGER-V2 §2, LMMV-2).

These are the CALLERS that keep the new contract from being dead code: they drive
``LocalModelProvider._models_from_catalog`` against a fixture ``catalog.json`` and assert
every Success-Criterion-6/7 behavior — an active model, a deprecated model (chip, still
bindable), a ``config_only`` gated model (pyannote-shape, never truncation-flagged), a
non-commercial license (warning flag), and a hand-truncated on-disk case (<60% →
``integrity:truncated``). Plus unit tests for the byte-sum helper, the host token, and the
license sniff.

**No provider apps are git-tracked in this core repo** (`git ls-files apps/` = 0), so the
core-repo deliverable is the mechanism proven with a fixture catalog — the per-app
migration (faster-whisper, sentence-transformers, piper-tts, diarization-*) is a
PersonalClawApps-repo follow-up. Every fs-touching test here takes ``tmp_path`` (the same
suite invariant test_local_model_layouts.py asserts).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.local_models import layouts
from personalclaw.local_models.provider import (
    CapabilityMatrix,
    LocalModel,
    LocalModelProvider,
    _is_non_commercial,
    _matrix_from_dict,
    host_platform_token,
)


class _CatalogProvider(LocalModelProvider):
    """A minimal fixed-catalog provider — exactly the shape the migrated apps will take:
    ``list_models`` is a one-liner over ``_models_from_catalog``."""

    def __init__(self, catalog_path: Path, cache_root: Path | None = None) -> None:
        self._catalog_path = catalog_path
        self._cache_root = cache_root

    @property
    def name(self) -> str:
        return "fixture-provider"

    @property
    def display_name(self) -> str:
        return "Fixture Provider"

    async def is_available(self) -> bool:
        return True

    async def list_models(self) -> list[LocalModel]:
        return self._models_from_catalog(self._catalog_path, cache_root=self._cache_root)

    async def download_model(self, model_name: str) -> bool:
        return True

    async def delete_model(self, model_name: str) -> bool:
        return True


# A catalog exercising every branch the contract must handle. `active-model`'s size_mb
# (10) is what the truncation test writes below-threshold bytes against.
_CARDS = {
    "models": [
        {
            "name": "active-model",
            "label": "An active STT model",
            "status": "active",
            "size_mb": 10,
            "capabilities": ["stt"],
            "source": "Systran/active",
            "license": "MIT",
            "runtime": "ctranslate2",
            "runtime_contract": "ctranslate2>=4",
            "context_tokens": 448,
            "io_mime": {"input": ["audio/wav"], "output": ["text/plain"]},
            "matrix": {"word_timestamps": True, "segment_timestamps": True, "languages": []},
        },
        {
            "name": "old-model",
            "label": "A deprecated model",
            "status": "deprecated",
            "size_mb": 5,
            "capabilities": ["stt"],
            "license": "Apache-2.0",
            "runtime": "ctranslate2",
        },
        {
            "name": "pyannote/pipeline",
            "label": "A gated config-only pipeline",
            "status": "active",
            "size_mb": 0,
            "config_only": True,
            "gated": True,
            "capabilities": ["diarization"],
            "license": "MIT",
            "runtime": "torch",
        },
        {
            "name": "community-model",
            "label": "A non-commercial model",
            "status": "active",
            "size_mb": 200,
            "capabilities": ["diarization"],
            "license": "CC-BY-NC-4.0",
            "runtime": "torch",
        },
    ]
}


def _write_catalog(tmp_path: Path, cards: dict | list) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(cards), "utf-8")
    return path


async def _load(
    tmp_path: Path, *, cards: dict | list | None = None, cache_root: Path | None = None
):
    catalog = _write_catalog(tmp_path, cards if cards is not None else _CARDS)
    return await _CatalogProvider(catalog, cache_root=cache_root).list_models()


# ── host platform token ─────────────────────────────────────────────────


def test_host_platform_token_shape():
    """`<platform>-<arch>`, arch aliases normalized (arm64/x86_64)."""
    tok = host_platform_token()
    assert "-" in tok
    assert tok == tok.lower()
    assert "aarch64" not in tok and "amd64" not in tok


# ── the loader maps every field ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_loads_every_card_and_maps_fields(tmp_path):
    models = await _load(tmp_path)
    assert [m.name for m in models] == [
        "active-model",
        "old-model",
        "pyannote/pipeline",
        "community-model",
    ]
    active = models[0]
    assert active.description == "An active STT model"  # label → description
    assert active.runtime == "ctranslate2"
    assert active.runtime_contract == "ctranslate2>=4"
    assert active.license == "MIT"
    assert active.context_tokens == 448
    assert active.io_mime == {"input": ["audio/wav"], "output": ["text/plain"]}
    assert isinstance(active.matrix, CapabilityMatrix)
    assert active.matrix.word_timestamps is True
    assert active.matrix.segment_timestamps is True


@pytest.mark.asyncio
async def test_fields_flow_through_to_dict(tmp_path):
    """The API serializes via to_dict() with no handler change (LMMV §2.1)."""
    models = await _load(tmp_path)
    d = models[0].to_dict()
    assert d["runtime"] == "ctranslate2"
    assert d["license"] == "MIT"
    assert d["status"] == "active"
    assert d["matrix"]["word_timestamps"] is True
    # A model with no matrix serializes it as None, not a crash.
    assert models[1].to_dict()["matrix"] is None


# ── Success Criterion 6: deprecated shows a chip but stays bindable ───────


@pytest.mark.asyncio
async def test_deprecated_model_kept_with_status(tmp_path):
    models = await _load(tmp_path)
    old = next(m for m in models if m.name == "old-model")
    assert old.status == "deprecated"  # FE renders a chip
    assert old in models  # still listed → still bindable


# ── Success Criterion 7: non-commercial license flagged ──────────────────


@pytest.mark.asyncio
async def test_non_commercial_license_flagged(tmp_path):
    models = await _load(tmp_path)
    community = next(m for m in models if m.name == "community-model")
    assert community.non_commercial is True  # warning chip at bind time
    assert next(m for m in models if m.name == "active-model").non_commercial is False


# ── truncation detection (§2.3) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_truncated_when_on_disk_below_floor(tmp_path):
    """A finished, non-config-only model with <60% of its declared bytes → truncated."""
    cache = tmp_path / "cache"
    cache.mkdir()
    # active-model declares 10 MB; write ~1 MB (well under the 60% = 6 MB floor).
    (cache / "active-model.bin").write_bytes(b"x" * 1_000_000)
    models = await _load(tmp_path, cache_root=cache)
    active = next(m for m in models if m.name == "active-model")
    assert active.downloaded is True
    assert active.integrity == "truncated"


@pytest.mark.asyncio
async def test_full_size_is_not_truncated(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "active-model.bin").write_bytes(b"x" * 10_000_000)  # exactly the declared size
    models = await _load(tmp_path, cache_root=cache)
    active = next(m for m in models if m.name == "active-model")
    assert active.downloaded is True
    assert active.integrity == ""


@pytest.mark.asyncio
async def test_config_only_never_truncated(tmp_path):
    """pyannote-shape: a pipeline repo has no local weights, so a tiny cache is fine."""
    cache = tmp_path / "cache"
    cache.mkdir()
    d = cache / "models--pyannote--pipeline" / "snapshots" / "r1"
    d.mkdir(parents=True)
    (d / "config.yaml").write_bytes(b"x" * 100)  # tiny, but config_only
    models = await _load(tmp_path, cache_root=cache)
    pipeline = next(m for m in models if m.name == "pyannote/pipeline")
    assert pipeline.downloaded is True
    assert pipeline.integrity == ""  # not flagged despite tiny footprint


@pytest.mark.asyncio
async def test_absent_model_not_downloaded_and_not_truncated(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    models = await _load(tmp_path, cache_root=cache)
    active = next(m for m in models if m.name == "active-model")
    assert active.downloaded is False
    assert active.integrity == ""


@pytest.mark.asyncio
async def test_active_download_suppresses_truncation(tmp_path):
    """A model mid-download is legitimately partial — never flag it truncated."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "active-model.bin").write_bytes(b"x" * 1_000_000)
    catalog = _write_catalog(tmp_path, _CARDS)
    models = _CatalogProvider(catalog)._models_from_catalog(
        catalog, cache_root=cache, active_downloads={"active-model"}
    )
    active = next(m for m in models if m.name == "active-model")
    assert active.downloaded is True
    assert active.integrity == ""


# ── platform filtering (§4) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_platforms_filter_against_host(tmp_path):
    host = host_platform_token()
    cards = {
        "models": [
            {"name": "keep-empty", "size_mb": 1, "platforms": []},
            {"name": "keep-host", "size_mb": 1, "platforms": [host]},
            {"name": "drop-other", "size_mb": 1, "platforms": ["nonexistent-9000"]},
        ]
    }
    models = await _load(tmp_path, cards=cards)
    names = {m.name for m in models}
    assert names == {"keep-empty", "keep-host"}  # empty = all hosts; other host dropped


# ── fail-soft: missing / malformed catalog ───────────────────────────────


@pytest.mark.asyncio
async def test_missing_catalog_returns_empty(tmp_path):
    provider = _CatalogProvider(tmp_path / "does-not-exist.json")
    assert await provider.list_models() == []


@pytest.mark.asyncio
async def test_malformed_json_returns_empty(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text("{not json", "utf-8")
    assert await _CatalogProvider(path).list_models() == []


@pytest.mark.asyncio
async def test_one_bad_card_does_not_blank_the_list(tmp_path):
    """A single card missing 'name' is skipped; the rest still load."""
    cards = {
        "models": [
            {"label": "no name here", "size_mb": 1},  # bad — no name
            {"name": "good", "size_mb": 1},
            "not-even-a-dict",
        ]
    }
    models = await _load(tmp_path, cards=cards)
    assert [m.name for m in models] == ["good"]


@pytest.mark.asyncio
async def test_bare_list_catalog_shape(tmp_path):
    """The loader tolerates a top-level list as well as {"models": [...]}."""
    models = await _load(tmp_path, cards=[{"name": "solo", "size_mb": 1}])
    assert [m.name for m in models] == ["solo"]


# ── the helpers, unit-tested (Part 3) ────────────────────────────────────


def test_on_disk_bytes_sums_a_directory(tmp_path):
    d = tmp_path / "models--org--m" / "snapshots" / "r1"
    d.mkdir(parents=True)
    (d / "a.bin").write_bytes(b"x" * 100)
    (d / "b.bin").write_bytes(b"x" * 50)
    assert layouts.on_disk_bytes(tmp_path, "org/m") == 150


def test_on_disk_bytes_sums_a_direct_file(tmp_path):
    (tmp_path / "voice.onnx").write_bytes(b"x" * 42)
    assert layouts.on_disk_bytes(tmp_path, "voice") == 42


def test_on_disk_bytes_sums_every_layout(tmp_path):
    """Two fetch paths → both counted, mirroring downloaded_layouts."""
    snap = tmp_path / "models--org--m" / "snapshots" / "r1"
    snap.mkdir(parents=True)
    (snap / "w.bin").write_bytes(b"x" * 10)
    native = tmp_path / "org" / "m"
    native.mkdir(parents=True)
    (native / "w.bin").write_bytes(b"x" * 20)
    assert layouts.on_disk_bytes(tmp_path, "org/m") == 30


def test_on_disk_bytes_zero_when_absent(tmp_path):
    assert layouts.on_disk_bytes(tmp_path, "nope") == 0


@pytest.mark.parametrize(
    "license_id,expected",
    [
        ("CC-BY-NC-4.0", True),
        ("cc-by-nc-sa-4.0", True),
        ("some-NC-license", True),
        ("MIT", False),
        ("Apache-2.0", False),
        ("", False),
    ],
)
def test_non_commercial_sniff(license_id, expected):
    assert _is_non_commercial(license_id) is expected


def test_non_commercial_explicit_flag_wins():
    """An explicit card flag overrides the license sniff (either direction)."""
    assert _is_non_commercial("MIT", explicit=True) is True
    assert _is_non_commercial("CC-BY-NC-4.0", explicit=False) is False


def test_matrix_from_dict_ignores_unknown_keys():
    m = _matrix_from_dict({"word_timestamps": True, "not_a_field": 9, "hotword_budget": 224})
    assert m.word_timestamps is True
    assert m.hotword_budget == 224
    assert not hasattr(m, "not_a_field")

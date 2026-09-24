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

import dataclasses
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
    """A minimal fixed-catalog provider in the shape a migrated app ACTUALLY takes.

    ``list_models`` is a bare one-liner over ``_models_from_catalog`` — no ``cache_root``
    keyword — because that is what the one shipped adopter
    (``PersonalClawApps/voice-clone-tts/provider.py``) writes, and what this helper's own
    docstring tells the next app to write. This class used to pass ``cache_root=`` and so
    exercised a path production never took: the helper skipped the disk probe entirely when
    the keyword was absent, which is how ``integrity`` (and therefore the FE's Repair button)
    stayed unreachable through a fully-green catalog suite. The root now arrives the way it
    does in a real app, via ``cache_dir()``.
    """

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

    def cache_dir(self) -> str | None:
        return str(self._cache_root) if self._cache_root is not None else None

    async def list_models(self) -> list[LocalModel]:
        return self._models_from_catalog(self._catalog_path)

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
    """Drive the provider's real ``list_models()``.

    ``cache_root`` defaults to an EMPTY dir under ``tmp_path`` rather than to None: with the
    disk probe now unconditional, a provider naming no cache dir falls back to the shared
    models root under the real home, which the suite's model-root rail refuses outright. An
    empty tmp dir is the same answer ("nothing is downloaded") reached safely.
    """
    catalog = _write_catalog(tmp_path, cards if cards is not None else _CARDS)
    root = cache_root if cache_root is not None else tmp_path / "empty-cache"
    root.mkdir(parents=True, exist_ok=True)
    return await _CatalogProvider(catalog, cache_root=root).list_models()


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
async def test_unfinished_fetch_suppresses_truncation(tmp_path):
    """A model mid-download is legitimately partial — never flag it truncated.

    The excuse is read off DISK (a `.incomplete` beside the finished bytes), so no caller has
    to inject a set of in-flight names. The injected set this replaces was filled by nothing
    but its own test, so it would have been an unarmed guard the moment the disk probe below
    started running for real.
    """
    cache = tmp_path / "cache"
    # A two-shard HF fetch in progress: shard one renamed and finished, shard two still
    # `.incomplete`. `is_downloaded` says yes (real bytes are present) and the total is far
    # under the 60% floor — the exact shape that produced a false Repair button.
    blobs = cache / layouts.hf_repo_dirname("active-model") / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "aaaa").write_bytes(b"x" * 1_000_000)
    (blobs / "bbbb.incomplete").write_bytes(b"x" * 500_000)
    models = await _load(tmp_path, cache_root=cache)
    active = next(m for m in models if m.name == "active-model")
    assert active.downloaded is True
    assert layouts.has_partial(cache, "active-model") is True
    assert active.integrity == ""


@pytest.mark.asyncio
async def test_a_crashed_fetchs_leftovers_are_swept_then_the_model_reads_truncated(tmp_path):
    """Vacuity guard for the test above: the excuse must be the PARTIAL, not the layout.

    Suppressing on any HF-shaped directory would pass the previous test while silencing the
    detector for every hub-fetched model there is — which is most of them. So sweep the
    partial and assert the same tree flips to ``truncated``.
    """
    cache = tmp_path / "cache"
    blobs = cache / layouts.hf_repo_dirname("active-model") / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "aaaa").write_bytes(b"x" * 1_000_000)
    (blobs / "bbbb.incomplete").write_bytes(b"x" * 500_000)
    (blobs / "bbbb.incomplete").unlink()  # what "Reclaim N GB" does
    models = await _load(tmp_path, cache_root=cache)
    active = next(m for m in models if m.name == "active-model")
    assert active.downloaded is True
    assert active.integrity == "truncated"


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


# ── the rails: no catalog field may exist without a writer (#1776) ───────


@pytest.mark.asyncio
async def test_the_shipped_one_liner_reaches_the_repair_button(tmp_path):
    """The regression rail for #1776, asserted on the WIRE shape the FE reads.

    ``web/src/pages/settings/ModelsPanel.tsx`` gates its Repair action on
    ``model.integrity === 'truncated'``, and ``integrity`` has exactly one writer:
    ``_apply_disk_state``. Before the fix that writer was unreachable from the documented
    one-liner (``self._models_from_catalog(path)``), so the button could not render in any
    build no matter how many apps adopted a ``catalog.json``. Asserted through ``to_dict()``
    rather than the dataclass because the dataclass field being set is not the same claim as
    the key crossing ``/api/models/available``.
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "active-model.bin").write_bytes(b"x" * 1_000_000)  # declares 10 MB
    catalog = _write_catalog(tmp_path, _CARDS)
    provider = _CatalogProvider(catalog, cache_root=cache)

    wire = [m.to_dict() for m in await provider.list_models()]
    active = next(d for d in wire if d["name"] == "active-model")
    assert active["downloaded"] is True, "weights are on disk; the row must not offer Download"
    assert (
        active["integrity"] == "truncated"
    ), "the FE gates Repair on this exact string — an empty integrity is the whole bug"


def test_every_local_model_field_has_a_writer(tmp_path):
    """DERIVED: a field on ``LocalModel`` that neither a card nor the disk pass fills is dead.

    ``#1776`` is one instance of a general shape — a key the frontend reads and nothing
    writes. So partition ``LocalModel``'s fields BY EXECUTION rather than by a hand-kept
    list: run a maximal card through ``_model_from_card`` to learn what a catalog can express,
    run a truncated on-disk model through the default listing path to learn what disk adds,
    and require the union to be every field. A new field wired into ``to_dict()`` and the
    ``api.ts`` type but into no producer makes the residue non-empty and reds here.
    """
    maximal = {
        "name": "wired",
        "size_mb": 10,
        "label": "described",
        "capabilities": ["stt"],
        "gated": True,
        "source": "org/wired",
        "matrix": {"word_timestamps": True},
        "runtime": "ctranslate2",
        "runtime_contract": "ctranslate2>=4",
        "license": "CC-BY-NC-4.0",
        "context_tokens": 448,
        "output_tokens": 64,
        "io_mime": {"input": ["audio/wav"]},
        "status": "deprecated",
        "config_only": True,
    }
    from_card = LocalModelProvider._model_from_card(maximal, host_platform_token())
    default = LocalModel(name="")  # empty, so a written `name` reads as written
    card_writes = {
        f.name
        for f in dataclasses.fields(LocalModel)
        if getattr(from_card, f.name) != getattr(default, f.name)
    }

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "active-model.bin").write_bytes(b"x" * 1_000_000)
    catalog = _write_catalog(tmp_path, _CARDS)
    truncated = next(
        m
        for m in _CatalogProvider(catalog, cache_root=cache)._models_from_catalog(catalog)
        if m.name == "active-model"
    )
    disk_writes = {
        f.name
        for f in dataclasses.fields(LocalModel)
        if getattr(truncated, f.name) != getattr(LocalModel(name="active-model"), f.name)
    } - card_writes

    # Vacuity: an empty partition on either side would make the residue check trivial, and
    # `disk_writes` is the side that was empty in production for the whole life of the bug.
    assert len(card_writes) >= 14, sorted(card_writes)
    assert disk_writes == {"downloaded", "integrity"}, sorted(disk_writes)

    residue = {f.name for f in dataclasses.fields(LocalModel)} - card_writes - disk_writes
    assert not residue, (
        f"LocalModel.{sorted(residue)} is read on the wire (to_dict) but no catalog card and "
        f"no disk probe ever writes it — the #1776 shape. Give it a producer or delete it."
    )


@pytest.mark.parametrize("escaping", ["../SECRETS", "../../etc", "a/../../b"])
def test_a_card_name_cannot_reach_outside_the_cache_root(tmp_path, escaping):
    """A catalog card is APP-authored input joined onto a filesystem root (ARCC SAX-04).

    Measured before the guard: a card named ``"../SECRETS"`` made ``downloaded_layouts``
    return ``<root>/../SECRETS`` and ``on_disk_bytes`` sum 4096 bytes from it — and the same
    candidate list is what ``delete_all_layouts`` sweeps with ``rmtree``. Fails closed: no
    candidates, so the model simply reads as absent.
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    outside = tmp_path / "SECRETS"
    outside.mkdir()
    (outside / "id_rsa").write_bytes(b"x" * 4096)

    assert layouts.escapes_root(escaping) is True
    assert layouts.candidate_paths(cache, escaping) == []
    assert layouts.is_downloaded(cache, escaping) is False
    assert layouts.on_disk_bytes(cache, escaping) == 0
    assert layouts.downloaded_layouts(cache, escaping) == []
    assert layouts.delete_all_layouts(cache, escaping) == []
    assert (outside / "id_rsa").exists(), "the refusal must happen before anything is touched"


def test_an_ordinary_slashed_model_id_is_not_mistaken_for_an_escape(tmp_path):
    """Vacuity guard: the containment check must not refuse a normal namespaced repo id."""
    assert layouts.escapes_root("sentence-transformers/all-MiniLM-L6-v2") is False
    native = tmp_path / "sentence-transformers" / "all-MiniLM-L6-v2"
    native.mkdir(parents=True)
    (native / "w.bin").write_bytes(b"x" * 10)
    assert layouts.is_downloaded(tmp_path, "sentence-transformers/all-MiniLM-L6-v2") is True


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

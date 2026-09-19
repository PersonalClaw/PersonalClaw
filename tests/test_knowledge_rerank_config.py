"""KBVS-2 — the relevance-reranker config knobs round-trip through config, end to end.

Mirrors ``test_knowledge_similarity_config.py``'s shape (KL-13) for the same reason: every
link of the round-trip has its own failure mode.

* dataclass + ``_meta``  — a field with no label/help is invisible in the settings UI
* ``load()``             — the "round-tripped knob nothing reads" defect: the field is on
                           the dataclass and in ``to_dict()`` so a save/load test passes,
                           but ``load()`` never maps it and a configured value silently
                           reverts to the default on the next read
* ``to_dict()``          — automatic via ``asdict``, asserted rather than assumed
* ``_EDITABLE_CONFIG``   — without an entry the PATCH endpoint refuses the key
* a reader               — the value must arrive at a caller whose only input is
                           ``AppConfig.load()``
"""

import json
from dataclasses import fields
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.config.loader import AppConfig, KnowledgeConfig
from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

_FIELDS: tuple[tuple[str, object], ...] = (
    ("rerank_enabled", False),
    ("rerank_candidates", 20),
)
_KEYS = tuple(f"knowledge.{name}" for name, _ in _FIELDS)


@pytest.fixture()
def cfg_file(tmp_path, monkeypatch):
    """An isolated ``config.json``. Never touches the real home."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    with patch("personalclaw.config.loader.config_path", return_value=p):
        yield p


def _write(cfg_file, **knowledge_values):
    cfg_file.write_text(json.dumps({"knowledge": knowledge_values}), encoding="utf-8")


# ── 1. dataclass + _meta, and the documented defaults ─────────────────────────────────────


def test_fields_are_declared_with_meta():
    declared = {f.name: f for f in fields(KnowledgeConfig)}
    for name, _default in _FIELDS:
        assert name in declared, f"KnowledgeConfig is missing {name}"
        meta = declared[name].metadata
        assert meta.get("label"), f"{name} has no label"
        assert meta.get("help"), f"{name} has no help text"


def test_defaults_load_from_an_empty_config(cfg_file):
    """OFF unless configured (done_when's own wording) — an empty config.json must keep
    reranking off, not merely default to some other value."""
    cfg = AppConfig.load()
    for name, default in _FIELDS:
        assert getattr(cfg.knowledge, name) == default, name
    assert cfg.knowledge.rerank_enabled is False


# ── 2. to_dict() ──────────────────────────────────────────────────────────────────────────


def test_fields_appear_in_to_dict(cfg_file):
    d = AppConfig.load().to_dict()["knowledge"]
    for name, default in _FIELDS:
        assert name in d, f"to_dict()['knowledge'] missing {name}"
        assert d[name] == default


def test_to_dict_output_reloads_unchanged(cfg_file):
    """The file to_dict() writes must be one load() reads back identically — catches an
    to_dict() key load() does not map, where a save looks successful and the value is gone
    on the next read."""
    _write(cfg_file, rerank_enabled=True, rerank_candidates=35)
    once = AppConfig.load()
    cfg_file.write_text(json.dumps(once.to_dict()), encoding="utf-8")
    twice = AppConfig.load()
    for name, _default in _FIELDS:
        assert getattr(twice.knowledge, name) == getattr(once.knowledge, name), name


# ── 3. _EDITABLE_CONFIG PATCH allowlist ───────────────────────────────────────────────────


def test_editable_config_declares_both_keys():
    for key in _KEYS:
        assert key in _EDITABLE_CONFIG, f"{key} is not PATCH-editable"


def test_editable_config_types_match_the_dataclass():
    assert _EDITABLE_CONFIG["knowledge.rerank_enabled"]["type"] == "bool"
    assert _EDITABLE_CONFIG["knowledge.rerank_candidates"]["type"] == "int"


def test_editable_config_keys_name_real_dataclass_fields():
    """An allowlist key with no matching field is accepted by the PATCH path, written into
    config.json, and then ignored by load() forever — a setting that reports success and
    does nothing."""
    declared = {f.name for f in fields(KnowledgeConfig)}
    for key in _KEYS:
        section, _, field_name = key.partition(".")
        assert section == "knowledge"
        assert field_name in declared, f"{key} names no KnowledgeConfig field"


def test_editable_config_candidates_bounds_are_sane_and_contain_the_default():
    spec = _EDITABLE_CONFIG["knowledge.rerank_candidates"]
    lo, hi = spec["min"], spec["max"]
    assert lo < hi
    assert lo <= 20 <= hi


# ── 4. a reader actually receives the value ───────────────────────────────────────────────


def test_configured_values_are_read_back_from_disk(cfg_file):
    """A hand-written config.json value must survive load(). Drop either field from load()'s
    knowledge mapping and this reverts to the default and goes red, while the dataclass and
    to_dict() tests above still pass."""
    _write(cfg_file, rerank_enabled=True, rerank_candidates=42)
    k = AppConfig.load().knowledge
    assert k.rerank_enabled is True
    assert k.rerank_candidates == 42


def test_a_reader_receives_the_configured_rerank_knobs():
    """The value arrives at a caller whose only input is ``AppConfig.load()`` — the shape
    ``HybridRetriever._rerank_wanted`` / ``_apply_rerank`` actually use."""
    stub = AppConfig()
    stub.knowledge.rerank_enabled = True
    stub.knowledge.rerank_candidates = 33

    def read(key: str) -> object:
        from personalclaw.config.loader import AppConfig as Loaded

        return getattr(Loaded.load().knowledge, key.partition(".")[2])

    with patch.object(AppConfig, "load", staticmethod(lambda *a, **k: stub)):
        assert read("knowledge.rerank_enabled") is True
        assert read("knowledge.rerank_candidates") == 33


# ── 5. hostile hand-edited values ──────────────────────────────────────────────────────────


def test_zero_candidates_takes_the_shipped_default(cfg_file):
    """A configured 0 falls back to the default via the `or <default>` idiom shared with
    every sibling int knob in this dataclass (`max_mentions_per_claim`, `synthesis_window`,
    ...) — a window of zero is not a smaller window, it is no window at all."""
    _write(cfg_file, rerank_candidates=0)
    assert AppConfig.load().knowledge.rerank_candidates == 20


def test_a_wrongly_typed_enabled_value_is_stripped_to_the_default(cfg_file):
    """A non-boolean value fails the generated JSON Schema's `type: boolean` check
    (`config.schema` derives it from this exact dataclass field) and is stripped by
    `config.validation._apply_field_default` BEFORE `load()`'s per-field mapping ever
    runs `bool(...)` on it — so this degrades to the default rather than being coerced
    by truthiness, the same protection every other typed field in this dataclass gets."""
    _write(cfg_file, rerank_enabled="yes")
    assert AppConfig.load().knowledge.rerank_enabled is False


def test_a_wrongly_typed_candidates_value_is_stripped_to_the_default(cfg_file):
    """A non-integer value fails the generated schema's `type: integer` check and is
    stripped before `load()` ever calls `int(...)` on it, so this degrades to the
    default rather than raising — one typo must not make the whole config file
    unloadable. Distinct from the zero case above: zero PASSES the integer type check
    and is handled by load()'s own `or <default>` idiom instead."""
    _write(cfg_file, rerank_candidates="a lot")
    assert AppConfig.load().knowledge.rerank_candidates == 20


# ── 6. the PATCH endpoint, driven for real ────────────────────────────────────────────────


def _patch_app() -> web.Application:
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    return app


async def _send(client, path, value):
    return await client.patch("/api/config/personalclaw", json={"path": path, "value": value})


@pytest.mark.asyncio
async def test_patch_writes_rerank_enabled_and_load_reads_it_back(cfg_file):
    async with TestClient(TestServer(_patch_app())) as client:
        resp = await _send(client, "knowledge.rerank_enabled", True)
        assert resp.status == 200, await resp.text()
    assert AppConfig.load().knowledge.rerank_enabled is True


@pytest.mark.asyncio
async def test_patch_rejects_a_candidates_value_outside_the_allowlist_bounds(cfg_file):
    async with TestClient(TestServer(_patch_app())) as client:
        resp = await _send(client, "knowledge.rerank_candidates", 10_000)
        assert resp.status >= 400, "an out-of-range PATCH must be refused, not clamped"

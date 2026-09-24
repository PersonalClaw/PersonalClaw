"""KL-13: the similarity-edge knobs must round-trip through config, end to end.

The atom's clause is "the cosine floor, top-K and degree cap round-trip through config".
This file proves the WHOLE chain for each of the three fields, because every link has its own
failure mode and the project has shipped each of them at least once:

* dataclass + ``_meta``      — a field with no label/help is invisible in the settings UI
* ``load()``                 — the "round-tripped knob nothing reads" defect: the field is on
                               the dataclass and in ``to_dict()``, so a save/load test passes,
                               but ``load()`` never maps it, so a configured value silently
                               reverts to the default on the next read
* ``to_dict()``              — automatic via ``asdict``, which is exactly why it is asserted
                               rather than assumed
* ``_EDITABLE_CONFIG``       — without an entry the PATCH endpoint refuses the key
* a reader                   — the value must arrive at a caller that only sees
                               ``AppConfig.load()``

``config.json`` is hand-editable and ``_EDITABLE_CONFIG``'s bounds only guard the PATCH path,
so the hostile-value cases are tested too. The ZERO case and the NEGATIVE case are deliberately
SEPARATE tests: a single test written with ``0`` cannot tell the ``or <default>`` fallback apart
from a clamp, and it keeps passing with the clamp deleted.
"""

import json
from dataclasses import fields
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.config.loader import AppConfig, KnowledgeConfig
from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

# The three fields, their documented defaults, and their PATCH-allowlist keys.
_FIELDS: tuple[tuple[str, object], ...] = (
    ("similarity_min_score", 0.55),
    ("similarity_top_k", 8),
    ("similarity_degree_cap", 32),
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
    """Hand-edit ``config.json``'s knowledge section, the way an owner would."""
    cfg_file.write_text(json.dumps({"knowledge": knowledge_values}), encoding="utf-8")


# ── 1. dataclass + _meta, and the documented defaults ─────────────────────────────────────


def test_fields_are_declared_with_meta():
    """Each field exists with a label and a help string, or the settings UI cannot show it."""
    declared = {f.name: f for f in fields(KnowledgeConfig)}
    for name, _default in _FIELDS:
        assert name in declared, f"KnowledgeConfig is missing {name}"
        meta = declared[name].metadata
        assert meta.get("label"), f"{name} has no label"
        assert meta.get("help"), f"{name} has no help text"


def test_min_score_help_states_why_it_sits_above_the_retrieval_floor():
    """An edge is a stronger claim than a search hit; the field must say so.

    Without this, the next reader sees two similarity floors with no explanation of why they
    differ and "unifies" them back to one — which is the bug the two values exist to avoid.
    """
    help_text = {f.name: f.metadata.get("help", "") for f in fields(KnowledgeConfig)}[
        "similarity_min_score"
    ]
    assert "0.25" in help_text, "help must name the vector arm's retrieval floor"
    assert "edge" in help_text.lower()


def test_defaults_load_from_an_empty_config(cfg_file):
    """An empty config.json yields the documented defaults."""
    cfg = AppConfig.load()
    for name, default in _FIELDS:
        assert getattr(cfg.knowledge, name) == default, name


# ── 2. to_dict() ──────────────────────────────────────────────────────────────────────────


def test_fields_appear_in_to_dict(cfg_file):
    """to_dict() goes through asdict, so this is a verification, not an assumption."""
    d = AppConfig.load().to_dict()["knowledge"]
    for name, default in _FIELDS:
        assert name in d, f"to_dict()['knowledge'] missing {name}"
        assert d[name] == default


def test_to_dict_output_reloads_unchanged(cfg_file):
    """The file to_dict() writes must be one load() reads back identically.

    Catches the asymmetric case where to_dict() emits a key under a name load() does not map:
    the save looks successful and the value is gone on the next read.
    """
    _write(cfg_file, similarity_min_score=0.5, similarity_top_k=3, similarity_degree_cap=12)
    once = AppConfig.load()
    cfg_file.write_text(json.dumps(once.to_dict()), encoding="utf-8")
    twice = AppConfig.load()
    for name, _default in _FIELDS:
        assert getattr(twice.knowledge, name) == getattr(once.knowledge, name), name


# ── 3. _EDITABLE_CONFIG PATCH allowlist ───────────────────────────────────────────────────


def test_editable_config_declares_all_three_keys():
    """Without an allowlist entry the PATCH endpoint refuses the key outright."""
    for key in _KEYS:
        assert key in _EDITABLE_CONFIG, f"{key} is not PATCH-editable"


def test_editable_config_types_match_the_dataclass():
    """A float knob declared "int" in the allowlist truncates every PATCH silently."""
    assert _EDITABLE_CONFIG["knowledge.similarity_min_score"]["type"] == "float"
    assert _EDITABLE_CONFIG["knowledge.similarity_top_k"]["type"] == "int"
    assert _EDITABLE_CONFIG["knowledge.similarity_degree_cap"]["type"] == "int"


def test_editable_config_keys_name_real_dataclass_fields():
    """The key suffix must be an actual KnowledgeConfig field.

    An allowlist key with no matching field is accepted by the PATCH path, written into
    config.json, and then ignored by load() forever — a setting that reports success and does
    nothing.
    """
    declared = {f.name for f in fields(KnowledgeConfig)}
    for key in _KEYS:
        section, _, field_name = key.partition(".")
        assert section == "knowledge"
        assert field_name in declared, f"{key} names no KnowledgeConfig field"


def test_editable_config_bounds_are_sane_and_contain_the_default():
    """Bounds must admit the shipped default, or the UI cannot re-save what it displays."""
    for name, default in _FIELDS:
        spec = _EDITABLE_CONFIG[f"knowledge.{name}"]
        lo, hi = spec["min"], spec["max"]
        assert lo < hi, name
        assert lo <= default <= hi, f"{name}: default {default} outside [{lo}, {hi}]"


def test_min_score_bounds_reject_a_floor_of_zero_and_anything_above_one():
    """0.0 is not a looser floor, it is NO floor; >1.0 is unsatisfiable for a cosine.

    This path REJECTS rather than clamps, so a 0.0 accepted here would be replaced by the
    shipped default on the next load — a PATCH that reports success and changes nothing.
    """
    spec = _EDITABLE_CONFIG["knowledge.similarity_min_score"]
    assert spec["min"] > 0.0
    assert spec["max"] == 1.0


# ── 4. a reader actually receives the value ───────────────────────────────────────────────


def test_configured_values_are_read_back_from_disk(cfg_file):
    """A hand-written config.json value must survive load().

    This is the "round-tripped knob nothing reads" guard: drop any one of the three from
    load()'s knowledge mapping and its assertion here reverts to the default and goes red,
    while the dataclass and to_dict() tests above still pass.
    """
    _write(cfg_file, similarity_min_score=0.62, similarity_top_k=5, similarity_degree_cap=21)
    k = AppConfig.load().knowledge
    assert k.similarity_min_score == pytest.approx(0.62)
    assert k.similarity_top_k == 5
    assert k.similarity_degree_cap == 21


def test_a_reader_receives_the_configured_edge_knobs():
    """The value arrives at a caller whose only input is ``AppConfig.load()``.

    The similarity pass itself is a sibling atom, so this drives the reader SHAPE the
    knowledge area already uses for the neighbouring knob
    (``knowledge/maintenance.py:max_staleness_secs`` reads
    ``getattr(AppConfig.load().knowledge, ...)``). Attribute names come from the
    ``_EDITABLE_CONFIG`` keys, so a rename on either side breaks this.
    """
    stub = AppConfig()
    stub.knowledge.similarity_min_score = 0.71
    stub.knowledge.similarity_top_k = 4
    stub.knowledge.similarity_degree_cap = 17

    def read(key: str) -> object:
        from personalclaw.config.loader import AppConfig as Loaded

        return getattr(Loaded.load().knowledge, key.partition(".")[2])

    with patch.object(AppConfig, "load", staticmethod(lambda *a, **k: stub)):
        assert read("knowledge.similarity_min_score") == pytest.approx(0.71)
        assert read("knowledge.similarity_top_k") == 4
        assert read("knowledge.similarity_degree_cap") == 17


# ── 5. hostile hand-edited values — ZERO and NEGATIVE, deliberately separate ───────────────


def test_zero_values_take_the_shipped_default(cfg_file):
    """A configured 0 falls back to the default via this block's ``or <default>`` idiom.

    Kept apart from the negative case ON PURPOSE. This test passes with every clamp in
    load() deleted, so it can only ever prove the fallback — never a floor.
    """
    _write(cfg_file, similarity_min_score=0, similarity_top_k=0, similarity_degree_cap=0)
    k = AppConfig.load().knowledge
    assert k.similarity_min_score == pytest.approx(0.55)
    assert k.similarity_top_k == 8
    assert k.similarity_degree_cap == 32


def test_negative_values_are_floored_not_passed_through(cfg_file):
    """A negative is a typo with no reading at all, and must never reach the pass.

    The expectations differ from the zero case on purpose, which is what makes this test able
    to fail: a negative cosine floor resolves to the DEFAULT (below zero there is no floor to
    loosen), while a negative count clamps to the minimum useful value of 1.
    """
    _write(cfg_file, similarity_min_score=-0.5, similarity_top_k=-3, similarity_degree_cap=-9)
    k = AppConfig.load().knowledge
    assert k.similarity_min_score == pytest.approx(0.55)
    assert k.similarity_top_k == 1
    assert k.similarity_degree_cap == 1


def test_a_floor_above_one_clamps_to_one(cfg_file):
    """Above 1.0 no cosine can satisfy the floor, which would mean zero edges and no reason
    given. Clamping to 1.0 preserves the coherent reading: near-identical items only."""
    _write(cfg_file, similarity_min_score=5.0)
    assert AppConfig.load().knowledge.similarity_min_score == pytest.approx(1.0)


def test_unparseable_values_degrade_to_defaults_without_raising(cfg_file):
    """One typo must not make the whole config file unloadable."""
    _write(
        cfg_file,
        similarity_min_score="point four",
        similarity_top_k="eight",
        similarity_degree_cap=None,
    )
    k = AppConfig.load().knowledge
    assert k.similarity_min_score == pytest.approx(0.55)
    assert k.similarity_top_k == 8
    assert k.similarity_degree_cap == 32


# ── 6. the PATCH endpoint, driven for real ────────────────────────────────────────────────
#
# The allowlist assertions above are structural: they prove an entry is DECLARED. These drive
# the endpoint, because a declared entry can still be unreachable, and a PATCH that reports
# success while load() then discards the value is the exact "setting that changes nothing"
# defect the allowlist exists to prevent.


def _patch_app() -> web.Application:
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    return app


async def _send(client, path, value):
    return await client.patch("/api/config/personalclaw", json={"path": path, "value": value})


@pytest.mark.asyncio
async def test_patch_accepts_each_knob_and_load_reads_it_back(cfg_file):
    """The whole contract in one pass: PATCH → config.json → load()."""
    async with TestClient(TestServer(_patch_app())) as c:
        for path, value in (
            ("knowledge.similarity_min_score", 0.55),
            ("knowledge.similarity_top_k", 12),
            ("knowledge.similarity_degree_cap", 40),
        ):
            resp = await _send(c, path, value)
            assert resp.status == 200, f"{path} rejected: {await resp.text()}"

    k = AppConfig.load().knowledge
    assert k.similarity_min_score == pytest.approx(0.55)
    assert k.similarity_top_k == 12
    assert k.similarity_degree_cap == 40


@pytest.mark.asyncio
async def test_patch_refuses_a_zero_floor_rather_than_silently_defaulting_it(cfg_file):
    """This path rejects instead of clamping, so 0.0 must come back as an error.

    Accepting it would write a value load() replaces with 0.55 on the next read — success
    reported, nothing changed, and no way for the owner to tell.
    """
    async with TestClient(TestServer(_patch_app())) as c:
        resp = await _send(c, "knowledge.similarity_min_score", 0.0)
        assert resp.status == 400
        assert "between" in (await resp.text())


@pytest.mark.asyncio
async def test_patch_refuses_out_of_range_counts(cfg_file):
    """Zero/negative counts and an absurd fan-out are refused at the boundary."""
    async with TestClient(TestServer(_patch_app())) as c:
        for path, value in (
            ("knowledge.similarity_top_k", 0),
            ("knowledge.similarity_top_k", -1),
            ("knowledge.similarity_top_k", 65),
            ("knowledge.similarity_degree_cap", 0),
            ("knowledge.similarity_degree_cap", 513),
            ("knowledge.similarity_min_score", 1.5),
        ):
            resp = await _send(c, path, value)
            assert resp.status == 400, f"{path}={value} was accepted"

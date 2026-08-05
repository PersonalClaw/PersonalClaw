"""Discover — the curated tour of the system (Platform-Legibility §6).

Covers the hand-authored catalog's integrity, the pure visible-selection logic
(propose-don't-write: dismiss + auto-hide-when-used), dismissal persistence to
``entity_settings/legibility.json``, the isolated engagement checks, and the
``compute_discover`` payload incl. the ``legibility.discover_tips`` kill switch.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.legibility import discover as dc

_APP_TSX = Path("web/src/app/App.tsx")

# Top-level routes the SPA renders but immediately navigates away from, so a tip
# pointing at one never lands where its label promised:
#   loops — LoopsSection redirects the bare route to the `loop` composer; it survives
#           only as the transient plan-review address. The loop LIST is `loops/history`.
_REDIRECTING_ROUTES = {"loops"}

# ── catalog integrity ────────────────────────────────────────────────────────


def test_catalog_ids_are_unique():
    ids = [tip.id for tip in dc.CATALOG]
    assert len(ids) == len(set(ids)), "duplicate tip id in CATALOG"


def test_every_engaged_key_is_registered():
    # A tip that names an engaged_key must have a matching check, or auto-hide is
    # silently dead for it.
    for tip in dc.CATALOG:
        if tip.engaged_key:
            assert tip.engaged_key in dc._ENGAGEMENT_CHECKS, f"{tip.id} → {tip.engaged_key}"


def test_every_tip_has_a_deep_link():
    for tip in dc.CATALOG:
        assert tip.try_it.get("route"), f"{tip.id} has no route"
        assert tip.try_it.get("label"), f"{tip.id} has no try-it label"
        assert isinstance(tip.try_it.get("query"), dict)


def test_every_deep_link_is_a_real_non_redirecting_address():
    """Every tip lands where its label promised.

    Two ways a deep link lies, both invisible from Python alone: a route the SPA
    doesn't render (falls back to the dashboard), and a route it renders but
    immediately redirects away from (the user arrives somewhere else — this shipped
    as the `loops` tip landing on the loop composer instead of a list of loops).
    Pin both against App.tsx's own ROUTABLE set so a future rename breaks here
    rather than in the user's hands.
    """
    routable = _routable_routes()
    for tip in dc.CATALOG:
        top = tip.try_it["route"].split("/")[0]
        assert top in routable, f"{tip.id} → {top!r} is not an App.tsx ROUTABLE route"
        assert top not in _REDIRECTING_ROUTES or tip.try_it["route"] != top, (
            f"{tip.id} targets the bare {top!r} route, which the SPA redirects away "
            f"from — point at the concrete sub-route that owns the surface"
        )


def _routable_routes() -> set[str]:
    """App.tsx's ROUTABLE set: every `NAV` item id plus the extras spread beside it.

    Parsed from the source (the FE-source-guard idiom — see
    test_url_navigation_doctrine.py) because the route table lives in TypeScript and
    the catalog lives here; nothing else keeps the two honest.
    """
    src = _APP_TSX.read_text(encoding="utf-8")
    nav = re.search(r"const NAV: NavItem\[\] = \[(.*?)\n\]", src, re.S)
    extras = re.search(r"const ROUTABLE = new Set\(\[\.\.\.NAV\.map\(.*?\)(.*?)\]\)", src, re.S)
    assert nav and extras, "App.tsx NAV / ROUTABLE shape changed — update this parser"
    ids = set(re.findall(r"\bid: '([^']+)'", nav.group(1)))
    ids |= set(re.findall(r"'([^']+)'", extras.group(1)))
    assert "dashboard" in ids and "loops" in ids, f"parsed ROUTABLE looks wrong: {ids}"
    return ids


def test_to_dict_shape_is_frontend_contract():
    d = dc.CATALOG[0].to_dict()
    assert set(d) == {"id", "area", "title", "lesson", "try_it"}
    assert set(d["try_it"]) == {"route", "query", "label"}


def test_try_helper_copies_query():
    q = {"open": "x"}
    built = dc._try("tools", "Open", q)
    q["open"] = "mutated"
    assert built["query"] == {"open": "x"}, "try_it must not alias the caller's dict"


# ── visible selection (pure) ─────────────────────────────────────────────────


def test_select_visible_drops_dismissed():
    dismissed = {"chat"}
    visible = dc.select_visible(dismissed=dismissed, engaged={})
    ids = [t.id for t in visible]
    assert "chat" not in ids
    assert len(ids) == len(dc.CATALOG) - 1


def test_select_visible_auto_hides_engaged_areas():
    visible = dc.select_visible(dismissed=set(), engaged={"chat": True, "tasks": True})
    ids = [t.id for t in visible]
    assert "chat" not in ids and "tasks" not in ids
    assert len(ids) == len(dc.CATALOG) - 2


def test_select_visible_preserves_catalog_order():
    visible = dc.select_visible(dismissed=set(), engaged={})
    assert [t.id for t in visible] == [t.id for t in dc.CATALOG]


def test_select_visible_all_gone_is_empty():
    all_ids = {t.id for t in dc.CATALOG}
    assert dc.select_visible(dismissed=all_ids, engaged={}) == []


def test_group_by_area_collapses_consecutive_and_keeps_order():
    groups = dc._group_by_area(list(dc.CATALOG))
    # Areas appear in first-seen order, each with the tips that belong to it.
    assert [g["area"] for g in groups] == list(dict.fromkeys(t.area for t in dc.CATALOG))
    flat = [tip["id"] for g in groups for tip in g["tips"]]
    assert flat == [t.id for t in dc.CATALOG]


# ── engagement checks (isolation) ────────────────────────────────────────────


def test_compute_engaged_isolates_failures(monkeypatch: pytest.MonkeyPatch):
    # A check that raises must read False, never propagate.
    def _boom(_state):
        raise RuntimeError("boom")

    monkeypatch.setitem(dc._ENGAGEMENT_CHECKS, "chat", _boom)
    engaged = dc.compute_engaged(None)
    assert engaged["chat"] is False
    # Every registered key is present in the result.
    assert set(engaged) == set(dc._ENGAGEMENT_CHECKS)


def test_engaged_chat_reads_conversation_log():
    state = SimpleNamespace(conversation_log=SimpleNamespace(list_sessions=lambda: ["s1"]))
    assert dc._engaged_chat(state) is True
    empty = SimpleNamespace(conversation_log=SimpleNamespace(list_sessions=lambda: []))
    assert dc._engaged_chat(empty) is False
    assert dc._engaged_chat(SimpleNamespace()) is False  # no log attr


def test_engaged_knowledge_reads_stats():
    state = SimpleNamespace(knowledge_store=SimpleNamespace(get_stats=lambda: {"items": 3}))
    assert dc._engaged_knowledge(state) is True
    zero = SimpleNamespace(knowledge_store=SimpleNamespace(get_stats=lambda: {"items": 0}))
    assert dc._engaged_knowledge(zero) is False


def test_engaged_memory_uses_initialized_provider_only():
    # Reads the already-initialized vector store off the context builder; must not
    # touch any standalone-store creation path.
    vs = SimpleNamespace(memory_stats=lambda: {"semantic_active": 2, "episodic_active": 0})
    state = SimpleNamespace(
        context_builder=SimpleNamespace(memory=SimpleNamespace(vector_store=vs))
    )
    assert dc._engaged_memory(state) is True
    # No context builder → not engaged, no crash.
    assert dc._engaged_memory(SimpleNamespace()) is False


# ── dismissal persistence (entity_settings/legibility.json) ──────────────────


@pytest.fixture
def _entity_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("personalclaw.providers.entity_routes.config_dir", lambda: tmp_path)
    return tmp_path


def test_dismiss_persists_and_loads(_entity_home: Path):
    assert dc.load_dismissed() == set()
    dc.dismiss("chat")
    assert dc.load_dismissed() == {"chat"}
    # accumulates without dupes
    dc.dismiss("tasks")
    dc.dismiss("chat")
    assert dc.load_dismissed() == {"chat", "tasks"}
    assert (_entity_home / "entity_settings" / "legibility.json").exists()


# ── compute_discover wiring ──────────────────────────────────────────────────


def _stub_config(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> None:
    cfg = SimpleNamespace(legibility=SimpleNamespace(discover_tips=enabled))
    monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", classmethod(lambda cls: cfg))


def test_compute_respects_kill_switch(monkeypatch: pytest.MonkeyPatch):
    _stub_config(monkeypatch, enabled=False)
    out = dc.compute_discover()
    assert out == {"enabled": False, "areas": [], "visible_count": 0, "total": len(dc.CATALOG)}


def test_compute_returns_grouped_visible_tips(_entity_home: Path, monkeypatch: pytest.MonkeyPatch):
    _stub_config(monkeypatch, enabled=True)
    # Nothing engaged, one dismissed → catalog minus one, grouped by area.
    monkeypatch.setattr(dc, "compute_engaged", lambda state=None: {})
    dc.dismiss("chat")

    out = dc.compute_discover()
    assert out["enabled"] is True
    assert out["total"] == len(dc.CATALOG)
    assert out["visible_count"] == len(dc.CATALOG) - 1
    flat_ids = [tip["id"] for g in out["areas"] for tip in g["tips"]]
    assert "chat" not in flat_ids
    assert len(flat_ids) == out["visible_count"]


def test_compute_auto_hides_engaged(monkeypatch: pytest.MonkeyPatch, _entity_home: Path):
    _stub_config(monkeypatch, enabled=True)
    monkeypatch.setattr(dc, "compute_engaged", lambda state=None: {"chat": True, "loops": True})
    out = dc.compute_discover()
    flat_ids = [tip["id"] for g in out["areas"] for tip in g["tips"]]
    assert "chat" not in flat_ids and "loops" not in flat_ids
    assert out["visible_count"] == len(dc.CATALOG) - 2

"""Discover — the curated tour of the system (Platform-Legibility §6).

Covers the hand-authored catalog's integrity, the pure visible-selection logic
(propose-don't-write: dismiss + auto-hide-when-used), dismissal persistence to
``entity_settings/legibility.json``, the isolated engagement checks, and the
``compute_discover`` payload incl. the ``legibility.discover_tips`` kill switch.
"""

from __future__ import annotations

import json
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
    #
    # Machine-written rows (auto-consolidation) do NOT count — only the memory
    # editor's user_explicit rows evidence "review and curate" (issue 458 defect 4:
    # every row on the measured instance was machine-written, yet the tip hid).
    vs = SimpleNamespace(
        memory_stats=lambda: {"semantic_active": 2, "episodic_active": 3, "user_curated": 0}
    )
    state = SimpleNamespace(
        context_builder=SimpleNamespace(memory=SimpleNamespace(vector_store=vs))
    )
    assert dc._engaged_memory(state) is False
    curated = SimpleNamespace(
        memory_stats=lambda: {"semantic_active": 2, "episodic_active": 3, "user_curated": 1}
    )
    state_curated = SimpleNamespace(
        context_builder=SimpleNamespace(memory=SimpleNamespace(vector_store=curated))
    )
    assert dc._engaged_memory(state_curated) is True
    # No context builder → not engaged, no crash.
    assert dc._engaged_memory(SimpleNamespace()) is False


def test_engaged_automation_ignores_system_triggers(monkeypatch: pytest.MonkeyPatch):
    """The boot-registered digest trigger (created_by='system') must not read as the
    user having automated anything — issue 458 defect 1: the tip was unreachable on
    every install because boot filled the store before the first interaction."""

    def _store_with(rows):
        return lambda base_dir: SimpleNamespace(load=lambda: rows)

    system_row = SimpleNamespace(trigger=SimpleNamespace(created_by="system"))
    monkeypatch.setattr("personalclaw.triggers.store.TriggerStore", _store_with([system_row]))
    assert dc._engaged_automation(None) is False

    user_row = SimpleNamespace(trigger=SimpleNamespace(created_by="user"))
    monkeypatch.setattr(
        "personalclaw.triggers.store.TriggerStore", _store_with([system_row, user_row])
    )
    assert dc._engaged_automation(None) is True

    # Agent-created counts: the agent wrote it inside a user conversation.
    agent_row = SimpleNamespace(trigger=SimpleNamespace(created_by="agent"))
    monkeypatch.setattr(
        "personalclaw.triggers.store.TriggerStore", _store_with([system_row, agent_row])
    )
    assert dc._engaged_automation(None) is True


def test_engaged_inbox_requires_a_user_gesture(monkeypatch: pytest.MonkeyPatch):
    """A store full of untouched system items is NOT engagement (issue 670: one
    system-generated proposal hid the tip). A user gesture — any status off
    pending, or a favorite — is. FILTERED is system-written and never counts."""

    def _inbox_with(items):
        store = SimpleNamespace(items={str(i): it for i, it in enumerate(items)})
        store.load = lambda: None
        return lambda: store

    pending = SimpleNamespace(status="pending", favorited=False)
    filtered = SimpleNamespace(status="filtered", favorited=False)
    monkeypatch.setattr("personalclaw.inbox.InboxStore", _inbox_with([pending, pending, filtered]))
    assert dc._engaged_inbox(None) is False

    seen = SimpleNamespace(status="seen", favorited=False)
    monkeypatch.setattr("personalclaw.inbox.InboxStore", _inbox_with([pending, seen]))
    assert dc._engaged_inbox(None) is True

    favorited = SimpleNamespace(status="pending", favorited=True)
    monkeypatch.setattr("personalclaw.inbox.InboxStore", _inbox_with([favorited]))
    assert dc._engaged_inbox(None) is True

    monkeypatch.setattr("personalclaw.inbox.InboxStore", _inbox_with([]))
    assert dc._engaged_inbox(None) is False


def test_engaged_skills_ignores_bundled_baseline(monkeypatch: pytest.MonkeyPatch):
    """Passive turn-time injection of bundled skills is not the user teaching one
    (issue 458 defect 2: a single plain chat message hid 'Teach it a reusable
    skill'). Engaged = a skill exists beyond what the install ships."""
    from personalclaw.skills.loader import _BUILTIN_SKILLS_DIR

    bundled_names = [p.name for p in _BUILTIN_SKILLS_DIR.iterdir() if p.is_dir()]
    assert bundled_names, "baseline sanity: bundled skills ship with the code"

    def _loader_with(keys):
        return lambda: SimpleNamespace(list_skills=lambda: [{"key": k} for k in keys])

    monkeypatch.setattr("personalclaw.skills.loader.SkillsLoader", _loader_with(bundled_names))
    assert dc._engaged_skills(None) is False

    monkeypatch.setattr(
        "personalclaw.skills.loader.SkillsLoader",
        _loader_with([*bundled_names, "my-own-skill"]),
    )
    assert dc._engaged_skills(None) is True


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


def test_compute_respects_kill_switch(_entity_home: Path, monkeypatch: pytest.MonkeyPatch):
    # `_entity_home` is required, not decorative: the payload now carries
    # `dismissed_count` on BOTH branches, so this path reads the dismissal store and
    # without the fixture it would read the developer's real home.
    _stub_config(monkeypatch, enabled=False)
    out = dc.compute_discover()
    assert out == {
        "enabled": False,
        "areas": [],
        "visible_count": 0,
        "total": len(dc.CATALOG),
        "dismissed_count": 0,
        # 0 on this branch by construction, not by measurement: a disabled surface shows no
        # tips, so clearing dismissals reveals nothing here either. Pinned in the exact-dict
        # assertion so a later change that computes engagement on the disabled path — the
        # filesystem reads this branch exists to skip — has to come through this test.
        "restorable_count": 0,
    }


def test_compute_reports_how_many_tips_the_user_HID(
    _entity_home: Path, monkeypatch: pytest.MonkeyPatch
):
    """#452 — `visible_count: 0` has two causes and the payload must say which.

    A hub that only receives `visible_count`/`total` cannot tell "you used every area"
    from "you hid the tips", so its empty state congratulated the user either way. These
    two cases are the ones that used to be indistinguishable.
    """
    _stub_config(monkeypatch, enabled=True)

    # (a) every area engaged, nothing dismissed — the congratulation is EARNED.
    every_key = {tip.engaged_key: True for tip in dc.CATALOG if tip.engaged_key}
    monkeypatch.setattr(dc, "compute_engaged", lambda state=None: every_key)
    earned = dc.compute_discover()
    assert earned["visible_count"] == 0, "every tip carries an engaged_key, so all ten auto-hide"
    assert earned["dismissed_count"] == 0

    # (b) nothing engaged, every tip dismissed — same `visible_count`, different cause.
    monkeypatch.setattr(dc, "compute_engaged", lambda state=None: {})
    for tip_id in dc.TIP_IDS:
        dc.dismiss(tip_id)
    hidden = dc.compute_discover()
    assert hidden["visible_count"] == 0
    assert hidden["dismissed_count"] == len(dc.TIP_IDS)
    # The distinguishing fact: identical visible_count, different dismissed_count.
    assert earned["visible_count"] == hidden["visible_count"]
    assert earned["dismissed_count"] != hidden["dismissed_count"]


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


# ── restore: the way back from a dismissal (#452) ─────────────────────────────


def test_restorable_count_is_not_the_dismissed_count(
    _entity_home: Path, monkeypatch: pytest.MonkeyPatch
):
    """The discriminator the restore control gates on (#452).

    ``dismissed_count`` counts what the user hid; ``restorable_count`` counts what
    UN-hiding would actually show them. The two filters in ``select_visible`` are
    independent, so a tip that was dismissed AND whose area has since been engaged stays
    hidden either way — gating the button on ``dismissed_count`` ships a control that
    rewrites the settings file and changes nothing on screen. This test is what makes the
    two numbers observably different; a fix that returned ``dismissed_count`` under the new
    field's name would pass every other assertion in this file.
    """
    _stub_config(monkeypatch, enabled=True)
    dc.dismiss("chat")
    dc.dismiss("tasks")
    # "chat" is now ALSO auto-hidden. Restoring it would reveal nothing; "tasks" would.
    monkeypatch.setattr(dc, "compute_engaged", lambda state=None: {"chat": True})

    out = dc.compute_discover()
    assert out["dismissed_count"] == 2, "the user hid two tips"
    assert out["restorable_count"] == 1, "only 'tasks' would come back — 'chat' is engaged too"
    assert out["restorable_count"] != out["dismissed_count"]


def test_restorable_count_agrees_with_what_the_feed_then_admits(
    _entity_home: Path, monkeypatch: pytest.MonkeyPatch
):
    """The count must be exactly the number of tips clearing the dismissals adds.

    Stated as a before/after over the real writer rather than as a second derivation of the
    rule, so the count cannot drift from ``select_visible``'s own behaviour.
    """
    _stub_config(monkeypatch, enabled=True)
    monkeypatch.setattr(dc, "compute_engaged", lambda state=None: {"chat": True, "loops": True})
    for tip_id in ("chat", "loops", "tasks", "memory"):
        dc.dismiss(tip_id)

    before = dc.compute_discover()
    promised = before["restorable_count"]
    assert promised == 2, "'tasks' and 'memory'; 'chat'/'loops' are engaged as well"

    dc.clear_dismissed()
    after = dc.compute_discover()
    assert after["visible_count"] - before["visible_count"] == promised
    assert after["restorable_count"] == 0, "nothing is dismissed any more"


def test_clear_dismissed_removes_every_dismissal(_entity_home: Path):
    dc.dismiss("chat")
    dc.dismiss("tasks")
    assert dc.load_dismissed() == {"chat", "tasks"}

    assert dc.clear_dismissed() == 2, "returns how many stored ids it removed"
    assert dc.load_dismissed() == set()
    # Persisted, not just in-memory: a reload must not bring the dismissals back.
    settings = json.loads((_entity_home / "entity_settings" / "legibility.json").read_text())
    assert settings[dc._DISMISSED_FIELD] == []


def test_clear_dismissed_counts_junk_an_older_build_stored(_entity_home: Path):
    """Counts what was STORED, not what the catalog defines.

    ``load_dismissed`` narrows to :data:`TIP_IDS`, so junk an older permissive build wrote is
    invisible to every reader — but it IS in the file and clearing really does remove it.
    Reporting it keeps ``restored`` a true statement about the write.
    """
    from personalclaw.providers.entity_routes import _save_entity_settings

    _save_entity_settings(dc._ENTITY, {dc._DISMISSED_FIELD: ["chat", "not-a-tip", "also-junk"]})
    assert dc.load_dismissed() == {"chat"}, "readers already ignore the junk"
    assert dc.clear_dismissed() == 3, "but the write removed all three"
    assert dc.load_dismissed() == set()


def test_clear_dismissed_with_nothing_stored_does_not_write(
    _entity_home: Path, monkeypatch: pytest.MonkeyPatch
):
    """A no-op must stay a no-op — the button's gate can be one click stale."""
    calls: list[object] = []
    # Patched at its definition, not on `dc`: clear_dismissed imports the writer inside the
    # function body, so a module attribute on `dc` would never be consulted.
    monkeypatch.setattr(
        "personalclaw.providers.entity_routes._save_entity_settings",
        lambda *a, **k: calls.append(a),
    )
    assert dc.clear_dismissed() == 0
    assert calls == [], "nothing stored, so nothing was written"

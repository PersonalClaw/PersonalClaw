"""The `view` kind — pull-on-view refresh (R10 / §7 item 8 — S123).

🔴 THE DEFECT. `view` is the FOURTH declared kind found with no runtime, after `file` (S93),
`web_watch` (S121) and `run_completed` (S122). It is in `KINDS`, `SPEC_KEYS` accepts
`{surface_binding, ttl_secs}`, the store persists it, `/api/triggers` lists it and the Automations
page renders it. Measured: `surface_binding` was referenced by **exactly one** line in the whole
tree — its own declaration in `SPEC_KEYS`. Nothing read it, so nothing could fire a `view` trigger.

Deliberately NOT a poll. §3: "Pull-on-view (R10): fires when a bound surface renders past TTL;
within TTL serve cache … Sidesteps the 1440-run-dirs critique by never firing unviewed." A poll here
would reintroduce the exact cost this kind exists to avoid, so the runtime is a function a RENDER
calls and these tests drive it that way.
"""

from __future__ import annotations

import pytest

from personalclaw.triggers import pull_on_view as V
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


def _view(store, *, tid="view:tile", surface="dashboard.inbox", **spec):
    store.upsert(
        Trigger(
            id=tid,
            name=tid,
            kind="view",
            enabled=True,
            spec={"surface_binding": surface, **spec},
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    return store.get(tid).trigger


# ── binding ──


def test_a_view_trigger_is_FOUND_for_its_surface(store):
    """🔴 The defect at its root: `surface_binding` had no reader, so nothing could match."""
    _view(store)
    assert [t.id for t in V.bound_triggers(store, surface="dashboard.inbox")] == ["view:tile"]


def test_a_DIFFERENT_surface_does_not_match(store):
    _view(store)
    assert V.bound_triggers(store, surface="dashboard.other") == []


def test_an_UNBOUND_trigger_matches_NOTHING(store):
    """The important direction: a blank binding must not refresh on every render in the product,
    which is the opposite of what pull-on-view is for."""
    _view(store, tid="view:blank", surface="")
    assert V.bound_triggers(store, surface="dashboard.inbox") == []


def test_an_empty_surface_query_matches_nothing(store):
    _view(store)
    assert V.bound_triggers(store, surface="") == []


def test_a_DISABLED_binding_does_not_refresh(store):
    _view(store)
    row = store.get("view:tile").trigger
    row.enabled = False
    store.upsert(row)
    assert V.bound_triggers(store, surface="dashboard.inbox") == []


# ── the TTL, which is the whole control ──


def test_the_FIRST_render_refreshes(store, tmp_path):
    decision = V.on_render(_view(store, ttl_secs=300), now=NOW, base_dir=tmp_path)
    assert decision.refresh is True
    assert decision.reason == "first render"


def test_a_render_INSIDE_the_TTL_serves_CACHE(store, tmp_path):
    """🔴 The point of the kind. Two renders inside the window must cost nothing."""
    trigger = _view(store, ttl_secs=300)
    V.on_render(trigger, now=NOW, base_dir=tmp_path)
    decision = V.on_render(trigger, now=NOW + 10, base_dir=tmp_path)
    assert decision.refresh is False
    assert "served cache" in decision.reason


def test_a_render_at_the_TTL_BOUNDARY_still_serves_cache(store, tmp_path):
    trigger = _view(store, ttl_secs=300)
    V.on_render(trigger, now=NOW, base_dir=tmp_path)
    assert V.on_render(trigger, now=NOW + 299, base_dir=tmp_path).refresh is False


def test_a_render_PAST_the_TTL_refreshes(store, tmp_path):
    trigger = _view(store, ttl_secs=300)
    V.on_render(trigger, now=NOW, base_dir=tmp_path)
    decision = V.on_render(trigger, now=NOW + 400, base_dir=tmp_path)
    assert decision.refresh is True
    assert "stale" in decision.reason


def test_the_cache_reason_reports_the_AGE(store, tmp_path):
    """A surface that could not explain why it did not refresh is indistinguishable from a broken
    binding."""
    trigger = _view(store, ttl_secs=300)
    V.on_render(trigger, now=NOW, base_dir=tmp_path)
    assert "10s old" in V.on_render(trigger, now=NOW + 10, base_dir=tmp_path).reason


# ── the rate floor ──


def test_the_TTL_is_FLOORED(store):
    """🔴 A dashboard re-renders on every websocket nudge, so a TTL of 1 would mean an LLM turn per
    keystroke elsewhere in the UI. S109 recorded the R1 floor being declared but read by no code."""
    assert V.ttl_for(_view(store, ttl_secs=1)) == V.MIN_REFRESH_INTERVAL_SECS


def test_a_LONGER_ttl_is_honoured(store):
    assert V.ttl_for(_view(store, ttl_secs=3600)) == 3600


def test_NO_ttl_uses_the_default(store):
    assert V.ttl_for(_view(store)) == V.DEFAULT_TTL_SECS


def test_a_MALFORMED_ttl_falls_back_rather_than_crashing(store):
    assert V.ttl_for(_view(store, ttl_secs="soon")) == V.DEFAULT_TTL_SECS


# ── persist=False: asking must not change the answer ──


def test_a_DRY_read_does_not_consume_the_window(store, tmp_path):
    """🔴 Without this, a freshness column that merely REPORTED staleness would refresh the tile by
    asking — the observer changing what it observes."""
    trigger = _view(store, ttl_secs=300)
    first = V.on_render(trigger, now=NOW, base_dir=tmp_path, persist=False)
    second = V.on_render(trigger, now=NOW, base_dir=tmp_path, persist=False)
    assert first.refresh is True and second.refresh is True


# ── the payload + freshness bookkeeping ──


def test_the_payload_names_the_SURFACE(store, tmp_path):
    decision = V.on_render(_view(store), now=NOW, base_dir=tmp_path)
    assert decision.payload is not None
    assert decision.payload["surface_binding"] == "dashboard.inbox"
    assert decision.payload["kind"] == "view"


def test_the_refresh_COUNT_increments(store, tmp_path):
    """§3's freshness column: "refreshed 12 times" is what tells a user whether a binding earns its
    cost."""
    trigger = _view(store, ttl_secs=60)
    assert V.on_render(trigger, now=NOW, base_dir=tmp_path).payload["refresh_number"] == 1
    assert V.on_render(trigger, now=NOW + 100, base_dir=tmp_path).payload["refresh_number"] == 2


def test_freshness_SURVIVES_a_restart(store, tmp_path):
    """Persisted, so a gateway restart does not hand every open tile a free refresh."""
    trigger = _view(store, ttl_secs=300)
    V.on_render(trigger, now=NOW, base_dir=tmp_path)
    assert V.load_freshness(trigger.id, base_dir=tmp_path).refreshes == 1


def test_a_CORRUPT_sidecar_reads_as_never_refreshed(tmp_path):
    """Costs one extra refresh — strictly better than a render that raises on a truncated file."""
    path = V._state_path("view:tile", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert V.load_freshness("view:tile", base_dir=tmp_path).last_refresh_at == 0.0


# ── the render fan-out ──


def test_renders_returns_BOTH_refreshes_and_cache_hits(store, tmp_path):
    """§7 criterion 8's zero-silent-drops rule applies to a skipped refresh exactly as to a skipped
    fire, so the caller gets both lists."""
    _view(store, tid="view:a", ttl_secs=300)
    _view(store, tid="view:b", ttl_secs=300)
    payloads, cached = V.renders(store, surface="dashboard.inbox", now=NOW, base_dir=tmp_path)
    assert len(payloads) == 2 and cached == []

    payloads2, cached2 = V.renders(
        store, surface="dashboard.inbox", now=NOW + 10, base_dir=tmp_path
    )
    assert payloads2 == [] and len(cached2) == 2


def test_ONE_bad_binding_does_not_break_the_RENDER(store, tmp_path, monkeypatch):
    """A render is a user looking at a page. One broken binding must not blank it."""
    _view(store, tid="view:a")
    calls = {"n": 0}
    real = V.on_render

    def flaky(trigger, **kw):
        calls["n"] += 1
        if trigger.id == "view:a":
            raise RuntimeError("boom")
        return real(trigger, **kw)

    monkeypatch.setattr(V, "on_render", flaky)
    payloads, cached = V.renders(store, surface="dashboard.inbox", now=NOW, base_dir=tmp_path)
    assert payloads == []
    assert cached and "raised" in cached[0]["reason"]


# ── it is NOT a poll ──


def test_NO_background_loop_polls_this_kind():
    """🔴 R10's whole point, asserted. A `view` trigger must cost nothing when nobody is looking; a
    poll loop would reintroduce the 1440-run-dirs-a-day cost the kind exists to avoid."""
    import inspect

    from personalclaw import gateway

    src = inspect.getsource(gateway)
    assert "pull_on_view" not in src, "the view kind must be render-driven, never polled"

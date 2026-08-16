"""WATCHED-SOURCES WS-9 — the HTTP surface behind the Sources UI.

WS-2..WS-5 shipped the store, the poll engine and three providers, and
``store.create_source`` had **zero non-test callers**: no route, no CLI, no UI. These tests
cover the endpoints that end that, and each one is written against the clause of the atom's
``done_when`` it belongs to rather than against "the request returned 200":

* **lists all source kinds with health status** — the catalog is asserted to report
  ``previewable`` HONESTLY (true for the web kind, false for feed and dir), because WS-3
  deliberately kept ``preview`` off the ABC and a uniform-looking preview would be a lie
  about two of the three providers;
* **drives the paste-URL preview/tune/save create flow** — a preview returns items AND the
  detector that won, save is refused by the provider's own ``validate_spec`` (not by a
  second copy of its rules living in the handler), and a provider with no preview is refused
  with the reason instead of answered with an empty list that reads like a failure;
* **shows the 'no AI' chip on raw sources** — asserted as a READOUT of ``sources.enrichment``
  round-tripping through create → list, since §6.3's guarantee is structural and a chip
  rendered from anything else would be decoration;
* **offers listing-page/render-tier remediation affordances** — the two are asserted to stay
  DISTINCT in both kind and guidance text, and to carry OPPOSITE actions. Collapsing them is
  the mutation this file exists to red: WS-3 measured the discrimination precisely because
  the fixes are opposite.

Plus the FE/BE parity rail: the TypeScript status map and kind-form switch are asserted
against the Python vocabularies they render, so a status added in ``base.py`` cannot fall
through a hardcoded default branch in the UI.

No test opens a socket: the web provider is registered with a scripted ``fetch_fn``, which is
also how the preview path is driven. The registry is process-global, so every test registers
into it and tears down — a leaked fixture provider would silently change what a sibling test
believes is enrolled.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import knowledge as H
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.knowledge_providers import registry as prov_registry
from personalclaw.knowledge_providers.base import (
    ENRICHMENT_RAW,
    HEALTH_DEGRADED,
    HEALTH_NEEDS_RENDER,
    SOURCE_HEALTH,
)
from personalclaw.knowledge_providers.dir_source import DirSourceProvider
from personalclaw.knowledge_providers.feed_source import FeedSourceProvider
from personalclaw.knowledge_providers.web_source import (
    DETECTOR_ORDER,
    DETECTOR_SEMANTIC_HTML,
    LISTING_PAGE_GUIDANCE,
    RENDER_TIER_GUIDANCE,
    WebSourceProvider,
)

PAGE_URL = "https://app.example.com/changelog"

LISTING_HTML = """
<html><body>
  <article><h2><a href="/r/2-1">Release 2.1 ships dark mode</a></h2>
    <p>Dark mode arrives across every surface of the product.</p></article>
  <article><h2><a href="/r/2-0">Release 2.0 rewrites the editor</a></h2>
    <p>The editor is now a real document model with undo.</p></article>
</body></html>
"""


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


class _Resp:
    def __init__(self, body="", *, status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self.text = body
        self.url = PAGE_URL


class _Fetcher:
    """A scripted fetch seam that records every request, so a preview's budget spend is
    asserted rather than assumed. Routes by URL substring so the WordPress detector's REST
    sub-request is answered separately from the page — the only detector whose items carry
    real MARKUP, and therefore the only one that exercises the snippet conversion."""

    def __init__(self, resp=None, routes=None):
        self.resp = resp if resp is not None else _Resp(LISTING_HTML)
        self.routes = dict(routes or {})
        self.requests: list[str] = []

    async def __call__(self, url, *, policy=None, headers=None):
        self.requests.append(url)
        for needle, resp in self.routes.items():
            if needle in url:
                return resp
        return self.resp


@pytest.fixture()
def registered(store):
    """The three core providers registered exactly as ``server.py`` registers them, with the
    web kind's byte seam scripted. Torn down because the registry is process-global."""
    fetcher = _Fetcher()
    web_prov = WebSourceProvider(store, fetch_fn=fetcher)
    feed_prov = FeedSourceProvider(store)
    dir_prov = DirSourceProvider(store)
    for prov in (web_prov, feed_prov, dir_prov):
        prov_registry.register_provider(prov)
    try:
        yield SimpleNamespace(web=web_prov, feed=feed_prov, dir=dir_prov, fetcher=fetcher)
    finally:
        for prov in (web_prov, feed_prov, dir_prov):
            prov_registry.unregister_provider(prov.name)


class _NullQueue:
    def enqueue(self, item_id: str) -> None:
        pass

    def recover_pending(self) -> int:
        return 0


def _cfg():
    from personalclaw.config.loader import SourcesConfig

    return SourcesConfig(
        enabled=True,
        poll_interval_default_secs=900,
        network_floor_secs=0,
        max_sources=100,
        max_items_per_poll=50,
        daily_request_budget=288,
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _call(handler, store, method, path, *, body=None, match_info=None):
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    req = make_mocked_request(method, path, app=app, match_info=match_info or {})

    async def _json():
        return body or {}

    req.json = _json  # type: ignore[method-assign]
    resp = _run(handler(req))
    return resp, json.loads(resp.body)


def _get_sources(store):
    return _call(H.list_watched_sources, store, "GET", "/api/knowledge/sources")


def _create(store, **body):
    return _call(H.create_watched_source, store, "POST", "/api/knowledge/sources", body=body)


def _patch(store, sid, **body):
    return _call(
        H.update_watched_source,
        store,
        "PATCH",
        f"/api/knowledge/sources/{sid}",
        body=body,
        match_info={"id": sid},
    )


def _preview(store, **body):
    return _call(
        H.preview_watched_source, store, "POST", "/api/knowledge/sources/preview", body=body
    )


# ── the catalog: all kinds, and the preview asymmetry told honestly ─────────────────


def test_the_catalog_lists_every_registered_kind_with_its_own_form(store, registered):
    resp, body = _get_sources(store)

    assert resp.status == 200
    by_provider = {k["provider"]: k for k in body["kinds"]}
    assert set(by_provider) == {"watched-page", "watched-feed", "watched-dir"}
    assert {k["form"] for k in body["kinds"]} == {"web_page", "feed", "dir"}
    # The detector list the tune step renders comes from the provider's own order.
    assert by_provider["watched-page"]["detectors"] == list(DETECTOR_ORDER)
    assert "hn_algolia" in by_provider["watched-feed"]["presets"]
    assert by_provider["watched-dir"]["default_include"]


def test_previewable_is_measured_per_provider_not_declared_uniformly(store, registered):
    _, body = _get_sources(store)
    previewable = {k["provider"]: k["previewable"] for k in body["kinds"]}

    # WS-3's deliberate asymmetry: only the web kind has a detect-then-tune loop.
    assert previewable == {"watched-page": True, "watched-feed": False, "watched-dir": False}


def test_the_closed_vocabularies_ship_with_the_list_rather_than_being_retyped(store, registered):
    _, body = _get_sources(store)

    assert body["health_statuses"] == sorted(SOURCE_HEALTH)
    assert body["raw_enrichment"] == ENRICHMENT_RAW


def test_a_kind_with_no_enrolled_provider_is_not_offered(store):
    # No `registered` fixture: nothing is enrolled, so nothing may be offered — a create
    # form listing a kind nothing polls is how an inert source gets made.
    _, body = _get_sources(store)

    assert body["kinds"] == []


# ── create: the provider's own validator decides ───────────────────────────────────


def test_creating_a_web_source_persists_it_and_returns_the_row(store, registered):
    resp, body = _create(store, name="Changelog", provider="watched-page", spec={"url": PAGE_URL})

    assert resp.status == 201
    assert body["source"]["provider"] == "watched-page"
    assert body["source"]["spec"] == {"url": PAGE_URL}
    assert body["source"]["kind"] == "web_page"
    assert body["source"]["item_type"] == "bookmark"
    assert body["source"]["enrolled"] is True
    # …and it is genuinely in the store, which is the whole point of the atom.
    assert [s["name"] for s in store.list_sources()] == ["Changelog"]


def test_a_bad_spec_is_refused_by_the_providers_own_validator(store, registered):
    resp, body = _create(store, name="Bad", provider="watched-page", spec={"url": "ftp://x/y"})

    assert resp.status == 400
    # The message is the provider's, not a second copy of its rules living in the handler.
    assert "url" in body["error"]
    assert store.list_sources() == []


def test_a_dir_source_pointing_at_a_sensitive_path_is_refused(store, registered, tmp_path):
    resp, body = _create(
        store, name="Keys", provider="watched-dir", spec={"path": str(Path.home() / ".ssh")}
    )

    assert resp.status == 400
    assert "sensitive" in body["error"] or "does not exist" in body["error"]


def test_a_real_dir_saves_with_the_kinds_own_default_item_type(store, registered, tmp_path):
    watched = tmp_path / "notes"
    watched.mkdir()

    resp, body = _create(store, name="Notes", provider="watched-dir", spec={"path": str(watched)})

    assert resp.status == 201
    assert body["source"]["item_type"] == "note"
    assert body["source"]["kind"] == "dir"


def test_an_unknown_provider_is_refused_and_names_the_known_ones(store, registered):
    resp, body = _create(store, name="X", provider="watched-carrier-pigeon", spec={})

    assert resp.status == 400
    assert "watched-page" in body["error"]


def test_a_nameless_source_is_refused(store, registered):
    resp, body = _create(store, name="  ", provider="watched-page", spec={"url": PAGE_URL})

    assert resp.status == 400
    assert "name" in body["error"]


def test_an_unknown_enrichment_is_refused_rather_than_defaulted_to_full(store, registered):
    # A typo'd enrichment silently becoming `full` would send a no-AI source's content to a
    # model — the exact failure the closed ENRICHMENTS vocabulary exists to prevent.
    resp, body = _create(
        store, name="X", provider="watched-page", spec={"url": PAGE_URL}, enrichment="rawish"
    )

    assert resp.status == 400
    assert "enrichment" in body["error"]


# ── §6.3: the 'no AI' chip is a readout of the persisted field ──────────────────────


def test_a_raw_source_round_trips_its_no_ai_enrichment_through_the_list(store, registered):
    _create(
        store,
        name="Raw feed",
        provider="watched-feed",
        spec={"kind": "rss", "url": "https://f.example.com/rss"},
        enrichment=ENRICHMENT_RAW,
    )

    _, body = _get_sources(store)
    raw = [s for s in body["sources"] if s["enrichment"] == ENRICHMENT_RAW]

    assert len(raw) == 1
    assert raw[0]["name"] == "Raw feed"


def test_a_full_source_is_not_reported_as_raw(store, registered):
    # The vacuity counterpart: without it, a serializer hardcoding "raw" would pass above.
    _create(store, name="Full page", provider="watched-page", spec={"url": PAGE_URL})

    _, body = _get_sources(store)

    assert [s["enrichment"] for s in body["sources"]] == ["full"]


# ── §12/§2.3: the two remediations are OPPOSITE and must stay distinct ──────────────


def _polled(store, sid, *, health, summary, budget=None):
    """Record a poll outcome the way the engine does — `record_poll` clips the summary to
    200 chars, which is exactly why the remediation match is a prefix test."""
    if budget is not None:
        store.update_source(sid, budget=budget)
    store.record_poll(sid, cursor="", new_count=0, health_status=health, error_summary=summary)


def test_a_js_shell_without_the_render_tier_offers_the_render_tier_fix(store, registered):
    _, created = _create(store, name="SPA", provider="watched-page", spec={"url": PAGE_URL})
    sid = created["source"]["id"]
    _polled(store, sid, health=HEALTH_NEEDS_RENDER, summary=RENDER_TIER_GUIDANCE)

    _, body = _get_sources(store)
    rem = body["sources"][0]["remediation"]

    assert rem["kind"] == "render_tier"
    assert rem["guidance"] == RENDER_TIER_GUIDANCE
    assert rem["action"] == "allow_render"


def test_a_wrong_url_offers_the_listing_page_fix_instead(store, registered):
    _, created = _create(store, name="Homepage", provider="watched-page", spec={"url": PAGE_URL})
    sid = created["source"]["id"]
    _polled(store, sid, health=HEALTH_DEGRADED, summary=LISTING_PAGE_GUIDANCE)

    _, body = _get_sources(store)
    rem = body["sources"][0]["remediation"]

    assert rem["kind"] == "listing_page"
    # The FULL guidance, reconstituted: `record_poll` stored only the first 200 chars.
    assert rem["guidance"] == LISTING_PAGE_GUIDANCE
    assert len(rem["guidance"]) > 200
    assert rem["action"] == "edit_url"


def test_the_two_remediations_never_share_a_kind_a_message_or_an_action(store, registered):
    """The anti-collapse assertion. WS-3's whole JS-shell/wrong-URL discrimination exists
    because the fixes are opposite; one shared message would send half the users the wrong
    way, and that is a defect no per-case test above would catch on its own."""
    _, a = _create(store, name="SPA", provider="watched-page", spec={"url": PAGE_URL})
    _, b = _create(store, name="Homepage", provider="watched-page", spec={"url": PAGE_URL})
    _polled(store, a["source"]["id"], health=HEALTH_NEEDS_RENDER, summary=RENDER_TIER_GUIDANCE)
    _polled(store, b["source"]["id"], health=HEALTH_DEGRADED, summary=LISTING_PAGE_GUIDANCE)

    _, body = _get_sources(store)
    rems = {s["name"]: s["remediation"] for s in body["sources"]}

    assert rems["SPA"]["kind"] != rems["Homepage"]["kind"]
    assert rems["SPA"]["guidance"] != rems["Homepage"]["guidance"]
    assert rems["SPA"]["action"] != rems["Homepage"]["action"]
    # And each names its OWN fix: the render one talks about the knob, the URL one about
    # listing pages. A generic "nothing found" for both would satisfy the inequalities.
    assert "allow_render" in rems["SPA"]["guidance"]
    assert "LISTING" in rems["Homepage"]["guidance"]


def test_an_already_allowed_render_tier_is_advice_not_a_button(store, registered):
    _, created = _create(
        store,
        name="SPA",
        provider="watched-page",
        spec={"url": PAGE_URL},
        budget={"allow_render": True},
    )
    sid = created["source"]["id"]
    _polled(
        store,
        sid,
        health=HEALTH_NEEDS_RENDER,
        summary="render tier unavailable; install personalclaw[js-render]",
    )

    _, body = _get_sources(store)
    rem = body["sources"][0]["remediation"]

    assert rem["kind"] == "render_tier"
    # No knob to flip — it is already on. Offering the button would be a lie about what
    # pressing it would do.
    assert rem["action"] == ""
    # …and the REASON survives, because "install the extra" is not what the guidance says.
    assert "install" in rem["detail"]


def test_a_failing_source_still_says_when_it_will_be_retried(store, registered):
    """Found by driving the real thing: `record_poll`'s `next_poll_at` was written on the
    SUCCESS path only, so the two rows carrying a remediation were exactly the two with no
    "next check" to show — the same shape WS-3 fixed for `last_escalations`."""
    from personalclaw.knowledge.source_engine import SourceEngine

    _, created = _create(store, name="SPA", provider="watched-page", spec={"url": PAGE_URL})
    sid = created["source"]["id"]
    registered.fetcher.resp = _Resp("<html><body><script>x</script></body></html>")
    engine = SourceEngine(store, _NullQueue(), providers_lister=lambda: [registered.web])

    _run(engine.poll_source(store.get_source(sid), _cfg()))

    row = store.get_source(sid)
    assert row["health_status"] == HEALTH_NEEDS_RENDER
    assert row["next_poll_at"], "a failing source that will be retried must say so"


def test_a_healthy_source_offers_no_remediation(store, registered):
    _, created = _create(store, name="OK", provider="watched-page", spec={"url": PAGE_URL})
    _polled(store, created["source"]["id"], health="ok", summary="")

    _, body = _get_sources(store)

    assert body["sources"][0]["remediation"]["kind"] == ""


# ── PATCH: what makes the guidance actionable ───────────────────────────────────────


def test_allowing_the_render_tier_flips_only_that_knob(store, registered):
    _, created = _create(
        store,
        name="SPA",
        provider="watched-page",
        spec={"url": PAGE_URL},
        budget={"max_requests": 4},
    )
    sid = created["source"]["id"]

    resp, body = _patch(store, sid, budget={"max_requests": 4, "allow_render": True})

    assert resp.status == 200
    assert body["source"]["budget"] == {"max_requests": 4, "allow_render": True}
    assert body["source"]["name"] == "SPA"
    assert body["source"]["spec"] == {"url": PAGE_URL}


def test_a_url_fix_is_revalidated_by_the_provider(store, registered):
    _, created = _create(store, name="Page", provider="watched-page", spec={"url": PAGE_URL})
    sid = created["source"]["id"]

    bad, bad_body = _patch(store, sid, spec={"url": "not-a-url"})
    good, good_body = _patch(store, sid, spec={"url": "https://app.example.com/blog"})

    assert bad.status == 400 and "url" in bad_body["error"]
    assert good.status == 200
    assert good_body["source"]["spec"] == {"url": "https://app.example.com/blog"}
    # The refused edit really did not land.
    assert store.get_source(sid)["spec"]["url"] == "https://app.example.com/blog"


def test_disabling_a_source_stops_it_being_enrolled_for_polling(store, registered):
    _, created = _create(store, name="Page", provider="watched-page", spec={"url": PAGE_URL})
    sid = created["source"]["id"]

    resp, body = _patch(store, sid, enabled=False)

    assert resp.status == 200 and body["source"]["enabled"] is False
    # `enabled_only` is the filter the engine's tick uses to pick due sources.
    assert store.list_sources(enabled_only=True) == []


def test_patching_nothing_is_refused_rather_than_reported_as_a_save(store, registered):
    _, created = _create(store, name="Page", provider="watched-page", spec={"url": PAGE_URL})

    resp, body = _patch(store, created["source"]["id"])

    assert resp.status == 400
    assert "editable" in body["error"]


def test_patching_a_missing_source_is_a_404(store, registered):
    resp, _ = _patch(store, "src-ghost", enabled=False)

    assert resp.status == 404


# ── §2.4: the paste-URL preview ─────────────────────────────────────────────────────


def test_a_paste_url_preview_returns_items_and_names_the_winning_detector(store, registered):
    resp, body = _preview(store, provider="watched-page", spec={"url": PAGE_URL})

    assert resp.status == 200
    assert body["error"] == ""
    assert [i["title"] for i in body["items"]] == [
        "Release 2.1 ships dark mode",
        "Release 2.0 rewrites the editor",
    ]
    # The user tunes a NAMED detector, not a black box.
    assert body["detector"] == DETECTOR_SEMANTIC_HTML
    assert body["requests_used"] == 1
    assert body["guidance"] == ""
    # A dry run: nothing persisted.
    assert store.list_sources() == []


def test_a_preview_that_finds_nothing_carries_the_listing_page_guidance(store, registered):
    registered.fetcher.resp = _Resp("<html><body><p>" + "prose " * 200 + "</p></body></html>")

    _, body = _preview(store, provider="watched-page", spec={"url": PAGE_URL})

    assert body["items"] == []
    assert body["guidance"] == LISTING_PAGE_GUIDANCE


def test_a_preview_snippet_is_clipped_and_carries_no_markup_field(store, registered):
    registered.fetcher.resp = _Resp(
        "<html><body><article><h2><a href='/x'>A perfectly ordinary headline</a></h2>"
        f"<p>{'body ' * 400}</p></article></body></html>"
    )

    _, body = _preview(store, provider="watched-page", spec={"url": PAGE_URL})

    assert len(body["items"]) == 1
    assert len(body["items"][0]["snippet"]) <= H._PREVIEW_SNIPPET_CHARS
    assert set(body["items"][0]) == {"guid", "title", "url", "published_at", "snippet"}


def test_a_preview_snippet_is_plain_text_not_the_items_markup(store, registered):
    """Found driving the real thing: a WordPress item's ``content`` is its ``excerpt.rendered``
    — real markup — and the client renders the snippet as TEXT, so without a conversion here
    every row on ``github.blog/changelog`` read ``<p>See how four…</p>`` with ``&#8217;`` for
    its apostrophes. All 20 of them did.

    Routed through the WORDPRESS detector deliberately. The first version of this test used a
    ``semantic_html`` page and a mutation that removed the conversion entirely reded NOTHING:
    DOM extraction yields already-decoded plain text, so the assertion held for a reason that
    had nothing to do with the code under test. The five detectors are not interchangeable
    fixtures — only this one produces markup."""
    registered.fetcher.routes["wp-json"] = _Resp(
        json.dumps(
            [
                {
                    "id": 9,
                    "link": "https://app.example.com/2026/07/dark-mode",
                    "title": {"rendered": "Dark mode ships everywhere"},
                    "excerpt": {
                        "rendered": "<p>Dark mode arrives across every surface of "
                        "GitHub&#8217;s product.</p>"
                    },
                    "date_gmt": "2026-07-01T10:00:00",
                }
            ]
        )
    )
    registered.fetcher.resp = _Resp(
        '<html><head><link rel="https://api.w.org/" href="https://app.example.com/wp-json/">'
        "</head><body><p>a changelog</p></body></html>"
    )

    _, body = _preview(store, provider="watched-page", spec={"url": PAGE_URL})

    snippet = body["items"][0]["snippet"]
    assert "<p>" not in snippet, "the client renders this as text, so markup would show up raw"
    assert "&#8217;" not in snippet
    assert "Dark mode arrives" in snippet
    assert "\n" not in snippet


def test_a_provider_without_a_preview_says_so_instead_of_returning_nothing(store, registered):
    resp, body = _preview(
        store, provider="watched-feed", spec={"kind": "rss", "url": "https://f.example.com/f"}
    )

    assert resp.status == 400
    # Honest about the asymmetry rather than faking a uniform dry run.
    assert "no preview" in body["error"]
    assert "first poll" in body["error"]


def test_a_preview_spends_the_specs_request_budget(store, registered):
    resp, body = _preview(
        store, provider="watched-page", spec={"url": PAGE_URL}, budget={"max_requests": 0}
    )

    assert resp.status == 200
    assert body["items"] == []
    assert "budget" in body["error"]
    assert registered.fetcher.requests == []


def test_a_preview_runs_under_the_engines_own_egress_posture(store, registered, monkeypatch):
    """Not a second posture resolved in the handler: the preview fetches the same targets a
    poll does, and two postures for one act is the hole in the SOURCE profile."""
    seen: list[object] = []

    async def _capture(url, *, policy=None, headers=None):
        seen.append(policy)
        return _Resp(LISTING_HTML)

    registered.web._fetch_fn = _capture
    from personalclaw.knowledge.source_engine import SourceEngine

    _preview(store, provider="watched-page", spec={"url": PAGE_URL})

    assert seen and seen[0] is not None
    assert seen[0].name == SourceEngine.egress_policy().name == "source"
    assert seen[0].max_bytes == SourceEngine.egress_policy().max_bytes


# ── store-level: the editable-field allowlist ───────────────────────────────────────


def test_update_source_refuses_a_field_outside_its_allowlist(store):
    sid = store.create_source(name="s", provider="p", kind="k")

    with pytest.raises(KeyError, match="editable"):
        store.update_source(sid, health_status="ok")

    # The engine's rollup is untouched — a client cannot overwrite a poll's verdict.
    assert store.get_source(sid)["health_status"] == "ok"


def test_update_source_is_partial_and_leaves_unsent_fields_alone(store):
    sid = store.create_source(name="s", provider="p", kind="k", spec={"url": "https://x/y"})

    store.update_source(sid, name="renamed")

    row = store.get_source(sid)
    assert row["name"] == "renamed"
    assert row["spec"] == {"url": "https://x/y"}


def test_update_source_on_a_missing_row_is_none_not_a_silent_insert(store):
    assert store.update_source("src-ghost", name="x") is None


# ── FE/BE parity: the UI cannot fall through a default branch ───────────────────────

_WEB = Path(__file__).resolve().parents[1] / "web" / "src" / "pages" / "knowledge"


def test_the_ui_status_map_covers_exactly_the_python_health_vocabulary():
    """A status the UI has no entry for renders through whatever its default branch does —
    silently, and for the one status that most needed a specific message. Read from source
    because the assertion is about two LISTS, not about rendering."""
    src = (_WEB / "sourceMeta.ts").read_text()
    block = re.search(r"HEALTH_META: Record<string, HealthMeta> = \{(.*?)\n\}", src, re.S)
    assert block, "could not locate HEALTH_META in sourceMeta.ts"
    keys = set(re.findall(r"^\s*'([^']+)':", block.group(1), re.M))

    assert keys, "HEALTH_META parsed empty — the assertion below would be vacuous"
    assert keys == set(SOURCE_HEALTH)


def test_the_ui_knows_the_raw_enrichment_literal_the_no_ai_chip_keys_on():
    src = (_WEB / "sourceMeta.ts").read_text()

    assert f"RAW_ENRICHMENT = '{ENRICHMENT_RAW}'" in src


def test_the_ui_branches_on_the_render_tier_status_by_its_real_value():
    """Both the create flow's preview and the list's remediation strip key on this one
    status, so a drift here silently turns the render-tier affordance off everywhere."""
    src = (_WEB / "sourceMeta.ts").read_text()

    assert f"HEALTH_NEEDS_RENDER = '{HEALTH_NEEDS_RENDER}'" in src


def test_the_ui_has_a_form_for_every_kind_the_catalog_can_offer(store, registered):
    """A kind the create page has no form for is a kind the API offers and the UI drops."""
    _, body = _get_sources(store)
    forms = {k["form"] for k in body["kinds"]}
    src = (_WEB / "SourceCreatePage.tsx").read_text()

    assert forms, "the catalog parsed empty — the assertion below would be vacuous"
    for form in forms:
        assert f"'{form}'" in src, f"SourceCreatePage.tsx has no branch for form {form!r}"

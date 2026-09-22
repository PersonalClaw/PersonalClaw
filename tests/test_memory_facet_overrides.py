"""#1783 clause 3 — `pin_facet` / `forget_facet` reachable, asserted at the ROUTE.

Two write paths with a complete read side and no writer. `Facet.pinned` and
`Facet.forgotten` persist, `decayed_stability` and `facet_state` both branch on them, the
identity report renders the resulting state — and nothing outside the test suite could set
either flag. The documented user override ("pin what you want kept, forget what you never
want to see again") existed only as a field.

So the tests here start at the handler, not at `pin_facet`: the primitive was already
correct and already covered. What had to be proved is that a request reaches it and that
the EFFECT lands where the user was promised it would — in the ambient profile block the
model reads, which is the only reason a pin is worth anything.

The list route is part of the same clause: a key is what a pin is addressed by, and the
one existing facet reader (`learning_report._gather_facets`) drops the key and hides
forgotten facets. Without `GET /api/memory/facets` the two write routes would be
unreachable from any surface — which is the defect, one layer up.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from personalclaw.after_turn_review import capture_preference_facet
from personalclaw.memory_service import MemoryService
from personalclaw.preference_facets import (
    Facet,
    load_facets,
    render_profile_block,
)
from personalclaw.vector_memory import VectorMemoryStore

#: A message the shipped detector recognizes, and the facet text it distills out of it.
#: Both come from the product, not from this test: `detect_facet_candidate` returns the
#: matched style SPAN lowercased, with ``cue="explicit"`` — which is what makes a
#: first-observation facet Active (base stability 1.0 ≥ `_ACTIVE_AT`) and therefore
#: visible in the ambient block. A hand-built facet at the default ``cue="inferred"``
#: starts at 0.5, i.e. Provisional and invisible, so it could not be pinned INTO the
#: block or forgotten OUT of it — the fixture has to be the real thing.
MESSAGE = "please keep your answers short"
TEXT = "keep your answers short"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A real vector store on a temp path — never the user's home."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    vs = VectorMemoryStore(db_path=tmp_path / "memory.db", embedding_dim=3)
    vs.init()
    return vs


@pytest.fixture()
def svc(store):
    return MemoryService.over_vector_store(store)


@pytest.fixture()
def key(svc, store):
    """One stored facet, written by the SHIPPED capture path from a real user message.

    Not `upsert_facet` directly: the cue the detector chooses decides the starting
    stability, which decides whether the facet is Active at all, which is the entire
    premise of the pin/forget effects measured below.
    """
    assert capture_preference_facet(svc, MESSAGE) == TEXT, "the detector no longer fires"
    facets = load_facets(store)
    assert len(facets) == 1, f"expected one captured facet, got {[k for k, _ in facets]}"
    return facets[0][0]


@pytest.fixture()
def handlers(monkeypatch, svc):
    from personalclaw.dashboard.handlers import memory as mod

    monkeypatch.setattr(mod, "_get_service", lambda _state: svc)
    return mod


def _request(*, match=None, body=None, bad_json=False):
    request = MagicMock()
    request.match_info = match or {}
    request.app = {"state": MagicMock()}

    async def _json():
        if bad_json:
            raise json.JSONDecodeError("no", "", 0)
        return body or {}

    request.json = _json
    return request


def _facet(store, key) -> Facet:
    return dict(load_facets(store))[key]


# ── the list route: the only facet read that carries a key ───────────────────


@pytest.mark.asyncio
async def test_the_list_carries_the_key_a_pin_is_addressed_by(handlers, store, key):
    """🔑 The whole reason this route exists. `_gather_facets` renders the same facets
    without their keys, so no surface built on it could ever name one to pin."""
    resp = await handlers.api_memory_facets(_request())
    assert resp.status == 200
    facets = json.loads(resp.body)["facets"]
    assert [f["key"] for f in facets] == [key]
    assert facets[0]["cls"] == "style"
    assert facets[0]["pinned"] is False and facets[0]["forgotten"] is False


@pytest.mark.asyncio
async def test_the_list_reports_the_decayed_score_and_the_stored_one(handlers, store, key):
    """A facet's displayed strength must be the value the profile block trusts. Showing
    the stored score would tell a user a facet is strong after surfacing dropped it; the
    stored score rides along so the decay is visible rather than inferred."""
    row = _facet(store, key)
    row.updated_at = "2020-01-01T00:00:00+00:00"  # long past any class half-life
    store.set_semantic(key, row.to_payload(), 1.0, "test")

    facets = json.loads((await handlers.api_memory_facets(_request())).body)["facets"]
    assert facets[0]["stability"] < facets[0]["stored_stability"]
    assert facets[0]["state"] in {"Provisional", "Candidate", "Dropped"}


@pytest.mark.asyncio
async def test_a_forgotten_facet_is_still_listed_and_flagged(handlers, store, key):
    """Forgetting is final, so the list is the ONLY place a retirement is visible. Hiding
    it would leave the user unable to tell a facet they retired from one that was never
    learned — and they cannot re-learn it to find out."""
    assert (await handlers.api_memory_facet_forget(_request(match={"key": key}))).status == 200
    facets = json.loads((await handlers.api_memory_facets(_request())).body)["facets"]
    assert [(f["key"], f["forgotten"], f["state"]) for f in facets] == [(key, True, "Dropped")]


# ── the pin route ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pinning_holds_the_facet_in_the_profile_block(handlers, store, key):
    """🔴 THE defect, and the effect that makes a pin worth having: a facet decayed out of
    the ambient block comes back and stays. Measured against `render_profile_block`, not
    the flag, because the flag was always settable in a test — the block is what the model
    reads and what the user was promised."""
    row = _facet(store, key)
    row.updated_at = "2020-01-01T00:00:00+00:00"
    store.set_semantic(key, row.to_payload(), 1.0, "test")
    assert TEXT not in render_profile_block(store), "precondition: decayed out of the block"

    resp = await handlers.api_memory_facet_pin(_request(match={"key": key}, body={}))
    assert resp.status == 200 and json.loads(resp.body) == {"ok": True, "pinned": True}
    assert _facet(store, key).pinned is True
    assert TEXT in render_profile_block(store)


@pytest.mark.asyncio
async def test_unpinning_is_the_same_route(handlers, store, key):
    """`{"pinned": false}` releases it. One route, not a pin/unpin pair: pinning says
    "keep trusting this" and has no reason to be a one-way door the way a forget does."""
    await handlers.api_memory_facet_pin(_request(match={"key": key}, body={"pinned": True}))
    resp = await handlers.api_memory_facet_pin(_request(match={"key": key}, body={"pinned": False}))
    assert json.loads(resp.body) == {"ok": True, "pinned": False}
    assert _facet(store, key).pinned is False


@pytest.mark.asyncio
async def test_pinning_an_unknown_facet_is_a_404(handlers, store, key):
    """Not a silent 200. A stale row in an open panel must not report success for a write
    that reached nothing — that is the swallowed-write shape this whole issue is about."""
    resp = await handlers.api_memory_facet_pin(
        _request(match={"key": "pref.facet.style.deadbeef99"}, body={})
    )
    assert resp.status == 404
    assert "no such facet" in json.loads(resp.body)["error"]


@pytest.mark.asyncio
async def test_a_malformed_pin_body_is_a_400(handlers, store, key):
    resp = await handlers.api_memory_facet_pin(_request(match={"key": key}, bad_json=True))
    assert resp.status == 400
    assert _facet(store, key).pinned is False


# ── the forget route ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forgetting_drops_the_facet_out_of_the_profile_block(handlers, store, key):
    """The user-visible effect: stability reads 0 and the text leaves the block the model
    is given. The row survives, so the memory event log keeps the history."""
    assert TEXT in render_profile_block(store), "precondition: a fresh facet is Active"
    resp = await handlers.api_memory_facet_forget(_request(match={"key": key}))
    assert resp.status == 200 and json.loads(resp.body) == {"ok": True}
    assert _facet(store, key).forgotten is True
    assert TEXT not in render_profile_block(store)


@pytest.mark.asyncio
async def test_a_forget_cannot_be_undone_by_pinning(handlers, store, key):
    """Deliberate finality, pinned here because it is the surprising half of the contract
    and the frontend's confirm text promises exactly this. `decayed_stability` tests
    `forgotten` BEFORE `pinned`, so the pin route cannot become a resurrection route —
    this is the facet analogue of MGAV-8's human tombstone."""
    await handlers.api_memory_facet_forget(_request(match={"key": key}))
    resp = await handlers.api_memory_facet_pin(_request(match={"key": key}, body={"pinned": True}))
    assert resp.status == 200, "the pin itself is accepted — it just cannot bring it back"
    assert TEXT not in render_profile_block(store)
    facets = json.loads((await handlers.api_memory_facets(_request())).body)["facets"]
    assert facets[0]["stability"] == 0.0 and facets[0]["state"] == "Dropped"


@pytest.mark.asyncio
async def test_re_observing_a_forgotten_preference_does_not_resurrect_it(handlers, svc, store, key):
    """The other half of the finality, driven through the capture path that would do it:
    the detector seeing the same preference again is the loop this guard exists for. A
    user who retired a facet must not have to keep retiring it — and must not get a
    second row for the same text either, which is why the facet count is checked."""
    await handlers.api_memory_facet_forget(_request(match={"key": key}))
    assert capture_preference_facet(svc, MESSAGE) == TEXT  # the detector fires again
    assert [k for k, _ in load_facets(store)] == [key], "reinforced the same row, not a new one"
    assert _facet(store, key).forgotten is True
    assert TEXT not in render_profile_block(store)


@pytest.mark.asyncio
async def test_forgetting_an_unknown_facet_is_a_404(handlers, store, key):
    resp = await handlers.api_memory_facet_forget(_request(match={"key": "pref.facet.style.nope"}))
    assert resp.status == 404


# ── the route registration (a dotted key is the whole key) ───────────────────


def _registered_facet_routes() -> list[tuple[str, str]]:
    """The (verb, path) pairs `server.py` really registers under /api/memory/facets.

    Read out of the source because the registrations live inside ``start_dashboard``,
    which cannot be called without binding a port. The pattern STRING is what is under
    test here, so reading it from the file that owns it is the faithful input — a
    hand-copied pattern would prove only that this test's copy works.
    """
    import ast
    from pathlib import Path

    from personalclaw.dashboard import server as server_mod

    verbs = {"add_get": "GET", "add_post": "POST"}
    out: list[tuple[str, str]] = []
    tree = ast.parse(Path(server_mod.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        verb = verbs.get(node.func.attr)
        first = node.args[0] if node.args else None
        if not verb or not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        if first.value.startswith("/api/memory/facets"):
            out.append((verb, first.value))
    return out


@pytest.mark.asyncio
async def test_the_registered_paths_match_a_dotted_facet_key():
    """A facet key is `pref.facet.style.<md5>`. aiohttp's default `{key}` segment match
    stops at the first dot, so a default pattern resolves the pin route with `key="pref"`
    — a 404 for every real facet, from routes that read as correctly wired.

    "The route is registered" and "the route matches a real key" are different claims and
    only the second one is the defect, so the patterns are resolved against a real key.
    """
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    routes = _registered_facet_routes()
    assert sorted(routes) == [
        ("GET", "/api/memory/facets"),
        ("POST", "/api/memory/facets/{key:.+}/forget"),
        ("POST", "/api/memory/facets/{key:.+}/pin"),
    ], f"the facet routes changed shape: {sorted(routes)}"

    app = web.Application()
    for verb, pattern in routes:
        app.router.add_route(verb, pattern, lambda _r: None)

    key = "pref.facet.style.0123456789"
    for verb, pattern in routes:
        path = pattern.replace("{key:.+}", key)
        match = await app.router.resolve(make_mocked_request(verb, path))
        assert match.http_exception is None, f"{verb} {path} did not resolve"
        if "{key" in pattern:
            assert match.get("key") == key, f"{path} matched key={match.get('key')!r}"

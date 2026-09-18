"""Skills search: an unscoped query must return at least what a scoped one does (#301).

`GET /api/skills/search` documents its own contract — "search across ALL registered skill
providers … an optional `marketplace` param RESTRICTS to a single provider" — and did the
exact inverse. The unscoped fan-out ran every hit through an already-installed DROP filter
that the scoped branch does not have, and on a stock install that filter removed the entire
catalogue:

* the gateway copies the whole bundled skill tree into the user's skills dir at startup
  (`skills/loader.py:_ensure_builtin_skills`);
* the `native` marketplace is registered against that same bundled dir, and its entry
  `id`/`name` is the bare directory name;
* `installed_names` is keyed on those same names.

So `native`'s id set was ALWAYS a subset of the installed set, `filtered` was ALWAYS `[]`,
and Skills → Browse rendered "No results — try a different search term or marketplace" for
every query a fresh install could make. Silent-wrong, at HTTP 200, with no log line.

Tested as the INVARIANT rather than the symptom, which is the issue's own suggestion:
`search(q) ⊇ search(q, marketplace=m)` for every registered `m`. That catches this bug, the
mirror-image bug (a scoped branch that grows its own filter), and any future re-divergence
— a symptom test pinned to "grill" would pass again the moment the filter came back keyed
on something else.

The catalogue here is a real `NativeSkillsMarketplace` over a temp dir, with the loader
reporting those same names as installed — the exact overlap the live instance has, rather
than the `list_skills() == []` stub the other skills suites use (which is precisely why
none of them saw this).
"""

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers.skills import search_marketplaces_counted
from personalclaw.skills.marketplace import SkillsRegistry
from personalclaw.skills.native import NativeSkillsMarketplace

BUNDLED = ("grill", "artifacts", "presentations")


@pytest.fixture()
def catalogue(tmp_path, monkeypatch):
    """A `native` + `installed` registry whose catalogue is FULLY installed.

    Both marketplaces exist on a stock install and both report `marketplace_type ==
    "native"`; `installed` mirrors the user's skills dir, so the fan-out skips it (correct
    — it would list every skill twice) and every `native` hit is already present.
    """
    root = Path(tmp_path) / "bundled"
    for name in BUNDLED:
        d = root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: A {name} helper for widgets.\n---\n\nbody\n",
            encoding="utf-8",
        )

    reg = SkillsRegistry()
    reg.register("native", NativeSkillsMarketplace(root=root))
    reg.register("installed", NativeSkillsMarketplace(root=root))
    monkeypatch.setattr(
        "personalclaw.skills.marketplace.get_default_skills_registry", lambda: reg, raising=True
    )
    # The whole catalogue reads as installed — the live shape, not an empty stub.
    monkeypatch.setattr(
        "personalclaw.skills.loader.SkillsLoader.list_skills",
        lambda self: [{"key": n} for n in BUNDLED],
        raising=True,
    )
    return reg


@asynccontextmanager
async def _client():
    """The two search routes over the patched registry.

    An async-CONTEXT-MANAGER helper rather than an async fixture: `test_task_*` uses the same
    shape, and it keeps the client's lifetime inside the test that drives it.
    """
    from personalclaw.dashboard.handlers.skills import (
        api_skills_marketplaces,
        api_skills_search,
    )

    app = web.Application()
    app.router.add_get("/api/skills/search", api_skills_search)
    app.router.add_get("/api/skills/marketplaces", api_skills_marketplaces)
    async with TestClient(TestServer(app)) as c:
        yield c


async def _search(client, query, marketplace=""):
    url = f"/api/skills/search?q={query}"
    if marketplace:
        url += f"&marketplace={marketplace}"
    return await (await client.get(url)).json()


# ── the invariant ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unscoped_search_is_a_superset_of_every_scoped_search(catalogue):
    """The endpoint's documented contract, asserted directly. `marketplace=` RESTRICTS, so
    scoped ⊆ unscoped for every registered provider."""
    async with _client() as client:
        for query in ("grill", "widgets", "a"):
            every = {r["id"] for r in (await _search(client, query))["results"]}
            for name in catalogue.list():
                ids = {r["id"] for r in (await _search(client, query, name))["results"]}
                assert ids <= every, f"{name} returns {ids - every} that all-marketplaces does not"


@pytest.mark.asyncio
async def test_the_default_scope_returns_the_catalogue_it_has(catalogue):
    """The user-visible symptom: the UI's own default call. `installed_names` covering the
    whole catalogue must not empty the answer."""
    async with _client() as client:
        body = await (await client.get("/api/skills/search?q=grill&limit=30")).json()
    assert [r["id"] for r in body["results"]] == ["grill"]
    assert body["counts"]["native"] == 1


@pytest.mark.asyncio
async def test_an_installed_hit_is_labelled_rather_than_withheld(catalogue):
    async with _client() as client:
        body = await _search(client, "widgets")
    assert {r["id"] for r in body["results"]} == set(BUNDLED)
    # ANNOTATED, not dropped — the frontend was already built to render this
    # (`installedIds` + `installed` on the detail view) and had nothing to render it from.
    assert all(r["installed"] is True for r in body["results"])


@pytest.mark.asyncio
async def test_both_branches_agree_about_one_row(catalogue):
    """A row is installed or not regardless of how it was asked for. The scoped branch used
    to report `installed: false` for the very row the unscoped branch dropped as installed."""
    async with _client() as client:
        unscoped = await _search(client, "grill")
        scoped = await _search(client, "grill", "native")
    assert unscoped["results"][0]["installed"] is True
    assert scoped["results"][0]["installed"] is True


@pytest.mark.asyncio
async def test_a_not_installed_skill_is_labelled_false(catalogue, monkeypatch):
    """VACUITY FLOOR for the annotation. `installed: true` on everything would satisfy the
    test above while saying nothing — and would be exactly as wrong in the other direction.
    """
    monkeypatch.setattr(
        "personalclaw.skills.loader.SkillsLoader.list_skills",
        lambda self: [{"key": "grill"}],
        raising=True,
    )
    async with _client() as client:
        body = await _search(client, "widgets")
    by_id = {r["id"]: r["installed"] for r in body["results"]}
    assert by_id["grill"] is True
    assert by_id["artifacts"] is False
    assert by_id["presentations"] is False


@pytest.mark.asyncio
async def test_the_installed_mirror_is_still_not_fanned_out_to(catalogue):
    """The one exclusion that is CORRECT stays. `installed` mirrors the user's own skills
    dir, so fanning out to it would list every skill twice — a different fact from the drop
    filter this change removed, and this asserts the two were not conflated."""
    results, counts = search_marketplaces_counted("widgets", limit=20)
    assert set(counts) == {"native"}
    assert len(results) == len(BUNDLED)

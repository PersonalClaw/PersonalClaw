"""Nothing to install FROM and zero MATCHES are different facts, and the search said neither.

Two sources register at import (`skills/native.py`): `native` mirrors the bundled catalogue and
`installed` mirrors the user's own skills. Neither is somewhere to install FROM — the fan-out
skips `installed` outright and then filters every `native` hit out as already present. So on a
fresh install the store can offer nothing FOR ANY QUERY, and `{"results": [], "counts": {}}` was
byte-identical to a query that genuinely matched nothing. The panel then showed *"No results —
try a different search term or marketplace"*, blaming the query (issue 1780).

Issue 1780 reads this as "the registry is empty". It is not, and that matters: `len(info())` is
**2** on a fresh install, so a naive count would have reported "sources configured" about two
mirrors of what the user already has. `installable_sources` counts sources whose declared
`marketplace_type` is not `native`, which is a property the interface already carries rather
than the two names hardcoded.

This is the repo's own "a failed fetch renders as an empty state" shape: the surface cannot be
told apart from a working surface with nothing to show, so nobody notices for as long as it takes
somebody to go looking.

**The response carries a COUNT, not a message.** The store's source filter already renders
per-source counts, so a number is the fact the frontend is missing; composing the sentence in the
handler would put UI copy in an API. The panel branches on `=== 0` rather than on a falsy check, so
a FAILED request keeps the ordinary wording instead of asserting a configuration fact it never
learned.

What is deliberately NOT changed: no marketplace is bundled to make the registry non-empty. Which
catalogue ships by default is a product decision, and shipping "the middle" — a slightly better
empty state plus a half-registered source — is what the issue asks not to do.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request


class _StubMarketplace:
    """The smallest thing the registry accepts, and it MATCHES NOTHING on purpose.

    A source that returns rows would make `results: []` impossible, and the whole point is to
    separate "no rows because no sources" from "no rows because nothing matched".
    """

    marketplace_type = "stub"
    trust_tier = "community"

    def search(self, query: str, limit: int = 20):
        return []


def _search(registry, query: str = "postgres", marketplace: str = ""):
    """Drive `GET /api/skills/search` against a given registry."""
    import personalclaw.dashboard.handlers.skills as H
    from personalclaw.skills import marketplace as mp_mod

    app = web.Application()
    app["state"] = SimpleNamespace()
    q = f"/api/skills/search?q={query}" + (f"&marketplace={marketplace}" if marketplace else "")
    req = make_mocked_request("GET", q, app=app)

    original = mp_mod.get_default_skills_registry
    mp_mod.get_default_skills_registry = lambda: registry
    try:
        import asyncio

        resp = asyncio.run(H.api_skills_search(req))
    finally:
        mp_mod.get_default_skills_registry = original
    return resp, json.loads(resp.body)


def _registry(*names: str):
    from personalclaw.skills.marketplace import SkillsRegistry

    reg = SkillsRegistry()
    for name in names:
        reg.register(name, _StubMarketplace())
    return reg


def test_an_empty_registry_reports_zero_installable_sources():
    """The bug. Before this, the response was indistinguishable from a no-match search, so the
    store told the user to try a different term when there was nothing to search at all."""
    resp, body = _search(_registry())

    assert resp.status == 200
    assert body["results"] == []
    assert body["installable_sources"] == 0


def test_a_registered_source_that_matches_nothing_reports_one_source():
    """The other side of the distinction, and the reason a boolean like `has_sources` would not
    have been enough on its own: this response is ALSO `results: []`, and it must keep the
    ordinary "no results" wording."""
    resp, body = _search(_registry("skills.sh"))

    assert resp.status == 200
    assert body["results"] == []
    assert body["installable_sources"] == 1


def test_the_count_is_the_number_of_sources_reached():
    """Not a flag. The store's source filter already shows per-source counts, so the number is
    what the frontend is missing — and it stays honest as sources are added."""
    _, body = _search(_registry("skills.sh", "packs", "vendor-catalog"))
    assert body["installable_sources"] == 3


def test_a_named_marketplace_branch_answers_the_same_shape():
    """Both branches of this endpoint must agree, or the frontend has to know which one it hit.
    Reaching the named branch means the marketplace resolved, so `sources` is 1 there."""
    resp, body = _search(_registry("skills.sh"), marketplace="skills.sh")

    assert resp.status == 200
    assert body["installable_sources"] == 1
    assert body["counts"] == {"skills.sh": 0}


def test_an_unknown_named_marketplace_is_still_a_404():
    """A typo'd source name is not "no sources", and it is refused in the shared envelope."""
    resp, body = _search(_registry("skills.sh"), marketplace="nope")
    assert resp.status == 404
    assert body == {
        "error": {
            "code": "skills_marketplace_not_found",
            "message": "No skill catalogue named 'nope' is set up here.",
        }
    }


def _bare_search(query_string: str):
    import asyncio

    import personalclaw.dashboard.handlers.skills as H

    app = web.Application()
    app["state"] = SimpleNamespace()
    req = make_mocked_request("GET", f"/api/skills/search?{query_string}", app=app)
    resp = asyncio.run(H.api_skills_search(req))
    return resp, json.loads(resp.body)


def test_a_missing_query_is_still_a_400():
    """`sources` must not turn an argument error into an empty result."""
    resp, body = _bare_search("q=")
    assert resp.status == 400
    assert body["error"]["code"] == "bad_request"


@pytest.mark.parametrize("limit", ["many", "0", "-3"])
def test_an_unusable_limit_is_refused_not_a_crash(limit):
    resp, body = _bare_search(f"q=postgres&limit={limit}")
    assert resp.status == 400
    assert body == {
        "error": {"code": "bad_request", "message": "`limit` is a whole number of 1 or more."}
    }


def test_the_shipped_registry_has_only_native_mirrors():
    """The premise, measured rather than assumed — and it corrects the issue.

    Importing the handler pulls in `skills/native.py`, which registers two sources. Both declare
    `type: "native"`, so both are mirrors of what is already on this machine and neither is a
    catalogue. That is why `installable_sources` filters on the type instead of counting the
    registry: `len(info())` is 2 here, and reporting 2 would have said "configured" about the
    exact situation this fix exists to describe.

    If a real catalogue is ever bundled this fails, which is the right prompt: the empty state's
    copy ("install a skill-source app to add one") stops being true the moment one ships, and a
    stale reassurance is worse than the wording it replaced.
    """
    import personalclaw.dashboard.handlers.skills  # noqa: F401 — registers the native sources
    from personalclaw.skills.marketplace import get_default_skills_registry

    info = get_default_skills_registry().info()
    assert info, "nothing registered at all — this test would then prove nothing"
    assert [
        m for m in info if m["type"] != "native"
    ] == [], "a non-native catalogue now ships; the store's empty-state copy needs revisiting"


class _UnreachableMarketplace(_StubMarketplace):
    """A catalogue that cannot be reached: its search raises, as a catalogue behind a dead network
    does."""

    def search(self, query: str, limit: int = 20):
        raise RuntimeError("the catalogue did not answer")


def test_a_catalogue_that_could_not_be_searched_is_named():
    """The third empty state. `results: []` with a catalogue registered read as "no results — try a
    different term" when that catalogue never answered; it is named in `unreachable` instead, and
    the ones that did answer are still searched."""
    reg = _registry("packs")
    reg.register("skills.sh", _UnreachableMarketplace())
    resp, body = _search(reg)

    assert resp.status == 200
    assert body["results"] == []
    assert body["installable_sources"] == 2
    (missed,) = body["unreachable"]
    assert missed["source"] == "skills.sh"
    assert missed["reason"], "why it could not be searched is said"
    assert (
        "did not answer" not in missed["reason"]
    ), "an unexpected fault's own text stays in the log"


def test_a_scoped_search_whose_catalogue_fails_says_so_in_the_envelope():
    """The scoped branch answers one catalogue, so its failure is the whole answer: a coded
    failure the page can branch on, naming the catalogue, with the fault's own text left in the
    log."""
    reg = _registry("packs")
    reg.register("skills.sh", _UnreachableMarketplace())
    resp, body = _search(reg, marketplace="skills.sh")
    assert resp.status == 500
    assert body["error"]["code"] == "skills_search_failed"
    assert body["error"]["message"].startswith("Couldn't search skills.sh. ")
    assert "did not answer" not in body["error"]["message"]


def test_a_search_every_catalogue_answered_names_none():
    _, body = _search(_registry("skills.sh"))
    assert body["unreachable"] == []
    _, scoped = _search(_registry("skills.sh"), marketplace="skills.sh")
    assert scoped["unreachable"] == [], "both branches answer the same shape"

"""The links the gateway writes name no address the dashboard has retired (#3815).

A retired address is a row of the dashboard's redirect table, ``web/src/app/routeRedirects.ts``:
the router sends it on to where its page is now, so a link someone kept from before still opens.
A link the gateway writes now names the new address, so it opens its page as itself, and the
table carries only links from before.

Memory left Settings for Library's Memory tab, ``#/memory``, and ``#/settings/memory`` is a row of
that table. Held here, for the gateway's producers of links to Memory:

  · the investigate back link of a memory record and of a learned lesson is ``#/memory``;
  · the Discover tip for memory opens ``memory``, with no query;
  · no back link the investigate resolvers write, and no Discover tip, is an address a row of the
    table retires. Each row's old path is read from the table and matched as the router matches
    it: segment by segment, empty segments dropped, each segment decoded once, a ``:name``
    standing for any one segment.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from personalclaw import investigate as inv
from personalclaw.legibility import discover as dc

_REDIRECTS = Path("web/src/app/routeRedirects.ts")


def _retired_paths() -> list[str]:
    """Each row's old path, from the table the router reads."""
    src = _REDIRECTS.read_text(encoding="utf-8")
    table = re.search(
        r"export const ROUTE_REDIRECTS: readonly RouteRedirect\[\] = \[(.*?)\n\]", src, re.S
    )
    assert table, "routeRedirects.ts ROUTE_REDIRECTS shape changed — update this parser"
    rows = re.findall(r"\{\s*from: '([^']+)'", table.group(1))
    assert {"files/:slug", "settings/vocabulary", "settings/memory"} <= set(rows), rows
    return rows


def _retired_by(link: str, rows: list[str]) -> str | None:
    """The row that retires ``link`` (written with or without its ``#/``), or None."""
    path = re.sub(r"^#?/?", "", link).split("?", 1)[0]
    segments = [unquote(s) for s in path.split("/") if s]
    for row in rows:
        parts = row.split("/")
        same = all(p.startswith(":") or p == s for p, s in zip(parts, segments))
        if len(parts) == len(segments) and same:
            return row
    return None


def _back_links() -> list[str]:
    """Every back link an investigate resolver writes as a literal. A ``{…}`` placeholder of an
    f-string fills one segment, which only a row's ``:name`` matches."""
    src = Path(inv.__file__).read_text(encoding="utf-8")
    links = re.findall(r'back_link=f?"([^"]*)"', src)
    assert len(links) >= 10, f"parsed back links look wrong: {links}"
    return links


def test_control_the_matcher_reads_an_address_as_the_router_does():
    rows = _retired_paths()
    assert _retired_by("#/settings/memory", rows) == "settings/memory"
    assert _retired_by("settings//%6Demory/?tab=audit", rows) == "settings/memory"
    assert _retired_by("#/files/report-1", rows) == "files/:slug"
    assert _retired_by("#/files/{art.slug}", rows) == "files/:slug"
    assert _retired_by("#/memory", rows) is None
    assert _retired_by("#/settings/memory/extra", rows) is None
    assert _retired_by("#/artifacts/{art.slug}", rows) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory_record", "memory_lesson"])
async def test_a_memory_back_link_opens_the_memory_tab(kind, monkeypatch):
    rec = SimpleNamespace(
        id="fact:pref.tone",
        kind="semantic",
        tier="core",
        scope="global",
        confidence=0.9,
        importance=0.5,
        recall_count=2,
        created_at="2026-10-01",
        updated_at="2026-10-02",
        source="chat",
        category="",
        tags=[],
        superseded_by=None,
        text="Prefers short answers",
        value=None,
    )
    svc = SimpleNamespace(
        get_record=lambda key: rec if key == rec.id else None, get_lessons=lambda: []
    )
    monkeypatch.setattr(inv, "_memory_service", lambda state: svc)
    ctx = await inv.resolve(kind, rec.id, SimpleNamespace())
    assert ctx is not None
    assert ctx.back_link == "#/memory"


def test_the_discover_tip_for_memory_opens_the_memory_tab():
    tip = next(t for t in dc.CATALOG if t.id == "memory")
    assert tip.try_it["route"] == "memory"
    assert tip.try_it["query"] == {}


def test_no_investigate_back_link_is_a_retired_address():
    rows = _retired_paths()
    assert [(link, row) for link in _back_links() if (row := _retired_by(link, rows))] == []


def test_no_discover_tip_opens_a_retired_address():
    rows = _retired_paths()
    tips = [(t.id, t.try_it["route"]) for t in dc.CATALOG]
    assert [(tip, route, row) for tip, route in tips if (row := _retired_by(route, rows))] == []

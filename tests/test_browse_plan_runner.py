"""Tests for the production content tick runner (BROWSE-AUTOMATION §(d)/A3, BA-6)."""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.browse import plans as bp
from personalclaw.browse.plan_runner import make_content_tick_runner
from personalclaw.guardrails.autonomy import RUNG_ONE_TAP

_MISSING = object()


@pytest.fixture()
def plan_home(tmp_path, monkeypatch):
    """An isolated home so a plan write never touches the operator's real ``browse/plans``."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _watch(**over) -> bp.BrowsePlan:
    base = dict(
        id="w1", goal="watch the changelog", kind=bp.KIND_WATCH_PAGE, start_url="https://x/c"
    )
    base.update(over)
    return bp.BrowsePlan(**base)


def _walk(**over) -> bp.BrowsePlan:
    base = dict(
        id="f1", goal="file the form", kind=bp.KIND_WALK_FLOW, start_url="https://x/f", submits=True
    )
    base.update(over)
    return bp.BrowsePlan(**base)


class FakePage:
    def __init__(self, html: str, url: str = ""):
        self._html = html
        self._url = url

    async def html(self) -> str:
        return self._html

    async def current_url(self) -> str:
        return self._url


class FakeSession:
    def __init__(self, *, start_exc=None, nav_exc=None):
        self.started = False
        self.navigated: list[str] = []
        self._start_exc = start_exc
        self._nav_exc = nav_exc

    async def start(self) -> None:
        if self._start_exc is not None:
            raise self._start_exc
        self.started = True

    async def navigate(self, url: str):
        if self._nav_exc is not None:
            raise self._nav_exc
        self.navigated.append(url)


class _Opener:
    """Records the closer call and any open fault; yields ``(session, page, closer)``."""

    def __init__(self, session, page, *, open_exc=None):
        self.session = session
        self.page = page
        self._open_exc = open_exc
        self.closed = 0
        self.opened_with: list[str] = []

    async def __call__(self, cdp_url: str):
        self.opened_with.append(cdp_url)
        if self._open_exc is not None:
            raise self._open_exc

        async def _close() -> None:
            self.closed += 1

        return self.session, self.page, _close


def _runner(opener, *, cdp_url="ws://gw/1", extract=_MISSING, settle=None):
    kw = dict(open_session=opener, resolve_url=lambda: cdp_url, settle=settle)
    if extract is not _MISSING:
        kw["extract"] = extract
    return make_content_tick_runner(**kw)


def test_watch_page_renders_and_extracts():
    page = FakePage("<article>Real article body here.</article>", url="https://x/final")
    opener = _Opener(FakeSession(), page)
    out = asyncio.run(_runner(opener)(_watch()))
    assert out.ok is True and out.verified is True
    assert "Real article body" in out.content
    assert out.html == "<article>Real article body here.</article>"  # raw markup carried too
    assert out.final_url == "https://x/final"
    assert opener.session.started and opener.session.navigated == ["https://x/c"]
    assert opener.closed == 1 and opener.opened_with == ["ws://gw/1"]


def test_walk_flow_is_refused_before_any_browser_work():
    opener = _Opener(FakeSession(), FakePage(""))
    with pytest.raises(bp.PlanError):
        asyncio.run(_runner(opener)(_walk()))
    assert opener.opened_with == [] and opener.closed == 0


def test_empty_cdp_url_fails_soft_without_opening():
    opener = _Opener(FakeSession(), FakePage("<article>text</article>"))
    out = asyncio.run(_runner(opener, cdp_url="")(_watch()))
    assert out.ok is False and "cdp_url" in out.note
    assert opener.opened_with == []


def test_open_fault_is_soft_and_leaks_nothing():
    opener = _Opener(FakeSession(), FakePage("x"), open_exc=RuntimeError("browser down"))
    out = asyncio.run(_runner(opener)(_watch()))
    assert out.ok is False and "failed" in out.note and "browser down" in out.note
    assert opener.closed == 0  # never opened → nothing to close


def test_drive_fault_still_closes_the_session():
    opener = _Opener(FakeSession(nav_exc=RuntimeError("nav boom")), FakePage("x"))
    out = asyncio.run(_runner(opener)(_watch()))
    assert out.ok is False and "nav boom" in out.note
    assert opener.closed == 1  # finally ran despite the fault


def test_empty_render_does_not_confirm():
    # A JS shell that renders no extractable text → ok=False so the content cursor never advances.
    opener = _Opener(FakeSession(), FakePage("<div id='root'></div>", url="https://x/c"))
    out = asyncio.run(_runner(opener)(_watch()))
    assert out.ok is False and out.verified is False
    assert "no extractable text" in out.note
    assert "root" in out.html  # raw markup still carried for a caller's own DOM detectors
    assert opener.closed == 1


def test_settle_hook_runs_after_navigate():
    order: list[str] = []
    page = FakePage("<article>text body content</article>", url="u")

    class _S(FakeSession):
        async def navigate(self, url):
            order.append("nav")
            await super().navigate(url)

    async def _settle(_pg):
        order.append("settle")

    asyncio.run(_runner(_Opener(_S(), page), settle=_settle)(_watch()))
    assert order == ["nav", "settle"]


def test_custom_extract_is_used():
    class _Ext:
        def __init__(self, text):
            self.text = text

    def _extract(_html, *, url=""):
        return _Ext(f"extracted:{url}")

    opener = _Opener(FakeSession(), FakePage("<html/>", url="u"))
    out = asyncio.run(_runner(opener, extract=_extract)(_watch()))
    assert out.content == "extracted:https://x/c"


def test_composes_with_execute_tick_idempotently(plan_home):
    # Prove the production runner feeds plans.execute_tick's idempotent cursor: the same page is
    # a change on the first tick and unchanged on the second.
    bp.save_plan(_watch())
    runner = _runner(_Opener(FakeSession(), FakePage("<article>changelog v2 shipped</article>")))
    first = asyncio.run(bp.execute_tick(bp.load_plan("w1"), run=runner, granted_rung=RUNG_ONE_TAP))
    assert first.changed is True
    assert "changelog v2 shipped" in first.html  # rendered markup flows up through execute_tick
    second = asyncio.run(bp.execute_tick(bp.load_plan("w1"), run=runner, granted_rung=RUNG_ONE_TAP))
    assert second.changed is False

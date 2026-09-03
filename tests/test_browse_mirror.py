"""BA-5 — the live mirror, the browse kill switch, and the mirror seam.

Three behaviours, each proven with its own control where an absence would otherwise pass
vacuously:

* the loop RELAYS every step (url + action + screenshot path) through its ``on_step`` sink, and a
  credential in the URL is screened on the relayed surface exactly as it is everywhere else;
* the browse kill switch stops browse — a running loop parks, a new run refuses — and is DISTINCT
  from the incident switch (engaging one leaves the other alone);
* the seam raises the banner + a needs_input inbox item at the expired write, and dedups per site.

The credential-handoff INVARIANT (§5.2) and the profile-encryption key live in
``test_browse_credential_handoff.py`` beside the rest of the handoff; this file is the mirror.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.browse_provider import BrowseActionProvider
from personalclaw.browse import killswitch
from personalclaw.browse import mirror as bmirror
from personalclaw.browse.loop import PARK_KILLED, run_browse_loop
from personalclaw.dashboard.handlers import browse_mirror as bm

# A distinctive credential literal so a sweep that finds it has found THAT value, not a substring.
OAUTH_CODE = "AUTHZ-mirror-CODEVALUE"
PLAIN_URL = "https://shop.test/catalog"
CALLBACK_URL = f"https://shop.test/callback?code={OAUTH_CODE}&country=US"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """`PERSONALCLAW_HOME` isolates the kill flag file, the profiles, and the inbox under a tmp
    home; the mirror reset drops the in-process kill mirror so one test's engage never leaks."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    killswitch.reset_browse_kill_mirror()
    yield home
    killswitch.reset_browse_kill_mirror()


# ── harness ─────────────────────────────────────────────────────────────────


class _FakePage:
    def __init__(self, pages: dict[str, str], *, url: str, shot: str = "") -> None:
        self._pages = dict(pages)
        self.url = url
        self._shot = shot

    async def html(self) -> str:
        return self._pages.get(self.url, "<html><body>nothing</body></html>")

    async def current_url(self) -> str:
        return self.url

    async def click(self, ref) -> None: ...
    async def fill(self, ref, value) -> None: ...
    async def submit(self) -> None: ...
    async def scroll(self, direction) -> None: ...
    async def go_back(self) -> None: ...

    async def screenshot(self) -> str:
        return self._shot


class _FakeSession:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    async def start(self) -> None: ...

    async def navigate(self, url: str):
        self._page.url = url
        return SimpleNamespace(ok=True, allowed=True, url=url, reason="", error="")


class _Decide:
    def __init__(self, *replies: str, fallback: str = "DONE") -> None:
        self.replies = list(replies)
        self.fallback = fallback
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else self.fallback


class _FakeState:
    """Records WS frames and notifications. No `_inbox_svc`, so `emit_attention_item` falls back to
    a fresh on-disk InboxStore under the isolated home — which the tests then read back."""

    def __init__(self) -> None:
        self.ws: list[tuple[str, dict]] = []
        self.notes: list[tuple] = []

    def broadcast_ws(self, msg_type: str, data: dict) -> None:
        self.ws.append((msg_type, data))

    def notify(self, kind: str, title: str, body: str, *, meta: dict | None = None) -> None:
        self.notes.append((kind, title, body))


def _loop(page: _FakePage, decide: _Decide, *, on_step=None, kill_check=None, start=PLAIN_URL):
    return _run(
        run_browse_loop(
            goal="read the page",
            start_url=start,
            session=_FakeSession(page),
            page=page,
            decide=decide,
            max_steps=4,
            on_step=on_step,
            kill_check=kill_check,
        )
    )


# ══════════════════════════════════════════════════════════════════════════════
# The live mirror relay
# ══════════════════════════════════════════════════════════════════════════════


class TestTheStepRelay:
    def test_every_step_is_relayed_with_url_action_and_screenshot(self):
        """The loop calls `on_step` once per completed step with the screenshot PATH, the screened
        URL, and the rendered action — the three fields the mirror renders."""
        page = _FakePage(
            {PLAIN_URL: "<html><body><h1>Catalog</h1></body></html>"},
            url=PLAIN_URL,
            shot="/tmp/pc-shot-1.png",
        )
        seen: list[tuple] = []
        result = _loop(
            page,
            _Decide("NOTES looked at the catalog", "DONE"),
            on_step=lambda s, shot: seen.append((s.index, s.url, s.action, shot)),
        )
        assert result.ok
        assert seen, "the mirror sink was never called"
        assert [s[0] for s in seen] == list(range(1, len(seen) + 1)), "step indices are dense"
        assert all(s[3] == "/tmp/pc-shot-1.png" for s in seen), "screenshot path not relayed"
        assert seen[-1][2] == "DONE", "the last action reaches the mirror"

    def test_a_run_with_no_sink_still_runs(self):
        """CONTROL: `on_step=None` is the pre-BA-5 behaviour, so the relay is additive — the loop
        completes identically whether or not anyone is watching."""
        page = _FakePage({PLAIN_URL: "<html><body><h1>Catalog</h1></body></html>"}, url=PLAIN_URL)
        result = _loop(page, _Decide("DONE"), on_step=None)
        assert result.ok and result.step_count >= 1

    def test_a_credential_in_the_url_is_screened_on_the_relayed_step(self):
        """The mirror is a surface a credential could reach, so it gets the same screen as every
        other: the relayed step URL carries `code=[withheld]`, never the authorization code."""
        page = _FakePage(
            {CALLBACK_URL: "<html><body><h1>Signed in</h1></body></html>"},
            url=CALLBACK_URL,
            shot="/tmp/pc-shot-2.png",
        )
        seen: list = []
        _loop(
            page,
            _Decide("NOTES landed on the callback", "DONE"),
            on_step=lambda s, shot: seen.append(s),
            start=CALLBACK_URL,
        )
        blob = "\n".join(f"{s.url} {s.action} {s.note}" for s in seen)
        assert OAUTH_CODE not in blob, "the authorization code leaked onto the mirror"
        assert "code=" in blob and "country=US" in blob, "the URL must stay diagnosable"

    def test_the_provider_sink_relays_through_the_seam(self, monkeypatch):
        """The provider's `_mirror_sink` builds the `{run_id, step_n, url, action, screenshot}`
        payload and hands it to the seam — the wiring the loop cannot test on its own."""
        captured: list[dict] = []
        monkeypatch.setattr(
            bmirror, "broadcast_browse_step", lambda payload, **kw: captured.append(payload)
        )
        ctx = ActionContext(event="e", payload={"run_id": "r9"})
        sink = BrowseActionProvider()._mirror_sink(ctx)
        from personalclaw.browse.loop import BrowseStep

        sink(
            BrowseStep(index=3, url=PLAIN_URL, action="CLICK ab12", fenced=True, note="clicked"),
            "/tmp/pc-shot-3.png",
        )
        assert captured == [
            {
                "run_id": "r9",
                "step_n": 3,
                "url": PLAIN_URL,
                "action": "CLICK ab12",
                "screenshot": "/tmp/pc-shot-3.png",
                "note": "clicked",
            }
        ]


# ══════════════════════════════════════════════════════════════════════════════
# The kill switch — distinct from the incident switch
# ══════════════════════════════════════════════════════════════════════════════


class TestTheKillSwitch:
    def test_engage_and_release_round_trip(self):
        assert killswitch.browse_killed() is False
        st = killswitch.engage("looks wrong")
        assert st.active and st.reason == "looks wrong" and st.started_at
        assert killswitch.browse_killed() is True
        killswitch.release()
        assert killswitch.browse_killed() is False

    def test_it_is_distinct_from_the_incident_switch(self):
        """The whole reason it exists: stopping browse must NOT halt every other automation. So
        engaging the browse kill leaves incident mode untouched."""
        from personalclaw.guardrails import incident

        incident.reset_incident_mirror()
        killswitch.engage("stop browse only")
        assert killswitch.browse_killed() is True
        assert incident.incident_active() is False, "browse kill must not engage incident mode"

    def test_a_running_loop_parks_when_the_kill_is_engaged(self):
        """`kill_check` is consulted before each model call, so a mid-run kill parks within one
        step — the mirror's stop actually stops an in-flight run."""
        page = _FakePage({PLAIN_URL: "<html><body><h1>hi</h1></body></html>"}, url=PLAIN_URL)
        decide = _Decide("DONE")
        result = _loop(page, decide, kill_check=lambda: (True, "stopped by human"))
        assert result.parked and result.park_reason == PARK_KILLED
        assert result.ok, "a kill is a park, not a failure — notes are kept"
        assert decide.prompts == [], "the kill preempts the first model call"

    def test_an_unkilled_loop_does_not_park(self):
        """CONTROL: the same harness with `kill_check` reporting not-killed runs to completion, so
        the park above is the kill and not the fixture."""
        page = _FakePage({PLAIN_URL: "<html><body><h1>hi</h1></body></html>"}, url=PLAIN_URL)
        result = _loop(page, _Decide("DONE"), kill_check=lambda: (False, ""))
        assert not (result.parked and result.park_reason == PARK_KILLED)

    def test_a_new_run_refuses_to_start_while_killed(self):
        killswitch.engage("stop")
        result = _run(
            BrowseActionProvider().execute(
                {"goal": "read", "start_url": PLAIN_URL}, ActionContext(event="e")
            )
        )
        assert result.success is False
        assert result.agent_error is not None
        assert result.agent_error.code == "ERR_BROWSE_KILLED"

    def test_a_new_run_starts_once_released(self, monkeypatch):
        """CONTROL: released, the SAME call gets PAST the kill check — it fails later on the missing
        CDP target, which proves the kill was the only thing stopping it before."""
        killswitch.engage("stop")
        killswitch.release()
        result = _run(
            BrowseActionProvider().execute(
                {"goal": "read", "start_url": PLAIN_URL}, ActionContext(event="e")
            )
        )
        assert result.agent_error is not None
        assert result.agent_error.code != "ERR_BROWSE_KILLED"


# ══════════════════════════════════════════════════════════════════════════════
# The mirror seam — broadcasts + the expired surfacing
# ══════════════════════════════════════════════════════════════════════════════


class TestTheSeam:
    def test_broadcast_helpers_relay_to_the_state(self):
        st = _FakeState()
        bmirror.broadcast_browse_step({"step_n": 1, "url": PLAIN_URL}, state=st)
        bmirror.broadcast_kill(killswitch.BrowseKillState(active=True, reason="x"), state=st)
        types = [t for t, _ in st.ws]
        assert bmirror.WS_BROWSE_STEP in types and bmirror.WS_BROWSE_KILL in types

    def test_broadcast_is_a_noop_without_a_live_state(self):
        """No gateway up → the relay silently no-ops rather than raising into the loop."""
        bmirror.broadcast_browse_step({"step_n": 1}, state=None)  # must not raise

    def test_surface_auth_expired_raises_banner_and_needs_input(self):
        st = _FakeState()
        bmirror.surface_auth_expired("https://bank.test/x", state=st)
        assert any(t == bmirror.WS_BROWSE_AUTH_EXPIRED for t, _ in st.ws), "no banner broadcast"

        from personalclaw.inbox import InboxStore

        store = InboxStore()
        store.load()
        rows = [i for i in store.items.values() if i.refs.get("browse_auth") == "expired"]
        assert rows, "no needs_input inbox row was raised for the expired site"
        assert rows[0].item_kind == "needs_input"
        assert rows[0].refs.get("site") == "bank.test"

    def test_the_expired_inbox_row_is_deduped_per_site(self):
        """A scheduled watcher re-hits the wall every tick; the row must not stack once per tick."""
        st = _FakeState()
        bmirror.surface_auth_expired("https://bank.test/x", state=st)
        bmirror.surface_auth_expired("https://bank.test/other", state=st)  # same site slug

        from personalclaw.inbox import InboxStore

        store = InboxStore()
        store.load()
        rows = [
            i
            for i in store.items.values()
            if i.refs.get("browse_auth") == "expired" and i.refs.get("site") == "bank.test"
        ]
        assert len(rows) == 1, f"expected one deduped row, got {len(rows)}"


# ══════════════════════════════════════════════════════════════════════════════
# The routes
# ══════════════════════════════════════════════════════════════════════════════


class _FakeReq:
    def __init__(self, body=None, state=None) -> None:
        self._body = body
        self.app = {"state": state} if state is not None else {}

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _body_of(resp) -> dict:
    return json.loads(resp.body)


class TestTheRoutes:
    def test_status_reports_kill_and_expired(self):
        from personalclaw.browse.handoff import mark_expired, record_login

        record_login("https://news.test/home")
        mark_expired("https://news.test/home")
        resp = _run(bm.api_browse_status(_FakeReq()))
        body = _body_of(resp)
        assert body["kill"]["active"] is False
        assert any(e["site"] == "news.test" for e in body["expired"])

    def test_kill_route_engages_and_release_route_disengages(self):
        engaged = _run(bm.api_browse_kill(_FakeReq(body={"reason": "manual"})))
        assert _body_of(engaged)["kill"]["active"] is True
        assert killswitch.browse_killed() is True

        released = _run(bm.api_browse_kill_release(_FakeReq(body={"confirm": True})))
        assert _body_of(released)["kill"]["active"] is False
        assert killswitch.browse_killed() is False

    def test_release_requires_confirmation(self):
        """EXPLICIT release, like incident resume — a stray POST cannot re-enable browse."""
        killswitch.engage("stop")
        resp = _run(bm.api_browse_kill_release(_FakeReq(body={})))
        assert resp.status == 400
        assert killswitch.browse_killed() is True, "an unconfirmed release must NOT re-enable"

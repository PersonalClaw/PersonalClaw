"""A dismissed Discover tip can be brought back (issue 452).

Dismissing was one unconfirmed ``X`` on a card and it was terminal. Measured against
``origin/main``: there is exactly one writer (``dismiss``), it is add-only, the single route
is a ``POST``, and a grep for ``undismiss|un_dismiss|reset_dismissed|clear_dismissed`` over
``src/personalclaw/`` and ``web/src`` returned nothing. The page's own header comment said so
outright — *"an explicit dismiss persists forever"* — which makes the behaviour deliberate,
but Discover is the product's only feature-discovery surface, so a reflex click permanently
removed it with no list, no count of what was hidden, and no way back.

What this file pins, per the ruling on the issue:

* **Clear-all, not per-id.** The user is never shown *which* ids are stored (a hidden-tips
  list is out of scope), so a per-id control would ask them to choose from an invisible set.
* **The control gates on ``restorable_count``, never on ``dismissed_count``.** The two hide
  reasons in ``select_visible`` are independent, so a tip that was dismissed AND whose area
  has since been engaged stays hidden either way. Gating on ``dismissed_count`` ships a
  button that rewrites the settings file and changes nothing the user can see — the ruling
  names this explicitly. ``test_discover.py`` owns the count's arithmetic; this file owns
  the route.
* **The route is reachable.** A writer with no registration is the same bug as no writer.

Still propose-don't-write (§6's soul guardrail): this only un-hides what the user hid. It
enables nothing, configures nothing, and cannot make a tip appear that auto-hid on use.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import legibility as handler
from personalclaw.legibility import discover as dc


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """This store's established fixture (see `test_discover.py`)."""
    monkeypatch.setattr("personalclaw.providers.entity_routes.config_dir", lambda: tmp_path)
    return tmp_path


def _stored(home: Path) -> list[str] | None:
    path = home / "entity_settings" / "legibility.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())["dismissed_discover_tips"]


async def _delete() -> tuple[int, dict]:
    """Drive the real handler, so this measures what a client actually receives."""
    req = make_mocked_request("DELETE", "/api/legibility/discover/dismiss")
    resp = await handler.api_discover_dismiss_clear(req)
    return resp.status, json.loads(resp.text or "{}")


# ── the way back ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_endpoint_clears_every_dismissal(home: Path):
    """🔑 The defect itself: before this route, this assertion was unwritable."""
    dc.dismiss("chat")
    dc.dismiss("tasks")
    assert _stored(home) == ["chat", "tasks"]

    status, body = await _delete()
    assert status == 200
    assert body == {"ok": True, "restored": 2}
    assert _stored(home) == [], "the dismissals are gone from the user's disk"
    assert dc.load_dismissed() == set()


@pytest.mark.asyncio
async def test_a_cleared_tip_is_served_again(home: Path, monkeypatch: pytest.MonkeyPatch):
    """The round trip that matters to the user: hide it, restore it, see it in the payload."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "personalclaw.config.loader.AppConfig.load",
        classmethod(lambda cls: SimpleNamespace(legibility=SimpleNamespace(discover_tips=True))),
    )
    monkeypatch.setattr(dc, "compute_engaged", lambda state=None: {})

    dc.dismiss("chat")
    assert "chat" not in [t["id"] for g in dc.compute_discover()["areas"] for t in g["tips"]]

    await _delete()
    assert "chat" in [t["id"] for g in dc.compute_discover()["areas"] for t in g["tips"]]


@pytest.mark.asyncio
async def test_clearing_nothing_is_a_200_not_an_error(home: Path):
    """The button's gate reads a payload that can be a click stale, so this must not 400."""
    status, body = await _delete()
    assert status == 200
    assert body == {"ok": True, "restored": 0}
    assert _stored(home) is None, "a no-op must not create the settings file"


# ── reachability ─────────────────────────────────────────────────────────────────────────


def test_the_delete_route_is_registered():
    """A handler nothing routes to is as inert as no handler at all.

    Read off the registration function's own source rather than by building the app: the
    ``aiohttp`` app factory pulls in the whole gateway, and the fact under test is one line
    of wiring. Asserted on the SAME path as the POST, which is the design — one resource,
    two verbs — so a future split into a ``/restore`` noun has to come through here.
    """
    from personalclaw.dashboard import server

    src = inspect.getsource(server)
    assert 'add_post("/api/legibility/discover/dismiss"' in src, "POST anchor moved"
    assert (
        'add_delete("/api/legibility/discover/dismiss", api_discover_dismiss_clear)' in src
    ), "the clear handler exists but nothing routes to it"

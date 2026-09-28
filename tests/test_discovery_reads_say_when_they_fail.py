"""A read that looks for things (servers to import, packs to suggest) says when it failed to look.

An empty list is an answer: "there is nothing to import", "no pack matches". A read that could not
look cannot give that answer, and two of these reads gave it anyway:

* ``GET /api/mcp/importable`` caught every failure and answered 200 ``{"servers": []}``, so the
  Tools page read a failed look through the other tools' settings as "nothing to import". It now
  answers ``mcp_importable_failed`` with the exception's own words, screened as the onboarding scan
  of the same files screens them.
* ``GET /api/packs/proposals`` logged a project whose scan failed and went on, so a scan that failed
  for every project answered the same empty list as one that matched nothing. It still shows the
  other projects' cards, and now names each project it could not scan.

The routing and learning review queues and the onboarding network scan are pinned beside their own
routes' tests; the skill search beside the other empty states it must tell apart.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import make_mocked_request


def _body(resp) -> dict:
    return json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_a_failed_import_lookup_is_a_failure_not_an_empty_list(monkeypatch, tmp_path):
    from personalclaw import mcp_discovery
    from personalclaw.dashboard.handlers.mcp import api_mcp_importable

    config = tmp_path / ".claude.json"

    def unreadable():
        raise PermissionError(13, "Permission denied", str(config))

    monkeypatch.setattr(mcp_discovery, "discover_importable_servers", unreadable)
    resp = await api_mcp_importable(make_mocked_request("GET", "/api/mcp/importable"))
    assert resp.status == 500
    assert _body(resp) == {
        "error": {
            "code": "mcp_importable_failed",
            "message": (
                "Couldn't look through your other tools' MCP settings for servers to import: "
                f"[Errno 13] Permission denied: '{config}'"
            ),
        }
    }


@pytest.mark.asyncio
async def test_a_lookup_that_worked_still_answers_its_list(monkeypatch):
    """The control: only a failure changed, so an empty look is still `{"servers": []}`."""
    from personalclaw import mcp_discovery
    from personalclaw.dashboard.handlers.mcp import api_mcp_importable

    monkeypatch.setattr(mcp_discovery, "discover_importable_servers", lambda: [])
    resp = await api_mcp_importable(make_mocked_request("GET", "/api/mcp/importable"))
    assert resp.status == 200 and _body(resp) == {"servers": []}


@pytest.mark.asyncio
async def test_a_project_that_could_not_be_scanned_is_named(monkeypatch):
    from personalclaw.dashboard.handlers.packs import api_pack_proposals
    from personalclaw.packs import fingerprint
    from personalclaw.tasks import hierarchy

    ledger = SimpleNamespace(id="p-ledger", name="Ledger")
    garden = SimpleNamespace(id="p-garden", name="Garden")

    class _Store:
        def list_projects(self):
            return [ledger, garden]

    def scan(project, *, reason):
        if project is ledger:
            raise PermissionError(13, "Permission denied", "/srv/ledger")
        return []

    monkeypatch.setattr(hierarchy, "HierarchyStore", _Store)
    monkeypatch.setattr(fingerprint, "scan_project", scan)
    resp = await api_pack_proposals(make_mocked_request("GET", "/api/packs/proposals"))
    assert resp.status == 200
    assert _body(resp) == {
        "proposals": [],
        "unscanned": [
            {
                "project_id": "p-ledger",
                "project": "Ledger",
                "reason": "[Errno 13] Permission denied: '/srv/ledger'",
            }
        ],
    }


@pytest.mark.asyncio
async def test_every_project_scanned_names_none(monkeypatch):
    """The control: a scan that reached every project and matched nothing is the empty answer."""
    from personalclaw.dashboard.handlers.packs import api_pack_proposals
    from personalclaw.packs import fingerprint
    from personalclaw.tasks import hierarchy

    class _Store:
        def list_projects(self):
            return [SimpleNamespace(id="p-garden", name="Garden")]

    monkeypatch.setattr(hierarchy, "HierarchyStore", _Store)
    monkeypatch.setattr(fingerprint, "scan_project", lambda project, *, reason: [])
    resp = await api_pack_proposals(make_mocked_request("GET", "/api/packs/proposals"))
    assert _body(resp) == {"proposals": [], "unscanned": []}

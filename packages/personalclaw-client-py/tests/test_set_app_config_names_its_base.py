"""An app's settings are written over the copy they were built from.

``PUT /api/apps/{name}/config`` replaces the whole file, so the Gateway refuses a write that does
not name the revision it replaces (``428``) and one whose revision is stale (``409``). The client
sends the revision its caller read — never one it fetches itself just before the write, which
would name whatever is stored now and overwrite it.
"""

from __future__ import annotations

import pytest
from personalclaw_client import PersonalClawClient, PersonalClawError
from personalclaw_client.errors import http_error


def _client() -> PersonalClawClient:
    # An explicit token, so constructing the client never reads an app secret from a home on disk.
    return PersonalClawClient(app_name="notes", token="t")


def _gateway(pc: PersonalClawClient, answer):
    calls = []

    async def fake_request(method, path, body=None, headers=None):
        calls.append((method, path, body, headers or {}))
        return answer(method)

    pc._request = fake_request
    return calls


@pytest.mark.asyncio
async def test_the_write_names_the_revision_its_caller_read():
    pc = _client()
    saved = {
        "ok": True,
        "name": "notes",
        "config": {"folder": "b"},
        "_secret_set": [],
        "revision": "r2",
    }
    calls = _gateway(pc, lambda method: saved)
    assert await pc.set_app_config({"folder": "b"}, revision="r1") == saved
    # One request: the base is the caller's, not a fresh read that would name any change since.
    assert calls == [("PUT", "/api/apps/notes/config", {"folder": "b"}, {"If-Match": '"r1"'})]


@pytest.mark.asyncio
async def test_a_stale_revision_is_the_gateways_refusal_not_a_save():
    pc = _client()

    def refuse(method):
        raise http_error(
            409, {"error": "This write replaces the app 'notes''s settings…", "code": "stale_write"}
        )

    calls = _gateway(pc, refuse)
    with pytest.raises(PersonalClawError) as exc_info:
        await pc.set_app_config({"folder": "b"}, revision="r0")
    assert exc_info.value.status == 409
    assert [c[0] for c in calls] == ["PUT"]


def test_a_write_cannot_be_issued_without_a_base():
    # Required, with no default: a caller that never read the settings has no revision to name.
    with pytest.raises(TypeError):
        _client().set_app_config({"folder": "b"})  # type: ignore[call-arg]

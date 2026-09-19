"""#2983 at the bulk door: ``POST /api/tasks/bulk`` never resolved ``provider``.

#2983 closed the silent-misroute at eight single-item doors — a call scoped to a provider
that does not exist must not act on the NATIVE record and answer success. Bulk was not one of
the eight, and it is the door where the defect is cheapest to exploit: phase 1 validated
author, parent, title and id, and phase 2 then dropped the key outright
(``{k: v for k, v in item.items() if k != "provider"}``), so ``{"provider": "jira"}`` on a
bulk create was accepted, written to native, and reported ``created``.

Four merged PRs cite #2983. All four are the eight-door fix; none touches this path — so the
citations are not evidence it was closed, which is why this is measured here rather than
assumed.

The rule is enforced in PHASE 1 on purpose. A bulk endpoint that half-applies is worse than
one that refuses: the caller gets a 400 listing per-item errors and cannot tell which of the
sound rows also landed. So the assertions below are always a pair — the batch is refused AND
the store is still empty.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.tasks import registry
from personalclaw.tasks.handlers import register_task_routes


@asynccontextmanager
async def _client(tmp_path):
    registry._providers.clear()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=tmp_path),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path),
    ):
        app = web.Application()
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


async def _stored(limit: int = 10_000):
    tasks, _ = await registry.list_all_tasks(limit=limit)
    return tasks


class TestBulkCreateResolvesProvider:
    @pytest.mark.asyncio
    async def test_an_unknown_provider_refuses_the_whole_batch(self, tmp_path):
        async with _client(tmp_path) as client:
            r = await client.post(
                "/api/tasks/bulk",
                json={"op": "create", "items": [{"title": "x", "provider": "jira"}]},
            )
            assert r.status == 400
            body = await r.json()
            # The refusal NAMES the provider, as `_unknown_provider` does at the single-item
            # doors — a bare 400 would not tell the caller which value was wrong.
            assert "jira" in body["errors"][0]["error"], body
            assert body["succeeded"] == 0
            # And nothing landed on native under a name the caller never addressed.
            assert await _stored() == []

    @pytest.mark.asyncio
    async def test_one_bad_provider_aborts_the_SOUND_items_too(self, tmp_path):
        """The validate-all contract. This is the assertion that separates "refuses" from
        "half-applies": the first item is perfectly valid and must still not be written."""
        async with _client(tmp_path) as client:
            r = await client.post(
                "/api/tasks/bulk",
                json={
                    "op": "create",
                    "items": [
                        {"title": "sound"},
                        {"title": "bad", "provider": "nope"},
                    ],
                },
            )
            assert r.status == 400
            assert await _stored() == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", [{"a": "b"}, ["x"], 5, True])
    async def test_a_non_string_provider_is_refused(self, bad):
        """``str()`` coercion would have turned each of these into a lookup for a provider
        named after a Python repr, so the type is refused before the lookup."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            async with _client(tmp) as client:
                r = await client.post(
                    "/api/tasks/bulk",
                    json={"op": "create", "items": [{"title": "x", "provider": bad}]},
                )
                assert r.status == 400
                assert "provider" in (await r.json())["errors"][0]["error"]
                assert await _stored() == []

    @pytest.mark.asyncio
    async def test_the_native_default_still_creates(self, tmp_path):
        """The refusal must not cost the feature: an ABSENT provider is the default (native),
        never a wildcard, and an explicit "native" resolves the same way.

        This is also the regression the lazy-registration hazard would cause — validating
        membership without the registry's own ``_ensure_native`` would refuse ``native`` on a
        first request, before it is registered."""
        async with _client(tmp_path) as client:
            r = await client.post(
                "/api/tasks/bulk",
                json={
                    "op": "create",
                    "items": [{"title": "implicit"}, {"title": "explicit", "provider": "native"}],
                },
            )
            assert r.status == 200, await r.text()
            assert (await r.json())["succeeded"] == 2
            assert sorted(t.title for t in await _stored()) == ["explicit", "implicit"]


class TestBulkUpdateResolvesProvider:
    @pytest.mark.asyncio
    async def test_an_unknown_provider_refuses_the_batch_and_changes_nothing(self, tmp_path):
        async with _client(tmp_path) as client:
            made = await (await client.post("/api/tasks", json={"title": "original"})).json()
            r = await client.post(
                "/api/tasks/bulk",
                json={
                    "op": "update",
                    "items": [{"id": made["id"], "title": "rewritten", "provider": "jira"}],
                },
            )
            assert r.status == 400
            assert "jira" in (await r.json())["errors"][0]["error"]
            # The row keeps its original title — the update did not half-apply.
            assert [t.title for t in await _stored()] == ["original"]


class TestBulkDeleteResolvesProvider:
    """#2983's HEADLINE, and the sharpest case in the family.

    The issue's title is this exact call: *"bulk delete of `{"provider":"jira"}` returns 200 and
    destroys the native task, while the single-item DELETE 400s"*. It is the worst of the three
    verbs for two reasons. Bulk delete never passes ``provider`` to the registry at all
    (``registry.delete_task(str(tid))``, no ``provider_name``), so unlike create/update there is
    nothing downstream that could have caught the name; and the act is irreversible, so reading
    an unrecognized scope as "every scope" destroys the record rather than mislabelling it.
    """

    @pytest.mark.asyncio
    async def test_an_unknown_provider_does_not_destroy_the_native_task(self, tmp_path):
        async with _client(tmp_path) as client:
            made = await (await client.post("/api/tasks", json={"title": "victim"})).json()
            r = await client.post(
                "/api/tasks/bulk",
                json={"op": "delete", "items": [{"id": made["id"], "provider": "jira"}]},
            )
            # Measured before the fix: 200 with `succeeded: 1`, and the task was GONE.
            assert r.status == 400, await r.text()
            body = await r.json()
            assert body["succeeded"] == 0
            assert "jira" in body["errors"][0]["error"]
            assert [t.title for t in await _stored()] == ["victim"]

    @pytest.mark.asyncio
    async def test_bulk_delete_now_AGREES_with_the_single_item_door(self, tmp_path):
        """The disagreement IS the bug: one door refused the name and the other obeyed it.

        Asserted as parity rather than as two independent statuses, because a later change that
        relaxes either door alone should fail here.
        """
        async with _client(tmp_path) as client:
            made = await (await client.post("/api/tasks", json={"title": "victim"})).json()
            single = await client.delete(f"/api/tasks/{made['id']}?provider=jira")
            bulk = await client.post(
                "/api/tasks/bulk",
                json={"op": "delete", "items": [{"id": made["id"], "provider": "jira"}]},
            )
            assert single.status == bulk.status == 400
            # Neither door touched it.
            assert [t.title for t in await _stored()] == ["victim"]

    @pytest.mark.asyncio
    async def test_a_real_delete_still_deletes(self, tmp_path):
        """The refusal must not cost the verb — including the bare-id item shape, which is not
        a dict and therefore carries no provider to resolve."""
        async with _client(tmp_path) as client:
            a = await (await client.post("/api/tasks", json={"title": "by-dict"})).json()
            b = await (await client.post("/api/tasks", json={"title": "by-bare-id"})).json()
            r = await client.post(
                "/api/tasks/bulk",
                json={"op": "delete", "items": [{"id": a["id"], "provider": "native"}, b["id"]]},
            )
            assert r.status == 200, await r.text()
            assert (await r.json())["succeeded"] == 2
            assert await _stored() == []


def test_the_premise_holds():
    """Phase 2 still strips ``provider``, which is WHY phase 1 has to resolve it.

    If a later change threads the resolved provider through to the apply phase, this assertion
    is the one that should be revisited deliberately rather than silently.
    """
    import inspect

    from personalclaw.tasks import handlers

    src = inspect.getsource(handlers.api_tasks_bulk)
    assert 'if k != "provider"' in src
    assert '"id", "provider"' in src

"""Rails for the two task-route parameters that were accepted but not honored.

Both defects are the same shape: `/api/tasks` read a parameter, failed to validate it,
and answered `200` describing a scope it had not applied — so a wrong-but-plausible value
came back as a quietly wrong answer instead of a refusal.

- #2984: `limit`/`offset` went straight into a Python slice with no lower clamp, so
  ``?limit=-1`` returned a silently SHORT page (5 of 6) while ``total`` still said 6, and
  the ``task_list`` tool advertised an unconstrained ``integer``. Every sibling paginated
  route in the gateway clamps with ``max(1, min(...))``; this one did not. ``limit=0`` also
  meant two different things — "none" over HTTP, "all" in the tool, because of an
  ``or 25`` default.

- #2983: an unknown ``?provider=`` meant "every provider" on all seven read/write doors,
  so ``DELETE /api/tasks/{id}?provider=jira`` deleted the NATIVE task and answered
  ``{"ok": true}``. ``POST /api/tasks`` with the same name already refused it, so the
  create door validated and the other seven did not. The registry conflated "no provider
  given" (legitimately: search them all) with "a name I do not recognize" (only ever a
  client error). The artifacts registry — same pluggable-provider design — already
  resolves it the refusing way, so this is the shape being made consistent, not invented.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.tasks import registry
from personalclaw.tasks.handlers import register_task_routes


@asynccontextmanager
async def _client(tmp_path):
    """A client over the task routes with isolated stores and ONLY `native` registered.

    No `request_boundary_middleware` here, deliberately: a route must produce its own wire
    envelope for a parameter it read, rather than depending on a global gate to convert an
    unhandled `ValueError` for it.
    """
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


async def _seed(client, n: int) -> list[str]:
    ids = []
    for i in range(n):
        r = await client.post("/api/tasks", json={"title": f"task-{i}"})
        assert r.status == 201
        ids.append((await r.json())["id"])
    return ids


# ── #2984: limit/offset are clamped, and the response describes the page it returned ──


class TestListPaginationIsClamped:
    @pytest.mark.asyncio
    async def test_negative_limit_does_not_return_a_short_page(self, tmp_path):
        # Measured on main: 200 with 5 of 6 tasks and `limit: -1` echoed back — the far-end
        # drop of `all_tasks[0:-1]`. The page must never be shorter than the limit it reports.
        async with _client(tmp_path) as client:
            await _seed(client, 6)
            body = await (await client.get("/api/tasks?limit=-1")).json()
            assert body["total"] == 6
            assert body["limit"] == 1
            assert len(body["tasks"]) == 1

    @pytest.mark.asyncio
    async def test_zero_limit_clamps_to_one_page_item(self, tmp_path):
        # `limit=0` meant "none" here and "all" in the tool. One meaning, both surfaces.
        async with _client(tmp_path) as client:
            await _seed(client, 6)
            body = await (await client.get("/api/tasks?limit=0")).json()
            assert body["limit"] == 1
            assert len(body["tasks"]) == 1

    @pytest.mark.asyncio
    async def test_negative_offset_does_not_empty_the_page(self, tmp_path):
        # Measured on main: `?offset=-2&limit=2` is `all_tasks[-2:0]` → an empty page, 200.
        async with _client(tmp_path) as client:
            await _seed(client, 6)
            body = await (await client.get("/api/tasks?offset=-2&limit=2")).json()
            assert body["offset"] == 0
            assert len(body["tasks"]) == 2

    @pytest.mark.asyncio
    async def test_oversized_limit_is_capped_and_reported_as_capped(self, tmp_path):
        async with _client(tmp_path) as client:
            await _seed(client, 3)
            body = await (await client.get("/api/tasks?limit=999999")).json()
            assert body["limit"] == 500
            assert len(body["tasks"]) == 3

    @pytest.mark.asyncio
    async def test_a_valid_limit_is_unchanged(self, tmp_path):
        async with _client(tmp_path) as client:
            await _seed(client, 6)
            body = await (await client.get("/api/tasks?limit=2&offset=1")).json()
            assert (body["limit"], body["offset"], body["total"]) == (2, 1, 6)
            assert len(body["tasks"]) == 2

    @pytest.mark.asyncio
    async def test_non_numeric_limit_is_the_routes_own_wire_envelope(self, tmp_path):
        # Without the handler's own guard this is an unhandled ValueError — a bare 500 in any
        # app that has not installed the global request-shape middleware.
        async with _client(tmp_path) as client:
            r = await client.get("/api/tasks?limit=abc")
            assert r.status == 400
            assert (await r.json())["error"]["code"] == "bad_request"

    @pytest.mark.asyncio
    async def test_search_route_clamps_the_same_way(self, tmp_path):
        # The same slice, on the sibling POST door of the same handler module.
        async with _client(tmp_path) as client:
            await _seed(client, 6)
            r = await client.post("/api/tasks/search", json={"limit": -1})
            body = await r.json()
            assert body["total"] == 6
            assert len(body["tasks"]) == 1


class TestTaskListToolLimitIsBounded:
    @pytest.fixture
    def tool_provider(self, tmp_path):
        registry._providers.clear()
        ws = tmp_path / "ws"
        ws.mkdir()
        with (
            patch("personalclaw.tasks.native.config_dir", return_value=tmp_path / "home"),
            patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path / "home"),
        ):
            yield NativeBuiltinToolProvider(ws)
        registry._providers.clear()

    @staticmethod
    def _shown(output: str) -> int:
        return sum(1 for line in output.splitlines() if line.startswith("- "))

    @pytest.mark.asyncio
    async def test_negative_limit_does_not_silently_drop_rows(self, tool_provider):
        # Measured on main: `limit: -1` → "6 task(s), showing 5:" and five rows. `-1` is the
        # common "give me everything" idiom, so this is the sharpest form of #2984.
        for i in range(6):
            await tool_provider.invoke("task_create", {"title": f"t{i}"})
        r = await tool_provider.invoke("task_list", {"limit": -1})
        assert r.success
        assert self._shown(r.output) == 1

    @pytest.mark.asyncio
    async def test_zero_limit_means_the_same_here_as_over_http(self, tool_provider):
        # `int(a.get("limit", 25) or 25)` turned 0 into 25, so `limit: 0` meant "all" in the
        # tool and "none" over HTTP. Same value, one meaning.
        for i in range(6):
            await tool_provider.invoke("task_create", {"title": f"t{i}"})
        r = await tool_provider.invoke("task_list", {"limit": 0})
        assert r.success
        assert self._shown(r.output) == 1

    @pytest.mark.asyncio
    async def test_the_schema_advertises_the_bounds(self, tool_provider):
        # A model reads the schema, not the handler. An unconstrained `integer` is what
        # invited `limit: -1` in the first place.
        defn = next(t for t in await tool_provider.list_tools() if t.name == "task_list")
        limit = defn.parameters["properties"]["limit"]
        assert limit["minimum"] == 1
        # Read from the module that owns the ceiling, so the advertised bound cannot drift
        # from the one the handler clamps to.
        assert limit["maximum"] == registry.MAX_TASK_PAGE


# ── #2983: an unknown provider name is a refusal, not "every provider" ──


class TestUnknownProviderIsRefused:
    @staticmethod
    async def _assert_refused(resp, name: str = "jira") -> None:
        assert resp.status == 400
        payload = (await resp.json())["error"]
        assert payload["code"] == "bad_request"
        assert name in payload["message"]

    @pytest.mark.asyncio
    async def test_list_does_not_widen_to_every_provider(self, tmp_path):
        # Measured on main: 200 with all 3 native tasks for `?provider=jira`. Every sibling
        # filter on this route narrows to nothing on an unknown value; this one widened.
        async with _client(tmp_path) as client:
            await _seed(client, 3)
            await self._assert_refused(await client.get("/api/tasks?provider=jira"))

    @pytest.mark.asyncio
    async def test_get_does_not_answer_from_another_provider(self, tmp_path):
        async with _client(tmp_path) as client:
            (tid,) = await _seed(client, 1)
            await self._assert_refused(await client.get(f"/api/tasks/{tid}?provider=jira"))

    @pytest.mark.asyncio
    async def test_update_does_not_write_to_the_native_task(self, tmp_path):
        async with _client(tmp_path) as client:
            (tid,) = await _seed(client, 1)
            await self._assert_refused(
                await client.put(f"/api/tasks/{tid}", json={"provider": "jira", "title": "renamed"})
            )
            # The refusal is total: the native row keeps its title.
            assert (await (await client.get(f"/api/tasks/{tid}")).json())["title"] == "task-0"

    @pytest.mark.asyncio
    async def test_delete_does_not_destroy_the_native_task(self, tmp_path):
        # The sharpest form: on main this answered `{"ok": true}` and the task was gone.
        async with _client(tmp_path) as client:
            (tid,) = await _seed(client, 1)
            await self._assert_refused(await client.delete(f"/api/tasks/{tid}?provider=jira"))
            assert (await client.get(f"/api/tasks/{tid}")).status == 200

    @pytest.mark.asyncio
    async def test_comment_read_is_refused(self, tmp_path):
        async with _client(tmp_path) as client:
            (tid,) = await _seed(client, 1)
            await self._assert_refused(await client.get(f"/api/tasks/{tid}/comments?provider=jira"))

    @pytest.mark.asyncio
    async def test_comment_create_is_refused_and_adds_nothing(self, tmp_path):
        async with _client(tmp_path) as client:
            (tid,) = await _seed(client, 1)
            await self._assert_refused(
                await client.post(
                    f"/api/tasks/{tid}/comments", json={"provider": "jira", "body": "hi"}
                )
            )
            listed = await (await client.get(f"/api/tasks/{tid}/comments")).json()
            assert listed["comments"] == []

    @pytest.mark.asyncio
    async def test_comment_delete_is_refused_and_the_comment_survives(self, tmp_path):
        async with _client(tmp_path) as client:
            (tid,) = await _seed(client, 1)
            made = await (
                await client.post(f"/api/tasks/{tid}/comments", json={"body": "keep me"})
            ).json()
            await self._assert_refused(
                await client.delete(f"/api/tasks/{tid}/comments/{made['id']}?provider=jira")
            )
            listed = await (await client.get(f"/api/tasks/{tid}/comments")).json()
            assert [c["id"] for c in listed["comments"]] == [made["id"]]

    @pytest.mark.asyncio
    async def test_create_keeps_refusing_and_now_says_it_the_same_way(self, tmp_path):
        # This door already refused; it is the reference behavior the other seven adopt, so
        # it is pinned here — including the envelope, so the eight cannot drift apart again.
        async with _client(tmp_path) as client:
            await self._assert_refused(
                await client.post("/api/tasks", json={"title": "x", "provider": "jira"})
            )

    @pytest.mark.asyncio
    async def test_a_known_provider_and_an_absent_one_both_still_work(self, tmp_path):
        # The refusal must not swallow the two legitimate inputs: a registered name scopes,
        # and no name at all still means "search them all".
        async with _client(tmp_path) as client:
            (tid,) = await _seed(client, 1)
            assert (await client.get("/api/tasks?provider=native")).status == 200
            assert (await client.get(f"/api/tasks/{tid}?provider=native")).status == 200
            assert (await client.get("/api/tasks")).status == 200
            assert (await client.get(f"/api/tasks/{tid}")).status == 200

    @pytest.mark.asyncio
    async def test_an_empty_provider_value_still_means_no_scope(self, tmp_path):
        # `?provider=` is an unset parameter, not a name the registry failed to recognize.
        async with _client(tmp_path) as client:
            await _seed(client, 2)
            body = await (await client.get("/api/tasks?provider=")).json()
            assert body["total"] == 2

    @pytest.mark.asyncio
    async def test_the_graph_routes_documented_fallback_is_left_alone(self, tmp_path):
        # `task_graph` falls back to native DELIBERATELY, with a docstring saying why (only
        # the native provider owns a mutable DAG). Pinned so the refusal above cannot creep
        # into the one door whose fallback is intended.
        async with _client(tmp_path) as client:
            assert (await client.get("/api/tasks/graph?provider=jira")).status == 200

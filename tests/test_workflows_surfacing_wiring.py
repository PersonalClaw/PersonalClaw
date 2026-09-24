"""The wiring that makes the surfacing mechanisms reachable (TASKS-SOPS §2/§5/§7 — S61b).

S55-S61 built decision modules and gave the def somewhere to declare its surfacing. Nothing CALLED
any of it. Three gaps were measured here, each of which made a shipped mechanism unreachable:

**`author_def` had no `metadata` parameter.** So every `DefMetadata` field — including the
`surface_mode`, `cadence_days` and `packs` the channels read — could be loaded from disk and never
SET through the API. A field with a read path and no write path is a field only a hand-edited file
can use, which is precisely what the config round-trip contract exists to prevent.

**`list_defs` drops `metadata` entirely.** Its projection is name/description/source/version/tags/
provider, so a templates list built on it cannot render a freshness gradient, a surfacing toggle or
a pack chip no matter what a def declares. The fields would be on disk and invisible to every
surface.

**`TaskComplete` was never fired.** Declared in `hooks.HOOK_EVENTS`, allowlisted in
`validation.ALLOWED_HOOK_EVENTS`, rendered by the hook UI — and no call site in the repo emitted it,
so a user could configure "when a task finishes" and get nothing. Now fired from
`NativeTaskProvider.update_task`, edge-triggered.
"""

import asyncio

import pytest

from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import service

ROOT = {
    "kind": "sequence",
    "id": "s",
    "children": [
        {"kind": "action", "id": "a", "config": {"provider": "bash", "with": {"command": "true"}}}
    ],
}
NOW = 1_700_000_000.0
DAY = 86400.0


class _MemProvider(defs_mod.WorkflowDefProvider):
    """A writable in-memory def provider, matching `test_workflows_api.py`'s pattern."""

    def __init__(self) -> None:
        self._defs: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return "wiring-mem"

    @property
    def readonly(self) -> bool:
        return False

    async def list_defs(self, *, limit: int = 200, offset: int = 0):
        items = list(self._defs.values())
        return items[offset : offset + limit], len(items)

    async def get_def(self, name: str):
        return self._defs.get(name)

    async def save_def(self, **fields):
        fields.setdefault("version", 1)
        fields.setdefault("source", "user")
        self._defs[fields["name"]] = dict(fields)
        return self._defs[fields["name"]]

    async def delete_def(self, name: str) -> bool:
        return self._defs.pop(name, None) is not None


@pytest.fixture
def provider():
    p = _MemProvider()
    defs_mod.register_provider(p)
    yield p
    defs_mod.unregister_provider("wiring-mem")


async def _author(name: str, metadata: dict) -> dict:
    return await service.author_def(name=name, root=ROOT, metadata=metadata, strict=False)


async def _rows(*, now: float = NOW) -> tuple[list[dict], dict]:
    """Surfacing rows from THIS test's provider only, plus the whole response.

    The def registry is process-global and a full run has other providers registered (bundled
    templates among them). Measured: asserting on `defs[0]` or on an empty list passed in isolation
    and failed in the xdist mix — the classic "test that only holds when it runs alone".
    """
    out = await service.list_defs_surfacing(now=now)
    return [r for r in out["defs"] if r.get("provider") == "wiring-mem"], out


# ── the metadata WRITE path ──


def test_surfacing_metadata_can_be_SET_through_the_API(provider):
    """The measured gap: `author_def` had no `metadata` parameter at all, so a def's surfacing
    configuration was unreachable except by hand-editing a file on disk."""
    result = asyncio.run(_author("backup", {"surface_mode": "passive", "cadence_days": 7}))
    assert result["ok"] is True
    saved = provider._defs["backup"]["metadata"]
    assert saved["surface_mode"] == "passive"
    assert saved["cadence_days"] == 7


def test_the_write_path_COERCES_like_the_read_path(provider):
    """Coercing on read alone would store a value the next reader silently reinterprets — the def
    file would say `vibes` while every surface showed `off`, and nobody could explain the gap."""
    asyncio.run(_author("typo", {"surface_mode": "vibes", "cadence_days": -3}))
    saved = provider._defs["typo"]["metadata"]
    assert saved["surface_mode"] == "off"
    assert saved["cadence_days"] == 0


def test_authoring_WITHOUT_metadata_still_works(provider):
    """The parameter is optional: a caller that never passed metadata must not now fail."""
    assert asyncio.run(service.author_def(name="plain", root=ROOT, strict=False))["ok"] is True


def test_every_declared_field_survives_the_write(provider):
    asyncio.run(
        _author(
            "full",
            {
                "surface_mode": "suggest",
                "agent_digest": "d",
                "summary": "s",
                "when_to_use": "w",
                "cadence_days": 30,
                "escalation": "auto",
                "packs": ["ci"],
                "hands_off_to": [{"target_def": "bug-fix"}],
                "guided": True,
            },
        )
    )
    saved = provider._defs["full"]["metadata"]
    assert saved["escalation"] == "auto"
    assert saved["packs"] == ["ci"]
    assert saved["guided"] is True
    assert saved["hands_off_to"][0]["target_def"] == "bug-fix"


# ── the surfacing READ projection ──


def test_the_thin_list_still_DROPS_metadata(provider):
    """Pinning the measured fact that motivated a second route: `list_defs` returns a deliberately
    thin projection, so it cannot be what the templates UX reads."""
    asyncio.run(_author("backup", {"surface_mode": "passive", "cadence_days": 7}))
    row = asyncio.run(service.list_defs())["defs"][0]
    assert "surface_mode" not in row
    assert "cadence_days" not in row


def test_the_surfacing_list_EXPOSES_the_fields(provider):
    asyncio.run(_author("backup", {"surface_mode": "passive", "cadence_days": 7, "packs": ["ci"]}))
    row = asyncio.run(_rows())[0][0]
    assert row["surface_mode"] == "passive"
    assert row["cadence_days"] == 7
    assert row["packs"] == ["ci"]


def test_the_surfacing_list_computes_FRESHNESS(provider):
    """A never-run def is its own band, not "infinitely overdue" — a checklist authored yesterday
    has not failed to run."""
    asyncio.run(_author("backup", {"surface_mode": "passive", "cadence_days": 7}))
    row = asyncio.run(_rows())[0][0]
    assert row["freshness"] == "never_run"
    assert row["overdue"] is True


def test_an_UNTRACKED_def_is_fresh(provider):
    asyncio.run(_author("adhoc", {"surface_mode": "passive"}))
    row = asyncio.run(_rows())[0][0]
    assert row["freshness"] == "fresh"
    assert row["overdue"] is False


def test_the_surfacing_list_carries_the_DOCTOR_findings(provider):
    """The reachability doctor is only useful if a surface can show it; a check nobody renders is a
    check nobody runs."""
    asyncio.run(_author("ghost", {"surface_mode": "passive"}))
    out = asyncio.run(service.list_defs_surfacing(now=NOW))
    assert [f["code"] for f in out["findings"]] == ["no_channel"]


def test_a_reachable_def_produces_NO_findings(provider):
    asyncio.run(_author("backup", {"surface_mode": "passive", "match_text": "back up"}))
    assert asyncio.run(service.list_defs_surfacing(now=NOW))["findings"] == []


def test_the_list_sorts_OVERDUE_first(provider):
    """One rule for the order: `sort_key` owns it, so the API and any other surface cannot disagree
    about which template is at the top."""
    asyncio.run(_author("fresh-one", {"surface_mode": "passive"}))
    asyncio.run(_author("overdue-one", {"surface_mode": "passive", "cadence_days": 7}))
    rows, _out = asyncio.run(_rows())
    assert [r["name"] for r in rows] == ["overdue-one", "fresh-one"]


def test_the_list_exposes_the_HANDOFF_edges(provider):
    asyncio.run(
        _author(
            "incident",
            {
                "surface_mode": "passive",
                "match_text": "incident",
                "hands_off_to": [{"target_def": "bug-fix", "context_fields": ["id"]}],
            },
        )
    )
    rows, _out = asyncio.run(_rows())
    assert rows[0]["hands_off_to"][0]["target_def"] == "bug-fix"


def test_an_edge_pointing_NOWHERE_is_not_rendered(provider):
    """A suggestion the user cannot accept is a dead affordance."""
    asyncio.run(
        _author("bad", {"surface_mode": "passive", "match_text": "x", "hands_off_to": [{}]})
    )
    assert asyncio.run(_rows())[0][0]["hands_off_to"] == []


def test_a_provider_with_NO_defs_contributes_nothing(provider):
    """`ok` regardless, and this provider contributes no rows — scoped, because other providers may
    legitimately have templates registered."""
    rows, out = asyncio.run(_rows())
    assert out["ok"] is True
    assert rows == []


# ── the route is registered, and BEFORE the wildcard ──


def test_the_surfacing_route_is_MOUNTED():
    from aiohttp import web

    from personalclaw.workflows.handlers import register_workflow_routes

    app = web.Application()
    register_workflow_routes(app)
    paths = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/api/workflows/surfacing" in paths


def test_the_literal_route_precedes_the_NAME_wildcard():
    """aiohttp matches in registration order: registered after `/{name}`, a GET for
    `/api/workflows/surfacing` would look for a definition named "surfacing" and 404 — the exact
    hazard `register_workflow_routes`' own docstring records for `/runs`."""
    from aiohttp import web

    from personalclaw.workflows.handlers import register_workflow_routes

    app = web.Application()
    register_workflow_routes(app)
    order = [r.resource.canonical for r in app.router.routes() if r.resource]
    assert order.index("/api/workflows/surfacing") < order.index("/api/workflows/{name}")


# ── the TaskComplete emission ──


def _fake_store(fired: list) -> object:
    class _Store:
        async def fire(self, event, context=""):
            fired.append((event, context))
            return []

    return _Store()


def test_completing_a_task_FIRES_the_lifecycle_hook(tmp_path, monkeypatch):
    """The measured gap: `TaskComplete` is declared, allowlisted and rendered by the hook UI, and no
    call site in the repo fired it — so a configured "when a task finishes" hook never ran."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    fired: list = []
    monkeypatch.setattr("personalclaw.hooks.get_global_hook_store", lambda: _fake_store(fired))

    from personalclaw.tasks.registry import create_task, update_task

    async def go():
        task = await create_task("native", title="Ship it")
        await update_task(task.id, provider_name="native", status="done")

    asyncio.run(go())
    assert [e for e, _c in fired] == ["TaskComplete"]
    assert "status=done" in fired[0][1]


def test_a_NON_completion_edit_fires_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    fired: list = []
    monkeypatch.setattr("personalclaw.hooks.get_global_hook_store", lambda: _fake_store(fired))

    from personalclaw.tasks.registry import create_task, update_task

    async def go():
        task = await create_task("native", title="x")
        await update_task(task.id, provider_name="native", status="in_progress")
        await update_task(task.id, provider_name="native", title="renamed")

    asyncio.run(go())
    assert fired == []


def test_RE_SAVING_a_done_task_does_NOT_re_fire(tmp_path, monkeypatch):
    """Edge-triggered. An idempotent projection recompute is the NORMAL path for workflow-bound
    tasks (§1), so level-triggering would emit one hook per rebuild."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    fired: list = []
    monkeypatch.setattr("personalclaw.hooks.get_global_hook_store", lambda: _fake_store(fired))

    from personalclaw.tasks.registry import create_task, update_task

    async def go():
        task = await create_task("native", title="x")
        await update_task(task.id, provider_name="native", status="done")
        await update_task(task.id, provider_name="native", status="done")

    asyncio.run(go())
    assert len(fired) == 1


def test_REOPENING_then_completing_fires_AGAIN(tmp_path, monkeypatch):
    """A genuine second completion is a second event — the edge rule must not latch."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    fired: list = []
    monkeypatch.setattr("personalclaw.hooks.get_global_hook_store", lambda: _fake_store(fired))

    from personalclaw.tasks.registry import create_task, update_task

    async def go():
        task = await create_task("native", title="x")
        await update_task(task.id, provider_name="native", status="done")
        await update_task(task.id, provider_name="native", status="open")
        await update_task(task.id, provider_name="native", status="done")

    asyncio.run(go())
    assert len(fired) == 2


def test_a_BROKEN_hook_does_not_fail_the_task_write(tmp_path, monkeypatch):
    """A hook is an OBSERVER of a task edit, not a participant: a user's broken script must not turn
    a successful `PUT /api/tasks/{id}` into a 500, and the task is already written by then."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))

    class _Boom:
        async def fire(self, event, context=""):
            raise RuntimeError("hook script exploded")

    monkeypatch.setattr("personalclaw.hooks.get_global_hook_store", lambda: _Boom())

    from personalclaw.tasks.registry import create_task, get_task, update_task

    async def go():
        task = await create_task("native", title="x")
        updated = await update_task(task.id, provider_name="native", status="done")
        return updated, await get_task(task.id)

    updated, reread = asyncio.run(go())
    assert updated is not None and updated.status.value == "done"
    assert reread.status.value == "done"


def test_NO_hook_store_is_not_an_error(tmp_path, monkeypatch):
    """The store is absent in plenty of contexts (CLI, tests, a bare provider import)."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.hooks.get_global_hook_store", lambda: None)

    from personalclaw.tasks.registry import create_task, update_task

    async def go():
        task = await create_task("native", title="x")
        return await update_task(task.id, provider_name="native", status="done")

    assert asyncio.run(go()).status.value == "done"


def test_the_fired_context_carries_workflow_PROVENANCE(tmp_path, monkeypatch):
    """A hook reacting to a workflow-projected task should be able to tell which run produced it."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    fired: list = []
    monkeypatch.setattr("personalclaw.hooks.get_global_hook_store", lambda: _fake_store(fired))

    from personalclaw.tasks.registry import create_task, update_task

    async def go():
        task = await create_task(
            "native",
            title="x",
            workflow_binding={"run_id": "r-1", "node_id": "deploy", "managed": True},
        )
        await update_task(task.id, provider_name="native", status="done")

    asyncio.run(go())
    assert "run=r-1" in fired[0][1]
    assert "node=deploy" in fired[0][1]

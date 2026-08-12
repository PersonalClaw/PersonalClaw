"""WF2LEA-14: a workspace-scoped lesson is STORED workspace-scoped, and stays put.

The defect these tests pin: `mcp_memory.memory_remember` advertised
``scope: global|workspace`` and enforced that a workspace name accompany
``scope='workspace'`` — then POSTed both fields to ``/api/lessons``, where
``api_lessons_create`` read only ``rule``/``category``/``negative``. Neither
``memory_service.write_lesson`` nor ``vector_memory.write_lesson`` had a scope
parameter at all, so the record landed at the ``MemoryRecord`` default (global) and
``MemoryScope.WORKSPACE`` sat in the inert-surface baseline. A caller that carefully
asked for a workspace-scoped lesson silently got a global one and was told "ok".

What is asserted here, end to end through the real handler:

* a workspace lesson persists ``scope='workspace'`` + ``scope_ref=<realpath>`` and
  reads back that way from a FRESH store over the same db file;
* it is NOT visible to another workspace, and NOT visible to a reader with no
  workspace identity — including through the real ``build_session_context``;
* a global lesson is still visible to everyone (existing lessons keep behaving);
* the three refusals: missing workspace, non-absolute workspace, unknown scope —
  each a 400, never a silent downgrade to global;
* every ``MemoryScope`` member is mapped, with no default branch that could turn a
  future member into "global".

All state is under ``tmp_path``; the real ``~/.personalclaw`` is never touched.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.handlers.schedule import api_lessons, api_lessons_create
from personalclaw.dashboard.state import DashboardState
from personalclaw.memory import MemoryStore
from personalclaw.memory_record import MemoryScope
from personalclaw.memory_service import (
    normalize_workspace_ref,
    resolve_lesson_scope,
    service_for,
)
from personalclaw.vector_memory import VectorMemoryStore

# ── helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def wired(tmp_path):
    """A DashboardState over a real memory.db, plus the two workspace dirs.

    Returns ``(state, db_path, ws_a, ws_b)``. ``ws_a``/``ws_b`` are real
    directories so ``realpath`` normalization is exercised rather than dodged.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    ws_a = tmp_path / "projects" / "alpha"
    ws_b = tmp_path / "projects" / "beta"
    ws_a.mkdir(parents=True)
    ws_b.mkdir(parents=True)

    db_path = tmp_path / "memory.db"
    mem = MemoryStore(workspace=ws)
    mem.init()
    vs = VectorMemoryStore(db_path=db_path, embedding_dim=3)
    vs.init()
    vs.embed_fn = lambda _t: [1.0, 0.0, 0.0]
    mem.vector_store = vs

    cb = MagicMock()
    cb.memory = mem
    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0, context_builder=cb)
    return state, db_path, str(ws_a), str(ws_b)


def _req(state, *, body=None, query=None):
    req = MagicMock()
    req.app = {"state": state}
    req.headers = {"X-Session-Key": "dashboard:ui"}
    req.query = query or {}

    async def _json():
        return body if body is not None else {}

    req.json = _json
    return req


async def _post(state, body):
    return await api_lessons_create(_req(state, body=body))


def _fresh_store(db_path) -> VectorMemoryStore:
    """A brand-new store over the same file — proves the scope is PERSISTED, not
    an artifact of the writing instance's in-memory state."""
    vs = VectorMemoryStore(db_path=db_path, embedding_dim=3)
    vs.init()
    return vs


def _rules(rows) -> set[str]:
    return {json.loads(r["value_json"]) for r in rows}


# ── the round trip: write scoped, read back scoped ────────────────────────────


@pytest.mark.asyncio
async def test_workspace_lesson_persists_workspace_scope_and_ref(wired):
    """(i) The stored record carries WORKSPACE + the scope_ref, from a fresh store."""
    state, db_path, ws_a, _ws_b = wired

    resp = await _post(
        state,
        {
            "rule": "this repo uses pytest-asyncio strict mode",
            "scope": "workspace",
            "workspace": ws_a,
        },
    )
    assert resp.status == 200

    rows = _fresh_store(db_path).get_lessons()
    assert len(rows) == 1
    assert rows[0]["scope"] == MemoryScope.WORKSPACE.value
    assert rows[0]["scope_ref"] == normalize_workspace_ref(ws_a)
    # The typed record view agrees with the row — the axis is not row-only trivia.
    rec = _fresh_store(db_path).get_record(rows[0]["key"])
    assert rec is not None and rec.scope is MemoryScope.WORKSPACE
    assert rec.scope_ref == normalize_workspace_ref(ws_a)


@pytest.mark.asyncio
async def test_workspace_lesson_is_invisible_to_another_workspace(wired):
    """(ii) The property that matters: no leak into an unrelated workspace."""
    state, db_path, ws_a, ws_b = wired
    await _post(
        state, {"rule": "alpha builds with make dev", "scope": "workspace", "workspace": ws_a}
    )

    vs = _fresh_store(db_path)
    assert _rules(vs.lessons_visible_in(normalize_workspace_ref(ws_a))) == {
        "alpha builds with make dev"
    }
    assert vs.lessons_visible_in(normalize_workspace_ref(ws_b)) == []
    # A reader with NO workspace identity is fail-closed too.
    assert vs.lessons_visible_in(None) == []
    # …while the inventory still sees it, so it remains listable and deletable.
    assert len(vs.get_lessons()) == 1


@pytest.mark.asyncio
async def test_global_lesson_is_still_visible_to_everyone(wired):
    """(iii) Existing lessons are all global and must keep reaching every reader."""
    state, db_path, ws_a, ws_b = wired
    await _post(state, {"rule": "always use dark mode"})  # no scope → global, as before

    vs = _fresh_store(db_path)
    assert vs.get_lessons()[0]["scope"] == MemoryScope.GLOBAL.value
    assert vs.get_lessons()[0]["scope_ref"] is None
    for ws in (None, normalize_workspace_ref(ws_a), normalize_workspace_ref(ws_b)):
        assert _rules(vs.lessons_visible_in(ws)) == {"always use dark mode"}


@pytest.mark.asyncio
async def test_mixed_store_shows_global_everywhere_and_workspace_only_at_home(wired):
    """The composite: one global + two workspace lessons, three different views."""
    state, db_path, ws_a, ws_b = wired
    await _post(state, {"rule": "prefer tabs nowhere"})
    await _post(state, {"rule": "alpha pins node 22", "scope": "workspace", "workspace": ws_a})
    await _post(state, {"rule": "beta pins node 18", "scope": "workspace", "workspace": ws_b})

    vs = _fresh_store(db_path)
    assert _rules(vs.lessons_visible_in(normalize_workspace_ref(ws_a))) == {
        "prefer tabs nowhere",
        "alpha pins node 22",
    }
    assert _rules(vs.lessons_visible_in(normalize_workspace_ref(ws_b))) == {
        "prefer tabs nowhere",
        "beta pins node 18",
    }
    assert _rules(vs.lessons_visible_in(None)) == {"prefer tabs nowhere"}
    assert len(vs.get_lessons()) == 3


@pytest.mark.asyncio
async def test_same_rule_text_global_and_workspace_do_not_collide(wired):
    """A workspace write must never UPSERT (and re-scope) the global row.

    Same text → same md5 → the old key derivation would have made the second write
    an update of the first, silently narrowing a global lesson to one directory.
    """
    state, db_path, ws_a, ws_b = wired
    await _post(state, {"rule": "run the linter"})
    await _post(state, {"rule": "run the linter", "scope": "workspace", "workspace": ws_a})

    vs = _fresh_store(db_path)
    scopes = sorted((r["scope"] or "global") for r in vs.get_lessons())
    assert scopes == ["global", "workspace"]
    # The global one still reaches the unrelated workspace.
    assert _rules(vs.lessons_visible_in(normalize_workspace_ref(ws_b))) == {"run the linter"}


@pytest.mark.asyncio
async def test_workspace_write_never_supersedes_a_global_lesson(wired):
    """Dedup is bucket-scoped: a narrower write cannot delete a wider record.

    "always run the full test suite before pushing" CONTAINS the global lesson's text,
    which in an unscoped dedup pass would have superseded (soft-deleted) it.
    """
    state, db_path, ws_a, ws_b = wired
    await _post(state, {"rule": "run tests"})
    await _post(
        state,
        {
            "rule": "run tests with the full suite before pushing",
            "scope": "workspace",
            "workspace": ws_a,
        },
    )

    vs = _fresh_store(db_path)
    assert _rules(vs.lessons_visible_in(normalize_workspace_ref(ws_b))) == {"run tests"}


# ── the injection path: the prompt a session actually gets ────────────────────


@pytest.mark.asyncio
async def test_injected_lessons_block_differs_by_working_directory(wired, tmp_path, monkeypatch):
    """Through the REAL `build_session_context`: cwd decides what is injected.

    `get_memory_for(cwd)` partitions memory by working directory, but that partition
    is COARSE — the gateway registers its one store under both the no-cwd `_default`
    key and the running workspace key (see `ContextBuilder.__init__`), so a single
    store backs several working directories in production. That is exactly where the
    leak lived, so the store here is registered under BOTH cwd keys: one memory.db,
    two working directories, and only the scope filter separates them.
    """
    from personalclaw import context as context_mod
    from personalclaw.config.loader import memory_dir_for_cwd
    from personalclaw.context import ContextBuilder
    from personalclaw.skills.loader import SkillsLoader

    state, _db_path, ws_a, ws_b = wired
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    mem = state.context_builder.memory
    monkeypatch.setattr(
        context_mod,
        "_memory_stores",
        {str(memory_dir_for_cwd(ws_a)): mem, str(memory_dir_for_cwd(ws_b)): mem},
    )

    await _post(state, {"rule": "global rule: never force-push main"})
    await _post(
        state,
        {"rule": "alpha rule: seed the fixture first", "scope": "workspace", "workspace": ws_a},
    )

    builder = ContextBuilder(
        memory=mem,
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )
    in_alpha = builder.build_session_context(cwd=ws_a)
    in_beta = builder.build_session_context(cwd=ws_b)

    assert "alpha rule: seed the fixture first" in in_alpha
    assert "alpha rule: seed the fixture first" not in in_beta
    for ctx in (in_alpha, in_beta):
        assert "global rule: never force-push main" in ctx


@pytest.mark.asyncio
async def test_lessons_context_service_leg_is_fail_closed(wired):
    """`MemoryService.lessons_context` normalizes its argument and defaults closed."""
    state, _db_path, ws_a, ws_b = wired
    await _post(state, {"rule": "alpha only rule", "scope": "workspace", "workspace": ws_a})

    svc = service_for(state.context_builder.memory)
    assert "alpha only rule" in svc.lessons_context(ws_a)
    assert "alpha only rule" not in svc.lessons_context(ws_b)
    assert "alpha only rule" not in svc.lessons_context()
    # A trailing separator / an unnormalized path still resolves to the same ref.
    assert "alpha only rule" in svc.lessons_context(ws_a + "/")


# ── the list surface ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_reports_scope_and_filters_on_request(wired):
    state, _db_path, ws_a, ws_b = wired
    await _post(state, {"rule": "everyone rule"})
    await _post(state, {"rule": "alpha rule", "scope": "workspace", "workspace": ws_a})

    body = json.loads((await api_lessons(_req(state))).body)
    by_rule = {e["rule"]: e for e in body["lessons"]}
    assert by_rule["everyone rule"]["scope"] == "global"
    assert by_rule["everyone rule"]["workspace"] == ""
    assert by_rule["alpha rule"]["scope"] == "workspace"
    assert by_rule["alpha rule"]["workspace"] == normalize_workspace_ref(ws_a)

    scoped = json.loads((await api_lessons(_req(state, query={"workspace": ws_b}))).body)
    assert {e["rule"] for e in scoped["lessons"]} == {"everyone rule"}


@pytest.mark.asyncio
async def test_list_refuses_a_relative_workspace_filter(wired):
    state, _db_path, _ws_a, _ws_b = wired
    resp = await api_lessons(_req(state, query={"workspace": "alpha"}))
    assert resp.status == 400


def test_memory_list_labels_a_workspace_lesson(monkeypatch):
    """The MCP inventory tool must not present a project-local rule as a universal one."""
    import personalclaw.mcp_memory as mcp_memory

    monkeypatch.setattr(
        mcp_memory,
        "_get",
        lambda _url: {
            "lessons": [
                {"rule": "everywhere", "category": "knowledge", "scope": "global", "workspace": ""},
                {
                    "rule": "only here",
                    "category": "tool",
                    "scope": "workspace",
                    "workspace": "/w/alpha",
                },
            ]
        },
    )
    out = mcp_memory._call_tool_inner("memory_list", {})
    assert "[knowledge] everywhere" in out
    assert "(workspace:" not in out.splitlines()[0]
    assert "[tool] only here (workspace: /w/alpha)" in out


# ── refusals: never silently downgrade ───────────────────────────────────────


@pytest.mark.asyncio
async def test_workspace_scope_without_a_workspace_is_a_400(wired):
    state, db_path, _ws_a, _ws_b = wired
    resp = await _post(state, {"rule": "scoped but nameless", "scope": "workspace"})
    assert resp.status == 400
    assert "workspace is required" in json.loads(resp.body)["error"]
    # Refused, not widened: nothing was written at all.
    assert _fresh_store(db_path).get_lessons() == []


@pytest.mark.asyncio
async def test_non_absolute_workspace_is_a_400(wired):
    """A bare project name would realpath against the GATEWAY's cwd — a ref that
    matches nothing. Silent invisibility is as dishonest as a silent downgrade."""
    state, db_path, _ws_a, _ws_b = wired
    resp = await _post(state, {"rule": "relative", "scope": "workspace", "workspace": "alpha"})
    assert resp.status == 400
    assert "absolute working-directory path" in json.loads(resp.body)["error"]
    assert _fresh_store(db_path).get_lessons() == []


@pytest.mark.parametrize("bad", ["everywhere", "GLOBAL_ISH", "session", "agent"])
@pytest.mark.asyncio
async def test_unknown_or_unwritable_scope_is_a_400(wired, bad):
    """An unmapped value must fail, not fall through to global.

    ``session``/``agent`` are real ``MemoryScope`` members with no lesson write path,
    so they are refused explicitly rather than mapped onto something else.
    """
    state, db_path, _ws_a, _ws_b = wired
    resp = await _post(state, {"rule": f"scope {bad}", "scope": bad})
    assert resp.status == 400
    assert _fresh_store(db_path).get_lessons() == []


@pytest.mark.parametrize("blank", ["", " ", None])
@pytest.mark.asyncio
async def test_absent_or_blank_scope_is_the_documented_default(wired, blank):
    """ "" and " " must mean the same thing — neither is a value a caller can see."""
    state, db_path, _ws_a, _ws_b = wired
    resp = await _post(state, {"rule": "unscoped", "scope": blank})
    assert resp.status == 200
    assert _fresh_store(db_path).get_lessons()[0]["scope"] == MemoryScope.GLOBAL.value


# ── the closed enum, enumerated ──────────────────────────────────────────────


def test_every_memory_scope_member_is_mapped_explicitly(tmp_path):
    """No default branch: each member either resolves or raises with a reason.

    A member added to `MemoryScope` later must trip this test rather than quietly
    becoming a global lesson.
    """
    writable = {MemoryScope.GLOBAL, MemoryScope.WORKSPACE}
    for member in MemoryScope:
        if member in writable:
            continue
        with pytest.raises(ValueError) as exc:
            resolve_lesson_scope(member.value, str(tmp_path))
        assert member.value in str(exc.value)

    assert resolve_lesson_scope("global", None) == (MemoryScope.GLOBAL, None)
    assert resolve_lesson_scope(None, None) == (MemoryScope.GLOBAL, None)
    # A workspace supplied with scope=global is ignored, not smuggled into scope_ref.
    assert resolve_lesson_scope("global", str(tmp_path)) == (MemoryScope.GLOBAL, None)
    assert resolve_lesson_scope("workspace", str(tmp_path)) == (
        MemoryScope.WORKSPACE,
        normalize_workspace_ref(str(tmp_path)),
    )
    assert resolve_lesson_scope(" WorkSpace ", str(tmp_path))[0] is MemoryScope.WORKSPACE


def test_normalize_workspace_ref_is_exact_never_fuzzy(tmp_path):
    """Two same-named checkouts under different parents must not share a ref."""
    a = tmp_path / "one" / "web"
    b = tmp_path / "two" / "web"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    assert normalize_workspace_ref(str(a)) != normalize_workspace_ref(str(b))
    assert normalize_workspace_ref("web") == ""
    assert normalize_workspace_ref("") == ""
    assert normalize_workspace_ref(None) == ""

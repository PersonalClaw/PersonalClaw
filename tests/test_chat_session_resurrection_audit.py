"""The session-resurrection audit: a deleted chat stays deleted.

``api_chat_session_delete`` is a HARD delete — it purges the JSONL history, the
tool-result store and the turn-checkpoint tree, precisely so a destroyed conversation
cannot come back (see ``test_chat_hard_delete.py``). Measured on ``origin/main``
against a live gateway, two writers undid that:

* ``POST /api/chat`` naming a hard-deleted key answered **200**, put the key back in
  ``GET /api/chat/sessions`` and served ``GET /api/chat/sessions/{key}`` 200 again —
  carrying only the resurrecting turn, so the conversation returned as a half-session.
* ``POST /api/chat/sessions/{session}/resume`` did the same, with an empty transcript.

There are **50** ``{session}``-addressed routes; ``/resume`` is one of them. Driving the
other 49 against an unknown key on a live gateway, **47 already answered 404**. The two
that did not are both under ``/api/skills/ephemeral/`` and neither writes a chat session:
``GET .../{session}`` answers an empty ``200`` (it lists a collection) and
``POST .../{session}/promote`` answers ``400`` because body validation runs first. So
this is not a missing convention — it is two writers outside an established one.

**What this module audits.** The durable question is *who owns "does this session key
exist, and may it be written?"*. That owner is
:func:`~personalclaw.dashboard.chat_persistence.session_key_exists`, and the hazard is a
writer that reaches ``state.get_or_create_session`` with a CLIENT-SUPPLIED name without
asking it first — because ``get_or_create_session`` mints a blank session on a miss.

The census is an AST walk (no import side effects) over every
``get_or_create_session`` call site in ``src/personalclaw``, keyed by
``file::qualname``. The keys are partitioned across the two hardcoded allowlists below.
**A new, unmapped call site reds this test naming its file:line** — the author must
consciously classify it as client-named (and then it must call the owner) or
creates-by-design. That conscious step is the control.

Adding a site → add its key to exactly one allowlist with a one-line reason. Removing a
site → drop its key. The test fails if the census and the union of the allowlists
disagree in either direction, so a stale entry is caught too. Modelled on
``test_spawn_ceiling_audit.py``, whose shape this is.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: The one owner. A client-named writer must call this before creating anything.
_OWNER = "session_key_exists"

#: The creator whose miss-branch mints a blank session — the mechanism being audited.
_CREATOR = "get_or_create_session"


# ── CLIENT-NAMED: the session name arrives from a REQUEST, so existence must be
#    proved first. Every key here is additionally asserted to call the owner. ──
_CLIENT_NAMED_MUST_GUARD: dict[str, str] = {
    "dashboard/chat_handlers.py::api_chat": (
        "POST /api/chat — `body['session']` is client-supplied; the resurrection this "
        "module is named for"
    ),
    "dashboard/chat_handlers.py::api_chat_session_resume": (
        "POST /api/chat/sessions/{session}/resume — `match_info['session']` / "
        "`body['key']` are client-supplied; the second resurrection"
    ),
}

# ── CREATES-BY-DESIGN: minting on a miss is the point. Either no name is passed at
#    all (the callee auto-generates ``chat-N-<ts>``), or the name is SERVER-derived
#    from a resource that owns it (a cron job, a loop, a plan, an API client id), or
#    the site sits BEHIND an existence check that is the check. ──
_CREATES_BY_DESIGN: dict[str, str] = {
    # ── no name at all → the callee mints `chat-N-<ts>` ──
    "channel_inbound.py::_route_to_session": (
        "no name — an unlinked channel thread mints a session, then link_channel binds it"
    ),
    "dashboard/handlers/investigate.py::api_investigate": "no name — investigate mints its own",
    "gateway.py::GatewayOrchestrator._deliver_result": "no name — result delivery mints its own",
    "dashboard/chat_fork.py::api_chat_session_fork": (
        "name=None — a fork mints a NEW key; the PARENT it reads is resolved and 404s"
    ),
    "dashboard/chat_fork.py::api_chat_session_fork_rewound": ("name=None — same as the fork above"),
    # ── the explicit create route ──
    "dashboard/chat_handlers.py::api_chat_session_create": (
        "POST /api/chat/sessions IS the create verb — this is the one route whose job "
        "is to mint a key, and it is what a client uses instead of naming a dead one"
    ),
    # ── SERVER-derived key: the owning resource's identity IS the session name, so a
    #    miss means 'this resource has no session yet', never 'the client typed a key' ──
    "dashboard/schedule_inject.py::inject_schedule_result_to_session": (
        "name=`cron-{job.id}` — derived from the cron job that owns the session"
    ),
    "loop/manager.py::start": "name=`session_key(loop_id)` — derived from the loop",
    "loop/manager.py::spawn_task_worker": (
        "name=`task_session_key(loop.id, task.id)` — derived from the loop task"
    ),
    "planning/runner.py::run_planner_pass": "name=`skey` — derived from the planner pass",
    "inbound/openai_dialect.py::handle_chat_completions": (
        "name=`session_key_for(client_id, tag)` — namespaced by the AUTHENTICATED client "
        "id, and create-on-first-use is this dialect's whole contract (it has no delete "
        "verb of its own, so there is no deleted state for a client to resurrect through)"
    ),
    # ── behind the existence check, or driven BY the log ──
    "dashboard/chat_persistence.py::_rehydrate_session_from_history": (
        "reached only AFTER resolve_history_key + get_metadata confirmed the key is "
        "persisted — this site IS the existence check the owner delegates to"
    ),
    "dashboard/chat_persistence.py::restore_recent_sessions": (
        "iterates keys the conversation log itself listed, so existence is a given"
    ),
}


# ── the scanner ───────────────────────────────────────────────────────────────


def scan_source(source: str, rel: str, creators: dict, guards: dict) -> None:
    """Record every ``get_or_create_session`` / owner call site in one module.

    Split out so the mechanism can be exercised against SYNTHETIC source (see
    :func:`test_the_scanner_sees_a_planted_unguarded_writer`). A census whose only test
    is "the tree currently measures N" cannot distinguish "no unguarded writers" from
    "the matcher is broken", and that distinction is the whole point.
    """
    tree = ast.parse(source)

    class V(ast.NodeVisitor):
        def __init__(self) -> None:
            self.q: list[str] = []

        def visit_FunctionDef(self, n) -> None:  # noqa: ANN001
            self.q.append(n.name)
            self.generic_visit(n)
            self.q.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, n) -> None:  # noqa: ANN001
            self.q.append(n.name)
            self.generic_visit(n)
            self.q.pop()

        def visit_Call(self, n: ast.Call) -> None:
            f = n.func
            callee = (
                f.attr
                if isinstance(f, ast.Attribute)
                else (f.id if isinstance(f, ast.Name) else "")
            )
            key = f"{rel}::{'.'.join(self.q) or '<module>'}"
            if callee == _CREATOR:
                creators.setdefault(key, []).append(n.lineno)
            elif callee == _OWNER:
                guards.setdefault(key, []).append(n.lineno)
            self.generic_visit(n)

    V().visit(tree)


def scan() -> tuple[dict[str, list[int]], dict[str, list[int]], int]:
    """``({creator_key: lines}, {guard_key: lines}, files_scanned)`` over the tree."""
    creators: dict[str, list[int]] = {}
    guards: dict[str, list[int]] = {}
    scanned = 0
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        try:
            scan_source(source, path.relative_to(SRC).as_posix(), creators, guards)
        except SyntaxError:  # pragma: no cover - defensive
            continue
        scanned += 1
    return creators, guards, scanned


#: Vacuity floor for the walk. The tree has ~950 modules; a scan that walks a handful
#: has lost its root, and "no unmapped call sites" would read as an improvement.
FILES_SCANNED_FLOOR = 800

#: The ``{session}``-addressed route population, derived from the real route table.
#: A FLOOR: the family this audit reasons about ("every other writer to a session")
#: is exactly this set, and a derivation that stopped finding it would make the
#: classification above look complete when it had simply gone blind. Measured at 50
#: (47 in ``dashboard/server.py`` + lifecycle/export/share in the two
#: ``register_routes`` modules).
SESSION_ROUTE_FLOOR = 45


# ── vacuity: a census that measures nothing must not read as clean ────────────


def test_the_census_is_not_vacuous():
    """Every assertion below is only as good as the walk that produced it."""
    creators, guards, scanned = scan()
    assert scanned >= FILES_SCANNED_FLOOR, (
        f"the census walked only {scanned} modules (floor {FILES_SCANNED_FLOOR}) — the "
        f"scan lost its root, so 'no unguarded writer' would read as an improvement. "
        f"Check SRC={SRC}."
    )
    assert creators, (
        f"no `{_CREATOR}` call site found ANYWHERE — the matcher is broken, and the "
        f"allowlists below are being compared against an empty set"
    )
    assert guards, (
        f"no `{_OWNER}` call site found anywhere — either the owner was deleted (and "
        f"then the two client-named writers resurrect again) or the matcher is broken"
    )


def test_the_session_route_family_is_still_derivable():
    """The floor under "47 of the other 49 already refuse".

    This audit's scope claim is about the ``{session}``-addressed route FAMILY. That
    family is derived from the real route table rather than hand-listed, so this asserts
    the derivation still finds it — otherwise a future reader could conclude the family
    is two routes wide because the walk broke.
    """
    routes = _session_routes()
    assert len(routes) >= SESSION_ROUTE_FLOOR, (
        f"only {len(routes)} '{{session}}'-addressed routes derived (floor "
        f"{SESSION_ROUTE_FLOOR}). The route-table walk lost its target, so this audit's "
        f"scope claim is unverified. Routes found: {sorted(routes)}"
    )
    # The two writers this module exists for must be IN the derived family, not assumed.
    assert ("POST", "/api/chat/sessions/{session}/resume") in routes, sorted(routes)


def _session_routes() -> set[tuple[str, str]]:
    """``{(method, path)}`` for every ``{session}``-addressed route, from the AST of the
    modules that register them. Derived, not hand-listed."""
    out: set[tuple[str, str]] = set()
    for rel in (
        "dashboard/server.py",
        "dashboard/session_bulk.py",
        "dashboard/session_starters.py",
    ):
        tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            attr = node.func.attr
            if not attr.startswith("add_"):
                continue
            method = attr[4:].upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            if not node.args:
                continue
            p = node.args[0]
            if isinstance(p, ast.Constant) and isinstance(p.value, str) and "{session}" in p.value:
                out.add((method, p.value))
    return out


# ── the classification ratchet ────────────────────────────────────────────────


def test_every_session_creator_call_site_is_classified():
    """Every ``get_or_create_session`` site is in exactly one allowlist.

    A new/unmapped site reds CI by name, forcing the author to decide whether the
    session name can arrive from a request (→ it must call the owner) or is minted /
    server-derived (→ creates-by-design).
    """
    creators, _guards, _scanned = scan()
    allow = set(_CLIENT_NAMED_MUST_GUARD) | set(_CREATES_BY_DESIGN)

    unmapped = sorted(set(creators) - allow)
    assert not unmapped, (
        f"Unmapped `{_CREATOR}` call site(s) — classify each in "
        f"tests/test_chat_session_resurrection_audit.py as CLIENT-NAMED (the session "
        f"name comes from a request → call "
        f"chat_persistence.{_OWNER} first and 404 `session_not_found` on a miss) or "
        f"CREATES-BY-DESIGN (no name, or a server-derived one):\n"
        + "\n".join(
            f"  {k}  ({', '.join(f'{k.split('::')[0]}:{ln}' for ln in creators[k])})"
            for k in unmapped
        )
    )

    stale = sorted(allow - set(creators))
    assert not stale, (
        f"Stale allowlist entr(y/ies) — no such `{_CREATOR}` site exists anymore; remove "
        f"from the allowlist:\n" + "\n".join(f"  {k}" for k in stale)
    )


def test_the_two_allowlists_are_disjoint():
    """A site cannot be both client-named and creates-by-design."""
    both = set(_CLIENT_NAMED_MUST_GUARD) & set(_CREATES_BY_DESIGN)
    assert not both, f"sites in both allowlists: {sorted(both)}"


def test_every_client_named_writer_actually_calls_the_owner():
    """The teeth. Classification alone would be a comment; this is the check.

    A handler listed as CLIENT-NAMED must call
    ``chat_persistence.session_key_exists`` in the SAME function that reaches
    ``get_or_create_session``. Deleting the guard while leaving the allowlist entry in
    place reds here — which is the failure mode a hand-maintained list cannot catch.
    """
    _creators, guards, _scanned = scan()
    missing = sorted(set(_CLIENT_NAMED_MUST_GUARD) - set(guards))
    assert not missing, (
        f"client-named writer(s) reach `{_CREATOR}` without asking `{_OWNER}` first, so a "
        f"hard-deleted session key can be written back into existence:\n"
        + "\n".join(f"  {k}  — {_CLIENT_NAMED_MUST_GUARD[k]}" for k in missing)
    )


# ── the vacuity floors for the ratchet, proved against source ─────────────────
#
# "No unmapped call site" and "every client-named writer guards" are both satisfied by a
# working scanner AND by a scanner that has stopped matching. These tell those apart.


_SYNTHETIC = '''
class State:
    def get_or_create_session(self, name=None):
        ...


async def a_new_unguarded_writer(request):
    """The hazard: a client-supplied name straight into the creator."""
    state = request.app["state"]
    name = request.match_info["session"]
    return state.get_or_create_session(name)


async def a_properly_guarded_writer(request):
    state = request.app["state"]
    name = request.match_info["session"]
    if not session_key_exists(state, name):
        return json_error("session_not_found", status=404)
    return state.get_or_create_session(name)
'''


def test_the_scanner_sees_a_planted_unguarded_writer():
    """The positive: a new creator call site is COUNTED, with its qualname."""
    creators: dict[str, list[int]] = {}
    guards: dict[str, list[int]] = {}
    scan_source(_SYNTHETIC, "synthetic.py", creators, guards)
    assert "synthetic.py::a_new_unguarded_writer" in creators, creators
    assert "synthetic.py::a_properly_guarded_writer" in creators, creators
    # The method DEFINITION is not a call site and must not be counted as one.
    assert "synthetic.py::State.get_or_create_session" not in creators, creators


def test_the_scanner_tells_a_guarded_writer_from_an_unguarded_one():
    """The negative half — otherwise "sees planted writers" could be satisfied by a
    scanner that simply flags everything."""
    creators: dict[str, list[int]] = {}
    guards: dict[str, list[int]] = {}
    scan_source(_SYNTHETIC, "synthetic.py", creators, guards)
    assert "synthetic.py::a_properly_guarded_writer" in guards, guards
    assert "synthetic.py::a_new_unguarded_writer" not in guards, (
        "the unguarded writer was scored as guarded — the owner matcher is matching the "
        "module, not the enclosing function"
    )


_HANDLERS = SRC / "dashboard" / "chat_handlers.py"
#: The live guard line in ``api_chat``. The plant below removes it.
_GUARD_LINE = "        if not session_key_exists(state, session_name):"


def test_stripping_the_real_guard_reds_the_ratchet():
    """The vacuity floor for :func:`test_every_client_named_writer_actually_calls_the_owner`.

    That test passes when the guard is present AND when the scanner has stopped
    detecting it. This proves the detector works against the source that actually
    ships: strip ``api_chat``'s guard out of the real module (a string swap on source
    read into memory — nothing is written to disk) and assert the ratchet's input
    changes. Do not delete this to make the ratchet cheaper; it is the only thing that
    makes "every client-named writer guards" mean anything.
    """
    real = _HANDLERS.read_text(encoding="utf-8")

    # 1) As it ships, api_chat guards — so a red below is the plant, not the tree.
    creators: dict[str, list[int]] = {}
    guards: dict[str, list[int]] = {}
    scan_source(real, "dashboard/chat_handlers.py", creators, guards)
    assert "dashboard/chat_handlers.py::api_chat" in guards, (
        "api_chat does not call the owner in the shipped source, so this floor cannot "
        f"attribute the plant. guards={sorted(guards)}"
    )

    # 2) The plant APPLIED. A swap that silently matched nothing would make step 3 a
    #    tautology about unmodified source — the exact shape of a vacuous guard.
    stripped = real.replace(
        _GUARD_LINE + '\n            return json_error("session_not_found", status=404)\n',
        "",
        1,
    )
    assert stripped != real, (
        f"the guard-strip swap matched nothing in {_HANDLERS.name}; _GUARD_LINE has "
        f"drifted from the shipped source, so this floor is measuring nothing. "
        f"Re-anchor it on api_chat's live `{_OWNER}` refusal."
    )
    assert _GUARD_LINE not in stripped, "the swap applied but the guard is still present"

    # 3) The detector notices — api_chat still reaches the creator, now unguarded.
    creators2: dict[str, list[int]] = {}
    guards2: dict[str, list[int]] = {}
    scan_source(stripped, "dashboard/chat_handlers.py", creators2, guards2)
    assert "dashboard/chat_handlers.py::api_chat" in creators2, (
        "api_chat stopped reaching get_or_create_session under the plant — the plant "
        "removed more than the guard, so this floor proves nothing"
    )
    assert "dashboard/chat_handlers.py::api_chat" not in guards2, (
        "stripping api_chat's `session_key_exists` call left the ratchet green. "
        "test_every_client_named_writer_actually_calls_the_owner is therefore measuring "
        f"the detector, not the code. guards={sorted(guards2)}"
    )


def test_a_new_unguarded_writer_planted_into_the_real_module_is_unmapped():
    """The vacuity floor for :func:`test_every_session_creator_call_site_is_classified`.

    "No unmapped call site" is also what a broken walk reports. So plant a brand-new
    unguarded writer into the REAL handlers module (in memory) and assert it lands
    OUTSIDE both allowlists — i.e. the classification ratchet would red and name it.
    """
    real = _HANDLERS.read_text(encoding="utf-8")
    planted = real + (
        "\n\nasync def api_chat_planted_writer(request):\n"
        '    state = request.app["state"]\n'
        '    return state.get_or_create_session(request.match_info["session"])\n'
    )
    creators: dict[str, list[int]] = {}
    guards: dict[str, list[int]] = {}
    scan_source(planted, "dashboard/chat_handlers.py", creators, guards)

    key = "dashboard/chat_handlers.py::api_chat_planted_writer"
    assert key in creators, f"the planted writer was not censused at all: {sorted(creators)}"
    allow = set(_CLIENT_NAMED_MUST_GUARD) | set(_CREATES_BY_DESIGN)
    assert key not in allow, (
        "the planted writer is somehow already allowlisted — the allowlist keys are not "
        "specific enough to catch a new site"
    )
    assert key not in guards, "the planted writer must not read as guarded"


# ── the behavioural rail: the defect itself, against an independent oracle ────
#
# The oracle is never the route under test. Existence is asserted from
# ``state._sessions`` and from the on-disk JSONL, plus a SECOND endpoint
# (``GET /api/chat/sessions/{key}``) — not from the send route's own reply.


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Every write lands in tmp_path — never the real ``~/.personalclaw``."""
    import personalclaw.config.loader as cfg
    import personalclaw.dashboard.state as st
    import personalclaw.session_workspace as ws

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(st, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)
    return tmp_path


def _seed_persisted_chat(state):
    """A session with turns PERSISTED to the JSONL (append alone is memory-only)."""
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    session = state.get_or_create_session(name=None)
    session.append("user", "the original question", "msg u0", broadcast=False)
    session.append("assistant", "the original answer", "msg a0", broadcast=False)
    session.drain()
    save_session_to_history(state, session, force=True)
    return session


async def _client(state) -> TestClient:
    client = TestClient(TestServer(_make_app(state)))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_a_send_to_a_deleted_session_is_a_coded_404(tmp_path):
    """The defect, end to end: DELETE then POST /api/chat must not resurrect.

    Measured before the fix: 200, and the key was back in the sidebar with only the
    resurrecting turn — "delete" degraded to "close".
    """
    from personalclaw.dashboard.chat_utils import _history_key_for

    state = _make_state(tmp_path)
    session = _seed_persisted_chat(state)
    key = session.key
    hk = _history_key_for(key)
    assert state.conversation_log.has_log(hk), "precondition: the history file exists"

    client = await _client(state)
    try:
        assert (await client.delete(f"/api/chat/sessions/{key}")).status == 200
        # Independent oracles: memory, disk, and a SECOND endpoint.
        assert key not in state._sessions
        assert not state.conversation_log.has_log(hk)
        assert (await client.get(f"/api/chat/sessions/{key}")).status == 404

        resp = await client.post("/api/chat", json={"session": key, "message": "resurrect"})
        assert resp.status == 404, f"a send to a deleted session answered {resp.status}"
        assert (await resp.json())["error"]["code"] == "session_not_found"

        # And it STAYED deleted — measured against the oracles, not the reply above.
        assert key not in state._sessions, "the refused send created the session anyway"
        assert not state.conversation_log.has_log(hk), "the refused send wrote history"
        assert (await client.get(f"/api/chat/sessions/{key}")).status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_resume_of_a_deleted_session_is_a_coded_404(tmp_path):
    """The second writer, found by deriving the family rather than taking the ticket."""
    from personalclaw.dashboard.chat_utils import _history_key_for

    state = _make_state(tmp_path)
    session = _seed_persisted_chat(state)
    key = session.key
    hk = _history_key_for(key)

    client = await _client(state)
    try:
        assert (await client.delete(f"/api/chat/sessions/{key}")).status == 200
        assert key not in state._sessions

        resp = await client.post(f"/api/chat/sessions/{key}/resume", json={})
        assert resp.status == 404, f"a resume of a deleted session answered {resp.status}"
        assert (await resp.json())["error"]["code"] == "session_not_found"
        assert key not in state._sessions, "the refused resume created the session anyway"
        assert not state.conversation_log.has_log(hk)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_never_created_key_is_refused_too(tmp_path):
    """A key that never existed is the same failure as a deleted one: a 200 claiming a
    continuity it does not have. Both writers refuse it."""
    state = _make_state(tmp_path)
    client = await _client(state)
    try:
        for path, body in (
            ("/api/chat", {"session": "chat-9999-1", "message": "hi"}),
            ("/api/chat/sessions/chat-9999-1/resume", {}),
        ):
            resp = await client.post(path, json=body)
            assert resp.status == 404, path
            assert (await resp.json())["error"]["code"] == "session_not_found", path
        assert "chat-9999-1" not in state._sessions
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_omitting_the_session_still_starts_a_new_conversation(tmp_path):
    """The contract the refusal does NOT break, and the reason a 404 is the right answer
    rather than "silently start a new session under a new key": starting a new
    conversation is already spelled by OMITTING ``session``. That is what the dashboard's
    ``ensureSession`` does, so a refusal on a named-but-dead key costs no reachable flow.
    """
    state = _make_state(tmp_path)
    client = await _client(state)
    try:
        # ``?ws=1`` so the reply is JSON rather than a held-open SSE stream.
        resp = await client.post("/api/chat?ws=1", json={"message": "a brand new chat"})
        assert resp.status == 200, await resp.text()
        minted = (await resp.json())["session"]
        assert minted and minted in state._sessions
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_disk_only_session_is_still_writable_after_eviction(tmp_path):
    """The regression the guard must NOT cause (G157).

    After a gateway restart only recent/pinned/foldered sessions are resident, so the
    first send to an older chat arrives for a key that is on DISK and not in memory.
    That must still work — the guard's predicate is "persisted metadata", not "resident".
    Simulated by evicting the session from ``state._sessions`` while leaving its JSONL.
    """
    from personalclaw.dashboard.chat_utils import _history_key_for

    state = _make_state(tmp_path)
    session = _seed_persisted_chat(state)
    key = session.key
    hk = _history_key_for(key)

    # Evict from memory ONLY — the disk history stays, exactly as after a restart.
    state._sessions.pop(key, None)
    assert key not in state._sessions
    assert state.conversation_log.has_log(hk), "precondition: history still on disk"

    client = await _client(state)
    try:
        resp = await client.post("/api/chat?ws=1", json={"session": key, "message": "still here?"})
        assert resp.status == 200, (
            f"a disk-only session was refused ({resp.status}) — the guard is asking "
            f"'resident?' instead of 'persisted?': {await resp.text()}"
        )
        # And it came back WITH its history, not as a blank session.
        assert key in state._sessions
        contents = [m["content"] for m in state._sessions[key].messages]
        assert "the original question" in contents, contents
    finally:
        await client.close()


def _archive(state, key: str) -> str:
    """Put *key* into the ARCHIVED state and evict it, returning its history key.

    Archival is ``closed: true`` on the meta line — the flag
    ``save_session_to_history(..., closed=True)`` writes, which is what
    ``POST /api/chat/sessions/cleanup`` does to a stale session. Written directly (with
    the session evicted) because the cleanup route only archives sessions whose last
    turn is older than a day, and the point here is the resulting STATE, not the route
    that produces it.
    """
    from personalclaw.dashboard.chat_utils import _history_key_for

    hk = _history_key_for(key)
    path = state.conversation_log._path(hk)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    meta = json.loads(lines[0])
    meta["closed"] = True
    lines[0] = json.dumps(meta) + "\n"
    path.write_text("".join(lines), encoding="utf-8")
    state.conversation_log._meta_cache.pop(hk, None)
    state._sessions.pop(key, None)
    return hk


@pytest.mark.asyncio
async def test_an_archived_session_is_still_writable_by_both_writers(tmp_path):
    """The OTHER regression the guard must not cause, and the reason the predicate is
    persisted-metadata rather than :func:`resolve_session`.

    ``resolve_session`` answers ``None`` for an ARCHIVED (``closed``) session — it is
    "give me the live session", and a soft-closed chat is not one. But archival is not
    deletion: the conversation is right there on disk, ``/cleanup`` put it away, and
    reopening it is the documented way back (``api_chat_session_resume`` explicitly
    CLEARS the closed flag). A predicate that asked ``resolve_session`` would 404 a key
    that exists — a brand-new refusal on live user data, worse than the bug being fixed.

    So the archived state is established first and asserted to be REAL — ``resolve_session``
    really does answer ``None`` there — and then both writers are required NOT to refuse
    it. Swap the predicate for ``resolve_session`` and this reds.

    Scope note: what each writer then DOES with an archived transcript is a property of
    the code behind the guard, not of the guard, and this PR changes none of it. Measured
    on this state: ``POST /resume`` answers 200 but with an EMPTY transcript, because
    everything past the guard reads ``body["key"]`` — the BARE key — while the file lives
    under the ``dashboard:`` form. That mismatch pre-dates this change (the guard itself
    asks ``resolve_history_key``, which tries both forms), so it is recorded here rather
    than asserted as correct. Do not "fix" this test by asserting the empty transcript is
    right.
    """
    from personalclaw.dashboard.chat_persistence import resolve_session

    state = _make_state(tmp_path)
    key = _seed_persisted_chat(state).key
    hk = _archive(state, key)

    # The archived state is real, and it IS the state resolve_session refuses. Asserted
    # against the disk (the oracle), not against the predicate under test.
    assert state.conversation_log.has_log(hk), "precondition: the archived history is on disk"
    assert state.conversation_log.get_metadata(hk).get("closed") is True
    assert resolve_session(state, key) is None, (
        "resolve_session no longer refuses an archived session, so this test can no "
        "longer tell the two predicates apart — re-anchor it on whatever `closed` became"
    )
    state._sessions.pop(key, None)  # resolve_session may have rehydrated on the way past

    client = await _client(state)
    try:
        # WRITER 2: resume is the documented way back into an archived chat.
        resp = await client.post(f"/api/chat/sessions/{key}/resume", json={})
        assert resp.status == 200, (
            f"resume of an ARCHIVED session was refused ({resp.status}) — the predicate "
            f"is asking 'is it live?' instead of 'does it exist?': {await resp.text()}"
        )

        # WRITER 1: and a send lands on it too.
        _archive(state, key)  # re-archive: resume is entitled to have cleared the flag
        resp = await client.post("/api/chat?ws=1", json={"session": key, "message": "still here?"})
        assert (
            resp.status == 200
        ), f"a send to an ARCHIVED session was refused ({resp.status}): {await resp.text()}"
        # The archived conversation is still THERE — the refusal-free path did not
        # quietly destroy what /cleanup put away. Oracle is the file, not a reply.
        assert state.conversation_log.has_log(hk)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_an_unreadable_log_does_not_lock_the_user_out(tmp_path):
    """The fail-open direction, measured through the scenario it is written for.

    ``resolve_history_key`` swallows a failed read into the same ``None`` it returns for
    "never persisted", so asking it alone cannot tell a hard-deleted key from a session
    whose file is present but unreadable — and answering "gone" for the second locks a
    user out of a chat that exists, which is a worse failure than the resurrection.

    The break is injected at the LOWEST layer that can fail on a real broken disk
    (``ConversationLog._read_metadata`` raising ``OSError``, exactly what
    ``path.read_text`` does on EIO), and the oracle is the route's status code plus the
    file's own presence on disk — never the predicate under test.
    """
    from personalclaw.dashboard.chat_utils import _history_key_for

    state = _make_state(tmp_path)
    key = _seed_persisted_chat(state).key
    hk = _history_key_for(key)
    state._sessions.pop(key, None)  # disk-only, as after a restart

    log = state.conversation_log
    log._meta_cache.pop(hk, None)
    real_read = log._read_metadata

    def unreadable(k: str):
        if k in (hk, key):
            raise OSError("Input/output error")
        return real_read(k)

    log._read_metadata = unreadable
    assert log.has_log(hk), "precondition: the file is still right there"

    client = await _client(state)
    try:
        resp = await client.post("/api/chat?ws=1", json={"session": key, "message": "still here?"})
        assert resp.status == 200, (
            f"an unreadable log refused a send ({resp.status}) on a session whose file "
            f"IS on disk — a broken disk now locks the user out of their own chat, which "
            f"is what the fail-open branch exists to prevent: {await resp.text()}"
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_failing_open_still_refuses_a_key_with_nothing_on_disk(tmp_path):
    """The other half of fail-open, or it would be indistinguishable from no guard.

    "An unreadable log reads as exists" must not widen into "any unreadable state reads
    as exists": with the SAME read failure injected, a key that has no file at all is
    still refused. Otherwise the previous test could be satisfied by a predicate that
    simply answered ``True`` — which is the bug.
    """
    state = _make_state(tmp_path)
    log = state.conversation_log

    def unreadable(k: str):
        raise OSError("Input/output error")

    log._read_metadata = unreadable
    assert not log.has_log("chat-4242-gone"), "precondition: no file for this key"

    client = await _client(state)
    try:
        for path, body in (
            ("/api/chat?ws=1", {"session": "chat-4242-gone", "message": "hi"}),
            ("/api/chat/sessions/chat-4242-gone/resume", {}),
        ):
            resp = await client.post(path, json=body)
            assert resp.status == 404, (
                f"{path} answered {resp.status} for a key with NO file on disk — the "
                f"fail-open branch has swallowed the refusal whole: {await resp.text()}"
            )
            assert (await resp.json())["error"]["code"] == "session_not_found", path
        assert "chat-4242-gone" not in state._sessions
    finally:
        await client.close()


def test_a_predicate_that_blows_up_answers_exists(tmp_path):
    """The defensive backstop under the fail-open branch.

    Deliberately a UNIT test, and the only one in this module — the two tests above
    drive the fail-open path a real broken disk takes (a failed metadata read) through
    the routes, but this branch cannot be reached through a route with an observable
    outcome: anything violent enough to make the predicate itself RAISE also raises in
    ``_rehydrate_session_from_history`` three lines later, which has no ``except``, so
    the request 500s no matter which way the predicate answered. Asserting a 200 there
    would be asserting something the system cannot deliver.

    So the contract is pinned directly, against a FIXED LITERAL (``True``) and a fault
    built here rather than borrowed from the code under test: a ``conversation_log``
    whose every method raises. Without this, flipping that ``return True`` to
    ``return False`` is a silent, untested behaviour change.
    """
    from personalclaw.dashboard.chat_persistence import session_key_exists

    class Unusable:
        def has_log(self, key):
            raise OSError("Input/output error")

        def get_metadata(self, key):
            raise OSError("Input/output error")

    state = _make_state(tmp_path)
    state.conversation_log = Unusable()
    assert session_key_exists(state, "chat-1-1") is True, (
        "a conversation log that raises on every call read as 'this key does not "
        "exist' — a bug in the existence check now costs the user their chat"
    )

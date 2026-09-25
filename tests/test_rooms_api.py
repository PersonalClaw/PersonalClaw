"""AR-2/AR-3 — the room HTTP surface (`handlers/rooms.py`).

The store is tested directly in `test_rooms_store.py`; these are the rails for what only
the HTTP layer can get wrong:

* the `rooms.enabled` kill switch covering the READS too, not just the writes;
* the stable error envelope, so every refusal the store can raise has a registry row and a
  sensible status rather than a 500;
* route ordering, since `/api/rooms/{room_id}` would swallow every sibling sub-path if it
  were registered first — the same failure `/api/channels/trust` already shipped once;
* the app-scoped refusal, which is inherited from `APP_SCOPED_PREFIXES` rather than coded
  here, and is therefore exactly the kind of property that silently stops being true.
"""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import rooms as h
from personalclaw.rooms import store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point config_dir and the home env at tmp_path (the real home is never touched)."""
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    yield tmp_path


@pytest.fixture
def cfg(monkeypatch):
    """A config with rooms ON and two configured agent bindings."""
    from personalclaw.config.loader import AgentProfile, AppConfig

    conf = AppConfig()
    conf.rooms.enabled = True
    conf.agents = {"analyst": AgentProfile(), "skeptic": AgentProfile()}
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls, *a, **k: conf))
    return conf


def _body(response):
    return json.loads(response.body.decode("utf-8"))


def _json_request(method, path, payload=None, **match_info):
    """A mocked request whose `.json()` returns *payload*.

    `make_mocked_request` gives no body, so `json_object_body` is fed through the payload
    attribute the aiohttp request reads — the same shape the other handler tests use.
    """
    req = make_mocked_request(method, path, match_info=match_info or {})

    async def _json():
        if payload is None:
            raise ValueError("no body")
        return payload

    req.json = _json  # type: ignore[method-assign]
    return req


def _create(title="Pricing debate"):
    return asyncio.run(h.api_rooms_create(_json_request("POST", "/api/rooms", {"title": title})))


def _list(query=""):
    return asyncio.run(h.api_rooms_list(make_mocked_request("GET", f"/api/rooms{query}")))


def _get(room_id):
    return asyncio.run(
        h.api_room_get(_json_request("GET", f"/api/rooms/{room_id}", room_id=room_id))
    )


def _add_member(room_id, payload):
    return asyncio.run(
        h.api_room_member_add(
            _json_request("POST", f"/api/rooms/{room_id}/members", payload, room_id=room_id)
        )
    )


def _post_message(room_id, payload):
    return asyncio.run(
        h.api_room_message_post(
            _json_request("POST", f"/api/rooms/{room_id}/messages", payload, room_id=room_id)
        )
    )


# ── the kill switch ─────────────────────────────────────────────────────────


def test_every_route_refuses_while_rooms_is_disabled(monkeypatch):
    """`rooms.enabled=false` means OFF, reads included — not "off for writes only"."""
    from personalclaw.config.loader import AppConfig

    conf = AppConfig()
    assert conf.rooms.enabled is False, "the shipped default"
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls, *a, **k: conf))

    for response in (
        _list(),
        _create(),
        _get("anything"),
        _add_member("anything", {"name": "analyst"}),
        _post_message("anything", {"content": "hi"}),
        asyncio.run(
            h.api_room_archive(_json_request("POST", "/api/rooms/x/archive", None, room_id="x"))
        ),
        asyncio.run(
            h.api_room_export(
                make_mocked_request("GET", "/api/rooms/x/export", match_info={"room_id": "x"})
            )
        ),
        # AR-8's budget PATCH. In this loop rather than in its own section, because "the kill
        # switch covers every route" is a claim about the SET of routes — a new one exempted by
        # omission is exactly how that claim stops being true.
        asyncio.run(
            h.api_room_update(
                _json_request("PATCH", "/api/rooms/x", {"round_budget": 3}, room_id="x")
            )
        ),
    ):
        assert response.status == 403
        assert _body(response)["error"]["code"] == "rooms_disabled"


def test_a_disabled_feature_writes_nothing_to_disk(tmp_path, monkeypatch):
    from personalclaw.config.loader import AppConfig

    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls, *a, **k: AppConfig()))
    _create()
    assert not (tmp_path / "rooms").exists()


# ── the happy path, drivable before any UI exists ──────────────────────────


def test_a_room_is_fully_drivable_over_the_api(cfg, tmp_path):
    """T1.6's done-when: a room works end to end with nothing but HTTP calls."""
    created = _create("Pricing debate")
    assert created.status == 201
    room_id = _body(created)["room"]["id"]

    listed = _body(_list())["rooms"]
    assert [r["id"] for r in listed] == [room_id]
    assert listed[0]["effective_round_budget"] == 6, "the resolved budget, not the declared 0"

    assert _add_member(room_id, {"name": "analyst", "role_blurb": "numbers"}).status == 201
    assert _add_member(room_id, {"name": "skeptic", "listen_policy": "mention"}).status == 201

    posted = _post_message(room_id, {"content": "what should we charge?"})
    assert posted.status == 201
    assert _body(posted)["messages"][0]["content"] == "what should we charge?"

    fetched = _body(_get(room_id))
    assert [m["name"] for m in fetched["room"]["members"]] == ["analyst", "skeptic"]
    assert [m["listen_policy"] for m in fetched["room"]["members"]] == ["all", "mention"]
    assert len(fetched["messages"]) == 1

    # And it is on disk under the home, at the path AR-2 names.
    assert (tmp_path / "rooms" / room_id / "transcript.jsonl").exists()

    removed = asyncio.run(
        h.api_room_member_remove(
            make_mocked_request(
                "DELETE",
                f"/api/rooms/{room_id}/members/skeptic",
                match_info={"room_id": room_id, "name": "skeptic"},
            )
        )
    )
    assert [m["name"] for m in _body(removed)["room"]["members"]] == ["analyst"]

    archived = asyncio.run(
        h.api_room_archive(
            _json_request("POST", f"/api/rooms/{room_id}/archive", None, room_id=room_id)
        )
    )
    assert _body(archived)["room"]["archived"] is True
    assert _body(_list())["rooms"] == []
    assert [r["id"] for r in _body(_list("?archived=1"))["rooms"]] == [room_id]


def test_export_answers_both_formats_and_refuses_a_third(cfg):
    room_id = _body(_create("Exportable"))["room"]["id"]
    _post_message(room_id, {"content": "hello room"})

    def _export(fmt):
        return asyncio.run(
            h.api_room_export(
                make_mocked_request(
                    "GET",
                    f"/api/rooms/{room_id}/export?format={fmt}",
                    match_info={"room_id": room_id},
                )
            )
        )

    md = _export("md")
    assert md.status == 200 and md.content_type == "text/markdown"
    assert "hello room" in md.text

    as_json = _export("json")
    assert as_json.content_type == "application/json"
    assert json.loads(as_json.text)["messages"][0]["content"] == "hello room"

    bad = _export("pdf")
    assert bad.status == 400
    assert _body(bad)["error"]["code"] == "room_export_format_invalid"


def _export_room(room_id, fmt):
    return asyncio.run(
        h.api_room_export(
            make_mocked_request(
                "GET",
                f"/api/rooms/{room_id}/export?format={fmt}",
                match_info={"room_id": room_id},
            )
        )
    )


def test_both_export_formats_report_the_ROOMs_creation_time(cfg):
    """The two surfaces disagreed about the same room in the same request cycle.

    `export_payload` handed the renderer the transcript log's metadata, whose `created_at` is
    stamped when the log is lazily created on the first message write. So the export claimed
    the room was created when someone first spoke in it, while `GET /api/rooms/{id}` — read
    here as the positive control, on the same gateway in the same cycle — returned the real
    value. That control is what makes this a data defect rather than an instrument artifact.
    """
    created = _body(_create("Exportable"))["room"]
    room_id = created["id"]
    _post_message(room_id, {"content": "hello room"})

    assert _body(_get(room_id))["room"]["created_at"] == created["created_at"]

    assert json.loads(_export_room(room_id, "json").text)["created_at"] == created["created_at"]
    assert f"- **Created:** {created['created_at']}" in _export_room(room_id, "md").text


def test_a_room_nobody_has_spoken_in_still_exports_a_created_at_and_its_header_row(cfg):
    """No transcript exists yet, so the metadata line is `{}` — which exported `created_at: ""`.

    The markdown header went straight from the title to `**Messages:** 0` with no `Created:`
    row at all, because `render_markdown` emits that row only when `meta` supplies the key.
    Both halves are asserted: the JSON value is real (not `""`) and the markdown row is back.
    """
    created = _body(_create("Silent"))["room"]
    room_id = created["id"]

    as_json = json.loads(_export_room(room_id, "json").text)
    assert as_json["messages"] == []
    assert as_json["created_at"] == created["created_at"] != ""

    markdown = _export_room(room_id, "md").text
    assert f"- **Created:** {created['created_at']}" in markdown
    assert "- **Messages:** 0" in markdown


# ── refusals: the envelope and the status ──────────────────────────────────


def test_an_unknown_room_is_a_404_in_the_stable_envelope(cfg):
    response = _get("no-such-room")
    assert response.status == 404
    assert _body(response)["error"]["code"] == "room_not_found"
    assert _body(response)["error"]["message"]


def test_a_ghost_room_cannot_export_as_an_empty_transcript(cfg):
    """A sub-resource read on a non-existent parent must 404, not render nothing.

    This is the ghost-parent/real-empty ambiguity `test_subresource_read_census.py` exists
    to catch, and the reason `/api/rooms/{room_id}/export` is carried there as an
    exclusion rather than needing its own pair in the parent-validation suite: the claim is
    that `require_room` resolves the parent first, and this is that claim asserted.
    """
    response = asyncio.run(
        h.api_room_export(
            make_mocked_request("GET", "/api/rooms/ghost/export", match_info={"room_id": "ghost"})
        )
    )
    assert response.status == 404
    assert _body(response)["error"]["code"] == "room_not_found"


def test_a_member_naming_an_unconfigured_binding_is_refused(cfg):
    room_id = _body(_create("Closed door"))["room"]["id"]
    response = _add_member(room_id, {"name": "nobody"})
    assert response.status == 400
    assert _body(response)["error"]["code"] == "room_member_unknown_agent"
    assert _body(_get(room_id))["room"]["members"] == []


def test_a_bad_listen_policy_is_refused(cfg):
    room_id = _body(_create("Policy"))["room"]["id"]
    response = _add_member(room_id, {"name": "analyst", "listen_policy": "whisper"})
    assert response.status == 400
    assert _body(response)["error"]["code"] == "room_invalid_listen_policy"


def test_a_duplicate_member_is_a_conflict(cfg):
    room_id = _body(_create("Dupe"))["room"]["id"]
    _add_member(room_id, {"name": "analyst"})
    response = _add_member(room_id, {"name": "analyst"})
    assert response.status == 409
    assert _body(response)["error"]["code"] == "room_member_exists"


def test_a_missing_title_is_refused_by_the_shared_validator(cfg):
    response = asyncio.run(h.api_rooms_create(_json_request("POST", "/api/rooms", {})))
    assert response.status == 400
    assert "error" in _body(response)


def test_an_unreadable_index_is_a_503_not_a_500(cfg, tmp_path):
    """Fail closed with a real code: the state is unavailable, not the request invalid."""
    rooms_dir = tmp_path / "rooms"
    rooms_dir.mkdir(parents=True, exist_ok=True)
    (rooms_dir / "index.json").write_text("{ broken", encoding="utf-8")

    response = _get("anything")
    assert response.status == 503
    assert _body(response)["error"]["code"] == "room_state_unreadable"


def test_the_human_cannot_forge_a_members_words(cfg):
    """The message route takes no speaker, so a caller cannot author as a member.

    The transcript is what every other member reads as that member's position, so a
    forgeable speaker would be a trust failure rather than a data-quality one.
    """
    from personalclaw.history import speaker_of

    room_id = _body(_create("Forgery"))["room"]["id"]
    _add_member(room_id, {"name": "analyst"})
    _post_message(room_id, {"content": "I am the analyst", "speaker": "analyst"})

    messages = store.read_messages(room_id)
    assert [speaker_of(m) for m in messages] == [""], "the extra field is ignored, not honoured"


# ── the inherited properties, asserted rather than re-implemented ──────────


#: The modules that can raise a ``RoomError``. Enumerated so a new one is a deliberate
#: edit here rather than a code the rails below silently stop covering — `turn.py` was
#: already outside the first version of this census.
_RAISING_MODULES = (
    "personalclaw.rooms.store",
    "personalclaw.rooms.turn",
    "personalclaw.rooms.posture",
    h.__name__,
)


def _raised_room_error_codes() -> set[str]:
    """Every wire code a ``RoomError`` is raised with, read from the source by AST.

    AST rather than a regex: the raises are split across one, two and three lines, and the
    regex this replaced matched only the first two shapes — so it read a smaller set than
    the code raises and every "missing row" check below would have been quietly narrower
    than it claimed.
    """
    import ast
    import importlib

    codes: set[str] = set()
    for name in _RAISING_MODULES:
        mod = importlib.import_module(name)
        tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            named = (isinstance(func, ast.Name) and func.id == "RoomError") or (
                isinstance(func, ast.Attribute) and func.attr == "RoomError"
            )
            if not named:
                continue
            first = node.args[0]
            assert isinstance(first, ast.Constant) and isinstance(first.value, str), (
                f"{name}: a RoomError is raised with a computed code "
                f"({type(first).__name__} at line {node.lineno}) — the wire code would then "
                f"be unknowable to every rail below. Raise a literal."
            )
            codes.add(first.value)
    assert len(codes) >= 12, f"the AST census read only {sorted(codes)} — it lost its root"
    return codes


def test_every_room_error_code_has_a_registry_row():
    """The append-only wire registry is the contract; a missing row is a bare code on the
    wire with no documented sentence."""
    from personalclaw.http_errors import HTTP_ERROR_CODES

    missing = sorted(c for c in _raised_room_error_codes() if c not in HTTP_ERROR_CODES)
    assert missing == [], f"room error codes with no registry row: {missing}"


def test_the_refusal_table_is_exhaustive_over_every_raised_room_error():
    """``_REFUSALS`` answering every raise is what makes its fallback unreachable.

    The handler no longer forwards ``exc.code``, so a raise with no row here would answer
    ``bad_request`` and drop the code the caller was meant to branch on. That degradation
    is deliberate (better than a 500) but it must never actually ship, and this is the rail
    that says so — not the fallback's own docstring.
    """
    unmapped = sorted(c for c in _raised_room_error_codes() if c not in h._REFUSALS)
    assert unmapped == [], (
        f"these RoomError codes have no _REFUSALS row in handlers/rooms.py, so they would "
        f"reach the caller as bad_request: {unmapped}"
    )
    unraised = sorted(c for c in h._REFUSALS if c not in _raised_room_error_codes())
    assert unraised == [], (
        f"these _REFUSALS rows answer a code nothing raises any more — delete them rather "
        f"than leaving a wire code no route can produce: {unraised}"
    )


def test_every_refusal_row_emits_the_code_it_is_keyed_on():
    """The price of naming each code as a literal is that it appears twice per row.

    A row keyed ``room_archived`` that emits ``room_not_found`` would be invisible to the
    append-only rail (both codes are registered literals) and to the exhaustiveness rail
    above (the key is right), and would simply put the wrong code on the wire. This is the
    check that makes the duplication safe rather than merely explicit.
    """
    import ast

    tree = ast.parse(pathlib.Path(h.__file__).read_text(encoding="utf-8"))
    table = next(
        (
            node.value
            for node in tree.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_REFUSALS"
        ),
        None,
    )
    assert isinstance(table, ast.Dict), "_REFUSALS is no longer a module-level dict literal"
    assert len(table.keys) == len(h._REFUSALS), "the parsed table and the live one disagree"
    for key, value in zip(table.keys, table.values):
        assert isinstance(key, ast.Constant) and isinstance(key.value, str), ast.dump(key)
        emitted = [
            call.args[0].value
            for call in ast.walk(value)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "json_error"
            and call.args
            and isinstance(call.args[0], ast.Constant)
        ]
        assert emitted == [key.value], (
            f"the _REFUSALS row keyed {key.value!r} emits {emitted} — a row must emit its "
            f"own code, as a literal, exactly once"
        )


def test_the_room_surface_emits_no_computed_wire_code():
    """The rail this module's shape exists for, asserted where it can be read.

    ``tests/test_http_error_codes_append_only.py`` holds a tree-wide CEILING on
    ``json_error`` sites whose code is computed, because an expression in the code slot is
    the one way an unregistered wire code enters unseen. Eight such sites arrived with this
    surface — every route's ``except RoomError`` — and raising that ceiling would have been
    the re-baselined-ratchet move: the number goes green and the check it was protecting
    stops policing the sites it was raised for. Pinned per-module here so a future route
    reverting to ``json_error(exc.code, ...)`` reds beside the code instead of only in a
    tree-wide count somebody else has to attribute.
    """
    from test_wire_error_envelope_census import Census, scan_source

    census = Census()
    rel = "src/personalclaw/dashboard/handlers/rooms.py"
    scan_source(pathlib.Path(h.__file__).read_text(encoding="utf-8"), rel, census)
    assert census.emitter_sites, "the census read no json_error site at all — it is broken"
    assert census.emitter_dynamic_sites == [], (
        f"these json_error sites on the room surface compute their code, so the "
        f"append-only registry check cannot see it: {census.emitter_dynamic_sites}"
    )
    resolved = {code for _f, _ln, code in census.emitter_literal_codes}
    assert set(h._REFUSALS) <= resolved, sorted(set(h._REFUSALS) - resolved)


def test_room_routes_sit_under_the_app_scoped_prefixes():
    """An installed app cannot reach a room route unless it declares the permission.

    Inherited, not coded in `handlers/rooms.py`: `/api/rooms` is under
    `APP_SCOPED_PREFIXES` and `app_request_denial` fails closed. Asserted here because an
    inherited property is exactly the kind that silently stops holding.
    """
    from personalclaw.apps.permissions import APP_SCOPED_PREFIXES

    assert any("/api/rooms".startswith(p) for p in APP_SCOPED_PREFIXES)


def test_the_room_id_route_is_registered_after_its_siblings():
    """`/api/rooms/{room_id}` would swallow `/archive`, `/members`, `/messages`, `/export`
    if it came first — aiohttp resolves in registration order, and this is the failure
    `/api/channels/trust` already shipped once."""
    from personalclaw.dashboard import server

    source = open(server.__file__, encoding="utf-8").read()
    catch_all = source.index('"/api/rooms/{room_id}", api_room_get')
    for sibling in ("/archive", "/members", "/messages", "/export"):
        assert source.index(f'"/api/rooms/{{room_id}}{sibling}"') < catch_all, sibling
    # AR-8's PATCH shares the catch-all's PATH and differs only in method, so ordering does not
    # apply to it — but it must still be registered, or the write path this atom added is a
    # handler nothing can reach.
    assert 'add_patch("/api/rooms/{room_id}", api_room_update)' in source


# ── the turn the message route now starts (AR-3) ───────────────────────────


class _RecordingRoomTurn:
    """Captures the ``(room_id, content)`` the route hands to ``rooms.arbiter``.

    The round itself is exercised against real providers in `test_rooms_arbiter.py`; what only
    the HTTP layer can get wrong is WHETHER it hands the round over, with which arguments,
    and whether the task outlives the request — which is what this records.
    """

    def __init__(self) -> None:
        self.rounds: list[tuple[str, str]] = []

    async def __call__(self, state, sessions, room_id, content):
        self.rounds.append((room_id, content))
        return []


class _FakeState:
    """The two attributes the route touches on the dashboard state, and nothing else."""

    def __init__(self) -> None:
        self.sessions = object()
        self._background_tasks: set = set()


def _post_message_with_state(room_id, payload, state):
    req = _json_request("POST", f"/api/rooms/{room_id}/messages", payload, room_id=room_id)
    req.app["state"] = state

    async def drive():
        response = await h.api_room_message_post(req)
        # Let the background round run before the loop closes; a task the route forgot to
        # keep a reference to would be collectable here instead.
        await asyncio.gather(*list(state._background_tasks))
        return response

    return asyncio.run(drive())


def test_posting_a_message_starts_the_round_for_the_listening_members(cfg, monkeypatch):
    """AR-3's residual, at the route: the human's line is what puts members on a session."""
    recorder = _RecordingRoomTurn()
    monkeypatch.setattr(h.arbiter, "run_round", recorder)

    room_id = _body(_create("Round"))["room"]["id"]
    _add_member(room_id, {"name": "analyst"})
    _add_member(room_id, {"name": "skeptic", "listen_policy": "mention"})
    state = _FakeState()

    response = _post_message_with_state(room_id, {"content": "what should we charge?"}, state)

    assert response.status == 201
    assert _body(response)["speaking"] == ["analyst"], "the mention-only member was not named"
    assert recorder.rounds == [(room_id, "what should we charge?")]
    assert state._background_tasks == set(), "the finished task is discarded, not retained"


def test_a_room_with_no_listening_member_starts_no_round(cfg, monkeypatch):
    """A room of observers costs nothing: no provider is opened and no task is created."""
    recorder = _RecordingRoomTurn()
    monkeypatch.setattr(h.arbiter, "run_round", recorder)

    room_id = _body(_create("Observers"))["room"]["id"]
    _add_member(room_id, {"name": "analyst", "listen_policy": "silent"})
    state = _FakeState()

    response = _post_message_with_state(room_id, {"content": "anyone?"}, state)

    assert _body(response)["speaking"] == []
    assert recorder.rounds == [], "nobody was listening, so nothing was started"


def test_the_humans_message_is_durable_even_when_no_session_manager_exists(
    cfg, monkeypatch, caplog
):
    """The human's words are the part they cannot re-derive, so the 201 does not depend on
    the round being startable — but the dropped round is logged at ERROR, never swallowed."""
    import logging

    monkeypatch.setattr(h.arbiter, "run_round", _RecordingRoomTurn())
    room_id = _body(_create("No manager"))["room"]["id"]
    _add_member(room_id, {"name": "analyst"})

    with caplog.at_level(logging.ERROR, logger="personalclaw.dashboard.handlers.rooms"):
        response = _post_message(room_id, {"content": "still recorded"})

    assert response.status == 201
    assert store.read_messages(room_id)[0]["content"] == "still recorded"
    assert "takes no turn" in caplog.text


# ── AR-8: the per-room budget write path, and the member facts its UI renders ────


def _patch_room(room_id, payload):
    return asyncio.run(
        h.api_room_update(_json_request("PATCH", f"/api/rooms/{room_id}", payload, room_id=room_id))
    )


def test_the_per_room_budget_is_settable_and_not_merely_readable(cfg):
    """`Room.round_budget` was published on the wire with NO writer anywhere.

    That is worse than an absent field: a client could read a per-room budget it had no way to
    set, which reads as "this is configurable" while being false. `AR-8` is the atom that had to
    either give it a write path or take it off the wire; this is the write path.
    """
    room_id = _body(_create("Budget room"))["room"]["id"]
    assert _body(_get(room_id))["room"]["round_budget"] == 0, "inherits by default"

    response = _patch_room(room_id, {"round_budget": 12})

    assert response.status == 200
    payload = _body(response)["room"]
    assert payload["round_budget"] == 12
    assert payload["effective_round_budget"] == 12, "the override wins over the configured default"
    # And it is PERSISTED — a value that only lived in the response would be a write that did not
    # happen.
    assert store.require_room(room_id).round_budget == 12


def test_zero_puts_a_room_back_on_the_configured_default(cfg):
    """0 is a real value and the way BACK, so it must be accepted rather than treated as unset."""
    room_id = _body(_create("Budget room"))["room"]["id"]
    _patch_room(room_id, {"round_budget": 12})

    payload = _body(_patch_room(room_id, {"round_budget": 0}))["room"]

    assert payload["round_budget"] == 0
    assert payload["effective_round_budget"] == cfg.rooms.round_budget


def test_an_out_of_range_budget_is_refused_rather_than_clamped(cfg):
    """Refused, because clamping would store a ceiling its author did not choose.

    The accepted range is the SAME one `rooms.round_budget` takes in `_EDITABLE_CONFIG` (1-100),
    plus 0 for inherit: a per-room override that accepted a value the config key refuses would
    make the two controls disagree about what a legal budget is.
    """
    room_id = _body(_create("Budget room"))["room"]["id"]

    for bad in (-1, 101, "six", 1.5, True):
        response = _patch_room(room_id, {"round_budget": bad})
        assert response.status == 400, bad
        assert _body(response)["error"]["code"] == "room_round_budget_invalid", bad
    assert store.require_room(room_id).round_budget == 0, "a refusal writes nothing"


def test_the_budget_route_owns_one_key_and_says_so(cfg):
    """A PATCH that ignored an unknown key would let a caller believe it changed something."""
    room_id = _body(_create("Budget room"))["room"]["id"]

    for payload in ({"title": "renamed"}, {"round_budget": 3, "paused": True}, {}):
        response = _patch_room(room_id, payload)
        assert response.status == 400, payload
        assert _body(response)["error"]["code"] in (
            "room_round_budget_invalid",
            "bad_request",
        ), payload
    assert store.require_room(room_id).title == "Budget room"


def test_an_archived_room_refuses_a_budget_change(cfg):
    room_id = _body(_create("Budget room"))["room"]["id"]
    asyncio.run(
        h.api_room_archive(
            _json_request("POST", f"/api/rooms/{room_id}/archive", None, room_id=room_id)
        )
    )

    response = _patch_room(room_id, {"round_budget": 9})

    assert response.status == 409
    assert _body(response)["error"]["code"] == "room_archived"


def test_the_wire_publishes_the_budget_ceiling_the_writer_accepts(cfg):
    """So a UI stepper takes its bounds from the save path instead of restating them.

    A control whose range disagrees with the writer's is an offer the writer refuses.
    """
    room_id = _body(_create("Budget room"))["room"]["id"]
    assert _body(_get(room_id))["room"]["max_round_budget"] == store.MAX_ROOM_ROUND_BUDGET
    # The claim is that the number is USABLE as a bound, so the boundary itself is exercised.
    assert _patch_room(room_id, {"round_budget": store.MAX_ROOM_ROUND_BUDGET}).status == 200
    assert _patch_room(room_id, {"round_budget": store.MAX_ROOM_ROUND_BUDGET + 1}).status == 400


def test_the_wire_publishes_the_member_ceiling_the_writer_enforces(cfg):
    """So the member picker can refuse the ninth agent instead of offering it.

    At 8/8 the panel still offered an agent and "Add to the room" answered 400
    `room_member_limit` — an offer the writer refuses, with a Retry that could never succeed.
    The ceiling is `rooms.max_members`, which the operator can change, so the UI must read the
    number the writer enforces rather than restate the default.
    """
    from personalclaw.config.loader import AgentProfile

    cfg.rooms.max_members = 2
    cfg.agents["writer"] = AgentProfile()
    room_id = _body(_create("Ceiling room"))["room"]["id"]
    assert _body(_get(room_id))["room"]["max_members"] == 2
    assert _body(_list())["rooms"][0]["max_members"] == 2, "the list carries it too"
    # The claim is that the number is the one ENFORCED, so the boundary itself is exercised.
    assert _add_member(room_id, {"name": "analyst"}).status == 201
    assert _add_member(room_id, {"name": "skeptic"}).status == 201
    refused = _add_member(room_id, {"name": "writer"})
    assert refused.status == 400
    assert _body(refused)["error"]["code"] == "room_member_limit"


def test_member_bindings_report_the_model_and_runtime_each_member_actually_holds(cfg):
    """`AR-8`'s member chips need the binding, and it is not derivable from the room record."""
    from personalclaw.config.loader import AgentProfile

    cfg.agents["analyst"] = AgentProfile(
        model="gemma4:12b", provider="native", description="numbers"
    )
    cfg.agents["skeptic"] = AgentProfile(model="", provider="acp:claude-code")
    room_id = _body(_create("Bindings"))["room"]["id"]
    _add_member(room_id, {"name": "analyst"})
    _add_member(room_id, {"name": "skeptic"})

    rows = {r["name"]: r for r in _body(_get(room_id))["member_bindings"]}

    assert rows["analyst"] == {
        "name": "analyst",
        "configured": True,
        "model": "gemma4:12b",
        "provider": "native",
        "description": "numbers",
    }
    # An empty model is a REAL state (the binding runs on the configured default for its use
    # case), so it is passed through rather than substituted.
    assert rows["skeptic"]["model"] == ""
    assert rows["skeptic"]["provider"] == "acp:claude-code"


def test_a_member_whose_binding_was_deleted_reports_unknown_not_the_default_agents_model(cfg):
    """🔴 THE FABRICATED-VALUE RAIL, and the reason this join does not use the ordinary path.

    `config.loader.resolve_agent_bindings` falls back to ``default_agent`` for an unknown name —
    its own docstring says so — so asking it would report the DEFAULT agent's model as this
    member's. A member whose binding vanished would then render as healthy, bound to a model it is
    not. ``configured: false`` with EMPTY fields is the honest answer.
    """
    from personalclaw.config.loader import AgentProfile

    cfg.agents["analyst"] = AgentProfile(model="gemma4:12b")
    cfg.default_agent = "analyst"
    room_id = _body(_create("Ghost"))["room"]["id"]
    _add_member(room_id, {"name": "skeptic"})
    # The binding is removed AFTER the member was added — `add_member` fails closed on an unknown
    # name, so this is the only way to reach the state, and it is reachable.
    del cfg.agents["skeptic"]

    row = _body(_get(room_id))["member_bindings"][0]

    assert row == {"name": "skeptic", "configured": False, "model": "", "provider": ""}
    assert "gemma4:12b" not in json.dumps(row), "the default agent's model must not leak in"


def test_the_room_payload_carries_everything_one_poll_needs(cfg):
    """The surface reads the room, its posture, its bindings and its transcript as ONE picture.

    Splitting them across routes would triple the polling traffic for state that is only ever read
    together — so the shape is asserted rather than left to a caller to discover.
    """
    room_id = _body(_create("One poll"))["room"]["id"]
    _add_member(room_id, {"name": "analyst", "role_blurb": "argues from the numbers"})

    payload = _body(_get(room_id))

    assert set(payload) == {"room", "member_posture", "member_bindings", "messages"}
    assert [r["name"] for r in payload["member_posture"]] == ["analyst"]
    assert [r["name"] for r in payload["member_bindings"]] == ["analyst"]
    room = payload["room"]
    # The three the pause card renders, plus the queue it lists.
    for field in ("paused", "rounds_used", "effective_round_budget", "pending_queue"):
        assert field in room, field

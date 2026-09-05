"""A wrong-typed field cannot poison the inbox store (issue 338).

`PUT /api/inbox/{id}` took the body's values on trust. Measured by driving the real handlers
against a temp home — four distinct defects, in the order the handler hit them:

    PUT {"draft": {"a": 1}}       -> TypeError 500 … and the value was ALREADY SAVED
      GET /api/inbox             -> TypeError 500, permanently, surviving restart
    PUT {"confidence": 123}      -> 200. Accepted silently. A wrong type with no symptom.
    PUT "notadict"               -> AttributeError 500 (`body.get` on a str)
    PUT {"status":"dismissed"} to a NONEXISTENT id
                                 -> 404 … with the id already persisted into
                                    `inbox_state.dismissed` and an engagement signal recorded

The corruption is unrecoverable by design, not by accident: `store.update` reached
`self.save()` inside the same loop that did the `setattr`, so the bad value was on disk before
the exception left the function. `redact_item` — the ONE redaction pass every reader runs — then
regexes `draft`, so a single poisoned item took out `GET /api/inbox` and `GET /api/inbox/pending`
whole (a list comprehension over every item), not just its own row.

The pre-404 pollution is the sharper finding. Three blocks ran before the item was known to
exist, and each decided independently whether it does: `mute_thread` guarded with `if item:`,
`favorited` passed `None` into `_record_signal`, and `dismiss` did neither. So this is not a
missing guard — it is three copies of a decision that belongs in one place.

`isinstance` is load-bearing over `type(v) is`: all four string fields are legitimately written
with `str`-subclassing enum MEMBERS by this package's own callers (`handlers_inbox:428` passes
`ItemStatus.DISMISSED` itself, not `.value`), so an exact-type check would refuse the
codebase's own writes. Pinned below.

ARCC was queried first (persisted user data is an explicit trigger domain). SAX-04 Outcome 3
names *"validating input after processing rather than before"* as a pitfall and requires
sanitizing **before storage** with type/format validation and logged failures — which is
exactly the ordering fix. (Outcome 5's state-consistency material is DynamoDB/Step Functions
machinery and does not apply to a local JSON store; noted, not stretched.)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard import handlers_inbox as h
from personalclaw.inbox import (
    Confidence,
    InboxFieldTypeError,
    InboxItem,
    InboxState,
    InboxStore,
    ItemStatus,
    redact_item,
    validate_updatable_fields,
)

ITEM_ID = "c1_1700000000.1"


def _item(**over: Any) -> InboxItem:
    base: dict[str, Any] = dict(
        id=ITEM_ID,
        channel="c1",
        channel_name="general",
        thread_ts=None,
        message="hello",
        sender_id="u1",
        sender_name="Ann",
    )
    base.update(over)
    return InboxItem(**base)


class _State:
    """The minimum DashboardState surface these handlers touch."""

    _inbox_svc = None

    def __init__(self, store: InboxStore, state: InboxState) -> None:
        self._inbox_store = store
        self._inbox_state = state
        self.broadcasts: list[tuple[str, Any]] = []

    def broadcast_ws(self, event: str, payload: Any) -> None:
        self.broadcasts.append((event, payload))


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)
    store = InboxStore(path=tmp_path / "inbox.json")
    store.add(_item())
    store.save()
    inbox_state = InboxState()
    monkeypatch.setattr(inbox_state, "save", lambda: None, raising=False)
    st = _State(store, inbox_state)
    # `_get_inbox` re-`load()`s the store off disk, which would discard the in-memory item.
    monkeypatch.setattr(store, "load", lambda: None, raising=False)
    return st, store, inbox_state, tmp_path


_MALFORMED = object()


async def _put(st: _State, body: Any, item_id: str = ITEM_ID) -> tuple[int, dict]:
    req = make_mocked_request("PUT", f"/api/inbox/{item_id}", match_info={"id": item_id})
    req.app["state"] = st

    async def _json():
        if body is _MALFORMED:
            raise json.JSONDecodeError("bad", "", 0)
        return body

    req.json = _json  # type: ignore[method-assign]
    resp = await h.api_inbox_update(req)
    return resp.status, json.loads(resp.text or "{}")


async def _get(st: _State) -> tuple[int, Any]:
    req = make_mocked_request("GET", "/api/inbox")
    req.app["state"] = st
    resp = await h.api_inbox_list(req)
    return resp.status, json.loads(resp.text or "[]")


# ── the corruption ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_object_draft_is_refused_and_the_list_still_reads(env):
    """🔑 The reported defect, end to end: the refusal, and the reader that used to die."""
    st, store, _, _ = env
    status, body = await _put(st, {"draft": {"a": 1}})
    assert status == 400
    assert body == {
        "error": {"code": "invalid_field_type", "message": "draft must be a string, got object"}
    }
    assert store.items[ITEM_ID].draft == "", "the refused value was applied anyway"
    assert (await _get(st))[0] == 200, "GET /api/inbox is still broken"


@pytest.mark.asyncio
async def test_nothing_is_written_to_disk_by_a_refused_write(env):
    """The corruption's real mechanism: `save()` lived in the same loop as the `setattr`, so
    the poison was on disk before the exception left the function. That is what made a
    restart no help."""
    st, _, _, tmp_path = env
    before = (tmp_path / "inbox.json").read_text()
    await _put(st, {"draft": {"a": 1}})
    assert (tmp_path / "inbox.json").read_text() == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("draft", {"a": 1}, "draft must be a string, got object"),
        ("draft", ["a"], "draft must be a string, got array"),
        ("draft", 5, "draft must be a string, got number"),
        ("draft", None, "draft must be a string, got null"),
        ("confidence", 123, "confidence must be a string, got number"),
        ("classification", {"x": 1}, "classification must be a string, got object"),
        ("status", 7, "status must be a string, got number"),
        ("favorited", "yes", "favorited must be a boolean, got string"),
        ("favorited", 1, "favorited must be a boolean, got number"),
    ],
)
async def test_every_updatable_field_is_type_checked(env, field, value, expected):
    """`confidence: 123` was a silent 200 — a wrong type with no immediate symptom, which is
    worse than the crash, not better. The message names the WIRE type, never the value: the
    caller controls it and it may be arbitrarily large."""
    st, _, _, _ = env
    status, body = await _put(st, {field: value})
    assert status == 400
    assert body["error"]["message"] == expected


# ── the malformed / non-object body ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_scalar_body_is_a_400_not_a_500(env):
    st, _, _, _ = env
    status, body = await _put(st, "notadict")
    assert (status, body["error"]["code"]) == (400, "invalid_body")


@pytest.mark.asyncio
async def test_a_null_body_is_a_400(env):
    """`null` is the dangerous scalar: `isinstance(None, dict)` is False, but a handler that
    only guarded falsiness would fall through with `body` unusable."""
    st, _, _, _ = env
    assert (await _put(st, None))[0] == 400


@pytest.mark.asyncio
async def test_malformed_json_is_a_400(env):
    st, _, _, _ = env
    status, body = await _put(st, _MALFORMED)
    assert (status, body["error"]["code"]) == (400, "invalid_json")


# ── the pre-404 pollution ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dismissing_a_nonexistent_id_persists_nothing(env, monkeypatch):
    """🔑 The pollution, measured: this answered 404 *after* adding the id to
    `inbox_state.dismissed` and recording a dismiss signal for a `None` item."""
    st, _, inbox_state, _ = env
    signals: list[Any] = []
    monkeypatch.setattr(h, "_record_signal", lambda state, item, kind: signals.append((item, kind)))
    status, body = await _put(st, {"status": "dismissed"}, "does_not_exist")
    assert (status, body) == (404, {"error": "not found"})
    assert inbox_state.dismissed == set(), "a 404 persisted a dismissal"
    assert signals == [], "a 404 recorded an engagement signal"


@pytest.mark.asyncio
async def test_a_refused_field_does_not_apply_the_side_effects_either(env):
    """🪤 Validation had to move ABOVE the side effects, not just into the store.

    A body that dismisses AND carries a bad field: the dismissal is a state write plus a
    learning signal, so validating only inside `inbox.update` would 400 the request with the
    dismissal already persisted. A partial mutation on a refused write is the same defect
    class as the corruption.
    """
    st, store, inbox_state, _ = env
    status, _ = await _put(st, {"status": "dismissed", "draft": {"bad": 1}})
    assert status == 400
    assert inbox_state.dismissed == set(), "the dismissal was persisted by a refused request"
    assert store.items[ITEM_ID].status == ItemStatus.PENDING


@pytest.mark.asyncio
async def test_a_multi_field_write_is_all_or_nothing(env):
    """The store validates every field before the first `setattr`, so the good half of a
    part-bad write is not applied."""
    st, store, _, _ = env
    assert (await _put(st, {"draft": "real text", "confidence": 9}))[0] == 400
    assert store.items[ITEM_ID].draft == "", "the good field of a refused write was applied"


# ── what must still work ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_ordinary_updates_still_work(env):
    """Vacuity floor. Every one of these is a real frontend payload (`InboxDetail.patch`)."""
    st, store, inbox_state, _ = env
    assert (await _put(st, {"draft": "hello"}))[0] == 200
    assert store.items[ITEM_ID].draft == "hello"
    assert (await _put(st, {"classification": "needs_reply", "confidence": "user"}))[0] == 200
    assert (await _put(st, {"status": "handled"}))[0] == 200
    assert (await _put(st, {"favorited": True}))[0] == 200
    assert store.items[ITEM_ID].favorited is True


@pytest.mark.asyncio
async def test_dismiss_and_mute_still_record_for_a_real_item(env):
    st, _, inbox_state, _ = env
    assert (await _put(st, {"status": "dismissed"}))[0] == 200
    assert inbox_state.dismissed == {ITEM_ID}
    assert (await _put(st, {"mute_thread": True}))[0] == 200
    assert inbox_state.muted_threads, "muting a real item stopped working"


def test_the_store_refuses_a_bad_value_on_its_own(env):
    """The store validates for its own sake, not as a backstop to the handler.

    Three of its four callers never come through HTTP (`api_inbox_proposal_apply`,
    `api_inbox_send`, `api_inbox_favorite`, plus `inbox_service._classify`), so a guard that
    lived only in the PUT handler would leave those paths able to poison the store exactly as
    before. Driven directly, and asserted to have written nothing.
    """
    _, store, _, tmp_path = env
    before = (tmp_path / "inbox.json").read_text()
    with pytest.raises(InboxFieldTypeError):
        store.update(ITEM_ID, draft={"a": 1})
    assert store.items[ITEM_ID].draft == ""
    assert (tmp_path / "inbox.json").read_text() == before


def test_the_store_applies_nothing_when_a_later_field_is_bad(env):
    """The validation pass runs over ALL kwargs before the first `setattr` — the old single
    loop had already mutated (and saved) the earlier field by the time it reached the bad
    one."""
    _, store, _, _ = env
    with pytest.raises(InboxFieldTypeError):
        store.update(ITEM_ID, draft="good", confidence=0)
    assert store.items[ITEM_ID].draft == "", "an earlier field was applied before the refusal"


def test_the_packages_own_enum_writes_are_not_refused(env):
    """🪤 Why `isinstance`, not `type(v) is`.

    `handlers_inbox:428` calls `inbox.update(item.id, status=ItemStatus.DISMISSED)` — the enum
    MEMBER, not its `.value`. All four string fields' enums subclass `str`, so `isinstance`
    accepts them and an exact-type check would have refused the codebase's own writes. This
    asserts the property rather than the four class declarations.
    """
    _, store, _, _ = env
    assert store.update(ITEM_ID, status=ItemStatus.DISMISSED) is not None
    assert store.update(ITEM_ID, confidence=Confidence.NEEDS_REVIEW) is not None


# ── the recovery: a store already poisoned by an older build ─────────────────────────────


def test_an_already_poisoned_store_loads_and_reads_again(tmp_path: Path):
    """🔑 The half a 400 alone cannot reach.

    The corruption survives restart, so anyone who already hit this has an inbox that 500s
    forever with no UI recourse. `from_dict` was ALREADY tolerant of unknown keys — that is
    what kept `favorited` back-compatible — so extending the same tolerance to a wrong-typed
    value is this function's existing job, not a new mechanism.
    """
    (tmp_path / "inbox.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": ITEM_ID,
                        "channel": "c1",
                        "channel_name": "general",
                        "thread_ts": None,
                        "message": "the message must survive",
                        "sender_id": "u1",
                        "sender_name": "Ann",
                        "draft": {"poisoned": True},
                        "confidence": 42,
                        "favorited": "yes",
                        "status": "pending",
                    }
                ]
            }
        )
    )
    store = InboxStore(path=tmp_path / "inbox.json")
    store.load()

    item = store.items[ITEM_ID]
    assert item.draft == "" and item.confidence == Confidence.NEEDS_REVIEW
    assert item.favorited is False
    # Only the unusable fields are dropped — the record is repaired, not discarded.
    assert item.message == "the message must survive"
    assert item.status == "pending"
    # And the reader that used to raise now runs.
    assert redact_item(item.to_dict())["draft"] == ""


def test_a_required_field_is_never_silently_invented(tmp_path: Path):
    """The repair is deliberately scoped to fields that HAVE a default. A record missing `id`
    is genuinely unreadable, and `load` already logs-and-continues for that; inventing an id
    would put a phantom row in the user's inbox.
    """
    (tmp_path / "inbox.json").write_text(json.dumps({"items": [{"channel": "c1"}]}))
    store = InboxStore(path=tmp_path / "inbox.json")
    with pytest.raises(TypeError):
        store.load()


# ── the rails ────────────────────────────────────────────────────────────────────────────


def test_the_type_map_covers_exactly_the_updatable_fields():
    """🪤 The hand-maintained pair. `_UPDATABLE_FIELDS` (the HTTP allowlist) and
    `_UPDATABLE_FIELD_TYPES` (the type contract) live in different modules, so adding a
    sixth updatable field would otherwise re-open this bug for exactly that field —
    silently, since it would be accepted and only fail later, in a reader.
    """
    from personalclaw.inbox import _UPDATABLE_FIELD_TYPES

    assert set(_UPDATABLE_FIELD_TYPES) == h._UPDATABLE_FIELDS


def test_every_typed_field_has_a_dataclass_default():
    """🪤 What makes the repair safe. `from_dict` drops a wrong-typed value and lets the
    dataclass default fill it in; a typed field with NO default would instead raise
    `TypeError` at construction and take out the whole load.
    """
    import dataclasses

    from personalclaw.inbox import _UPDATABLE_FIELD_TYPES

    fields = {f.name: f for f in dataclasses.fields(InboxItem)}
    for name in _UPDATABLE_FIELD_TYPES:
        f = fields[name]
        has_default = f.default is not dataclasses.MISSING or (
            f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
        )
        assert has_default, f"{name} is type-checked but has no default to fall back to"


def test_the_declared_type_matches_the_dataclass_annotation():
    """🪤 The other half of the same drift: a field whose annotation changes (say `draft`
    becoming `str | None`) but whose entry here does not would start refusing valid writes.
    """
    import typing

    from personalclaw.inbox import _UPDATABLE_FIELD_TYPES

    hints = typing.get_type_hints(InboxItem)
    for name, declared in _UPDATABLE_FIELD_TYPES.items():
        assert hints[name] is declared, f"{name}: map says {declared}, dataclass says {hints[name]}"


def test_validation_is_reachable_from_both_call_sites():
    """One implementation, two callers — the handler (before its side effects) and the store
    (for the three callers that never touch HTTP). A guard with one caller is how the other
    path stays broken."""
    src = (Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "inbox.py").read_text()
    handler_src = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "personalclaw"
        / "dashboard"
        / "handlers_inbox.py"
    ).read_text()
    assert "validate_updatable_fields(kwargs)" in src, "the store stopped validating"
    assert "validate_updatable_fields(updates)" in handler_src, "the handler stopped validating"


def test_the_direct_validator_raises_on_the_first_bad_field():
    with pytest.raises(InboxFieldTypeError) as exc:
        validate_updatable_fields({"draft": 1})
    assert exc.value.field == "draft"
    validate_updatable_fields({"draft": "ok", "favorited": False})  # no raise


def test_the_refusal_never_echoes_the_callers_value():
    """A 400 body is caller-visible and a caller-supplied value can be arbitrarily large (or
    hostile). The message carries type names only."""
    with pytest.raises(InboxFieldTypeError) as exc:
        validate_updatable_fields({"draft": {"secret": "x" * 5000}})
    assert "secret" not in str(exc.value) and len(str(exc.value)) < 80


def test_every_async_test_here_carries_the_asyncio_marker():
    """🪤 Vacuity floor: pytest-asyncio is in STRICT mode in this repo, so an async test with
    no marker is not an error — it is skipped with a warning and counted as a pass."""
    import inspect
    import sys

    module = sys.modules[__name__]
    unmarked = [
        name
        for name, fn in vars(module).items()
        if name.startswith("test_")
        and inspect.iscoroutinefunction(fn)
        and "asyncio" not in {m.name for m in getattr(fn, "pytestmark", [])}
    ]
    assert unmarked == [], f"async tests that would silently not run: {unmarked}"

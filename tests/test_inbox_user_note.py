"""A user-authored note reaches the inbox (INU-9).

Every inbox source that shipped before this was **synthesized**: a notification rule fired, a
run needed input, a poll found a message, an app contributed a proposal. There was no way for
a person to put something in their own inbox — which is why the desktop tray's "Quick Capture
Note…" row (DC-4) deep-linked ``#/inbox?capture=1`` and wrote nothing. `web/src` had zero
readers for that flag; the router parsed it and dropped it.

So these tests refuse to stop at the dataclass. Each one drives the REAL path — ``POST
/api/inbox/notes`` → :func:`personalclaw.inbox.emit_attention_item` → the persisted row
re-read **off disk** → ``GET /api/inbox`` — because the two failure modes worth catching are
both invisible to a unit test on the model:

* an item that exists only in the service's memory (the store holds items in RAM and never
  re-reads its file, so a writer that skipped ``flush()`` would pass any in-process
  assertion and lose the user's note at the next restart), and
* a notification target the rules vocabulary advertises but cannot deliver — the phantom-source
  failure two sibling atoms spent a week untangling. Here the check is direct: the pair is
  registered, its wire string round-trips, it renders as a row in the rules matrix, AND
  ``state.notify`` is observed carrying that wire string.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw import notification_kinds as nk
from personalclaw.dashboard import handlers_inbox as h
from personalclaw.http_errors import HTTP_ERROR_CODES
from personalclaw.inbox import NON_CHANNEL_KINDS, InboxState, InboxStore, ItemKind, ItemStatus

#: Every wire code `api_inbox_note_create` can answer with. Written out rather than scraped so
#: a new refusal path has to be listed here deliberately (and then owes its registry row).
NOTE_CODES = (
    "invalid_json",
    "invalid_body",
    "note_text_empty",
    "note_too_long",
    "note_not_saved",
)


# ── harness ──


def _svc(tmp_path):
    """A stand-in for the running inbox service, on a real store at a real path.

    `_get_inbox` prefers `state._inbox_svc.{state,inbox}`, which is the instance the live
    gateway serves — the same seam `inbox.live_store` exists to protect. Using real
    `InboxStore`/`InboxState` objects (not mocks) is what makes the disk assertions mean
    anything.
    """
    return SimpleNamespace(
        state=InboxState(tmp_path / "inbox_state.json"),
        inbox=InboxStore(tmp_path / "inbox.json"),
    )


def _state(svc):
    st = MagicMock()
    st._inbox_svc = svc
    return st


def _request(state, body, *, raw: str | None = None):
    req = MagicMock()
    req.app = {"state": state}
    req.query = {}
    if raw is not None:
        req.json = AsyncMock(side_effect=ValueError("not json"))
    else:
        req.json = AsyncMock(return_value=body)
    return req


async def _post(state, body, *, raw: str | None = None):
    resp = await h.api_inbox_note_create(_request(state, body, raw=raw))
    return resp, json.loads(resp.body.decode())


def _list(svc, query=None):
    req = MagicMock()
    req.app = {"state": _state(svc)}
    req.query = query or {}
    return req


async def _payload(resp):
    return json.loads(resp.body.decode())


# ── the capability: a person writes an inbox item ──


@pytest.mark.asyncio
async def test_a_note_becomes_an_inbox_item_through_a_real_endpoint(tmp_path):
    svc = _svc(tmp_path)
    resp, body = await _post(_state(svc), {"text": "Chase the invoice discrepancy"})

    assert resp.status == 201
    assert body["ok"] is True and body["id"]
    item = body["item"]
    assert item["message"] == "Chase the invoice discrepancy"
    assert item["item_kind"] == ItemKind.USER_NOTE.value
    assert item["status"] == ItemStatus.PENDING.value
    # No channel behind it, so no reply machinery — the UI keys its Send gate off this.
    assert item["can_reply"] is False
    assert ItemKind.USER_NOTE.value in NON_CHANNEL_KINDS


@pytest.mark.asyncio
async def test_the_typed_kind_says_user_authored_without_a_second_lookup(tmp_path):
    """The distinction is the whole point: a consumer must be able to TELL.

    Asserted as a partition over a store holding both provenances, and decided from
    ``item_kind`` ALONE — not from ``source``, not from ``sender_name``. A consumer that had
    to join two fields to answer "did a person write this?" would get it wrong the first time
    a synthesized emitter reused the source string.
    """
    from personalclaw.inbox import emit_attention_item

    svc = _svc(tmp_path)
    state = _state(svc)
    await _post(state, {"text": "Ask about the renewal"})
    # A synthesized sibling raised through the same seam, so the two are genuinely comparable.
    emit_attention_item(
        state,
        source="system",
        kind="digest",
        item_kind=ItemKind.DIGEST.value,
        title="Digest — 3 notifications",
        store=svc.inbox,
    )

    rows = await _payload(await h.api_inbox_list(_list(svc)))
    assert len(rows) == 2
    authored = [r for r in rows if r["item_kind"] == ItemKind.USER_NOTE.value]
    synthesized = [r for r in rows if r["item_kind"] != ItemKind.USER_NOTE.value]
    assert [r["message"] for r in authored] == ["Ask about the renewal"]
    assert len(synthesized) == 1
    # And the value spells the provenance out, so the test above is not tautological on a
    # kind name that merely happens to be unique.
    assert ItemKind.USER_NOTE.value == "user_note"


@pytest.mark.asyncio
async def test_the_first_line_is_the_subject_and_no_typed_line_is_lost(tmp_path):
    """A multi-line note keeps every line; only the blank line after the subject is added.

    `emit_attention_item` joins title and body with a blank line, so passing the whole blob as
    the title would have made the notification's subject the entire note, and passing a
    truncated preview as the title would have dropped the tail of line one from `message`.
    """
    svc = _svc(tmp_path)
    note = "Groceries for the week\n- oat milk\n- the good bread"
    _, body = await _post(_state(svc), {"text": note})

    assert body["item"]["message"] == "Groceries for the week\n\n- oat milk\n- the good bread"
    for line in ("Groceries for the week", "- oat milk", "- the good bread"):
        assert line in body["item"]["message"]


@pytest.mark.asyncio
async def test_the_chip_row_and_the_kind_filter_both_reach_a_note(tmp_path):
    """The filter value a user's URL carries is the same string the row persists."""
    svc = _svc(tmp_path)
    await _post(_state(svc), {"text": "Book the dentist"})

    selected = await _payload(await h.api_inbox_list(_list(svc, {"kind": "user_note"})))
    assert [r["message"] for r in selected] == ["Book the dentist"]
    assert await _payload(await h.api_inbox_list(_list(svc, {"kind": "message"}))) == []

    chips = (await _payload(await h.api_inbox_kinds(_list(svc))))["kinds"]
    assert [(c["kind"], c["open"], c["channel"]) for c in chips] == [("user_note", 1, False)]


# ── persistence: the note must survive the gateway that wrote it ──


@pytest.mark.asyncio
async def test_the_note_survives_a_restart_read_back_off_disk(tmp_path):
    """The proof, and the one an in-memory-only implementation would fail.

    A "restart" here is modelled the only way it can be inside a test: every in-process
    object that touched the write is discarded, and a FRESH `InboxStore` re-reads the file the
    endpoint wrote. Then the API is served from that reloaded store, so the assertion is about
    what a user sees after restarting, not about what the writer remembers.
    """
    path = tmp_path / "inbox.json"
    svc = _svc(tmp_path)
    _, body = await _post(_state(svc), {"text": "Renew the domain before the 3rd"})
    note_id = body["id"]

    # The file exists and is the durable copy — not the writer's memory.
    assert path.is_file()
    on_disk = json.loads(path.read_text())
    assert [i["item_kind"] for i in on_disk["items"]] == [ItemKind.USER_NOTE.value]

    # ── restart ──
    del svc
    reloaded = InboxStore(path)
    reloaded.load()
    assert note_id in reloaded.items
    assert reloaded.items[note_id].message == "Renew the domain before the 3rd"

    restarted = SimpleNamespace(state=InboxState(tmp_path / "inbox_state.json"), inbox=reloaded)
    rows = await _payload(await h.api_inbox_list(_list(restarted)))
    assert [(r["id"], r["message"]) for r in rows] == [(note_id, "Renew the domain before the 3rd")]


@pytest.mark.asyncio
async def test_a_write_that_did_not_land_is_never_reported_as_saved(tmp_path):
    """`emit_attention_item` swallows a failed write so it does not also lose the
    notification, and returns "". Answering 201 there would tell the user their only copy of
    the text was kept when it was not."""
    svc = _svc(tmp_path)

    def boom(_item):
        raise OSError("read-only file system")

    svc.inbox.add = boom  # type: ignore[method-assign]
    resp, body = await _post(_state(svc), {"text": "Something I cannot afford to lose"})

    assert resp.status == 500
    assert body["error"]["code"] == "note_not_saved"
    assert "still in the compose box" in body["error"]["message"]


@pytest.mark.asyncio
async def test_the_endpoint_has_no_write_path_of_its_own(tmp_path, monkeypatch):
    """One write path into the inbox, not two.

    Neutralising `emit_attention_item` must leave the endpoint writing NOTHING. If the handler
    also called `store.add` (or built its own `InboxStore`), a row would appear here — which is
    exactly the dual mechanism that drifts: the row and its notification stop being one event,
    and the store's dedup/id-shape/flush guarantees apply to one writer and not the other.
    """
    svc = _svc(tmp_path)
    monkeypatch.setattr("personalclaw.inbox.emit_attention_item", lambda *a, **k: "")

    resp, body = await _post(_state(svc), {"text": "Would a second path catch this?"})
    assert resp.status == 500 and body["error"]["code"] == "note_not_saved"
    assert svc.inbox.items == {}
    assert not (tmp_path / "inbox.json").exists()


# ── refusals, each with a user-actionable wire code ──


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   ", "\n\n\t ", None, 42, ["a"]])
async def test_a_note_with_nothing_in_it_is_refused(tmp_path, text):
    svc = _svc(tmp_path)
    resp, body = await _post(_state(svc), {"text": text})
    assert resp.status == 400
    assert body["error"]["code"] == "note_text_empty"
    assert svc.inbox.items == {}


@pytest.mark.asyncio
async def test_an_over_long_note_is_refused_with_the_REAL_count(tmp_path):
    """Refused, never truncated: quietly dropping the tail of someone's note is the one
    outcome worse than declining it. The message carries the actual count and the limit,
    which the registry's fixed sentence could not."""
    svc = _svc(tmp_path)
    over = "x" * (h._NOTE_MAX_CHARS + 1)
    resp, body = await _post(_state(svc), {"text": over})

    assert resp.status == 400
    assert body["error"]["code"] == "note_too_long"
    assert str(h._NOTE_MAX_CHARS + 1) in body["error"]["message"]
    assert str(h._NOTE_MAX_CHARS) in body["error"]["message"]
    assert svc.inbox.items == {}


@pytest.mark.asyncio
async def test_a_note_exactly_at_the_cap_is_accepted(tmp_path):
    """The boundary, so the cap is off-by-one-proof in the direction that loses data."""
    svc = _svc(tmp_path)
    resp, body = await _post(_state(svc), {"text": "y" * h._NOTE_MAX_CHARS})
    assert resp.status == 201
    assert len(body["item"]["message"]) == h._NOTE_MAX_CHARS


@pytest.mark.asyncio
async def test_a_malformed_body_is_refused_before_anything_is_written(tmp_path):
    svc = _svc(tmp_path)
    resp, body = await _post(_state(svc), None, raw="{not json")
    assert resp.status == 400 and body["error"]["code"] == "invalid_json"

    resp, body = await _post(_state(svc), ["text", "hello"])
    assert resp.status == 400 and body["error"]["code"] == "invalid_body"
    assert svc.inbox.items == {}


def test_every_wire_code_this_endpoint_emits_is_registered():
    """The rail in `test_http_error_codes_append_only` sweeps literal `json_error` codes
    tree-wide, but only a FULL suite run surfaces it. This is the same obligation asserted
    where this endpoint's own tests live, so a targeted run cannot miss it."""
    missing = [c for c in NOTE_CODES if c not in HTTP_ERROR_CODES]
    assert not missing, f"codes emitted with no user-actionable meaning: {missing}"
    for code in NOTE_CODES:
        meaning = HTTP_ERROR_CODES[code]
        assert meaning.strip().endswith("."), f"{code}: the meaning is copy, so it is a sentence"


# ── the notification target: registered, and NOT a phantom ──


def test_the_note_kind_is_registered_rather_than_falling_open_to_generic():
    """Unregistered, `resolve_kind` warns and returns system/generic on EVERY emission — so
    the kind would carry GENERIC's severity and mode, show no row in Settings →
    Notifications, and be grouped in the digest under whatever its bare string collides
    with. That is the 🪤 the registry's own comments record twice."""
    kind = nk.resolve_kind("user", "note")
    assert kind.key == "user/note"
    assert kind.attention is True
    # Round-trips, or the rule a user configures against it would silently do nothing.
    assert nk.kind_for_legacy_pair("user", "note") == "user_note"
    assert nk.kind_for_legacy("user_note").key == "user/note"


def test_the_note_kind_is_a_deliverable_target_not_a_phantom_source(tmp_path):
    """The failure mode this atom explicitly forbids: a source the rules vocabulary
    advertises but cannot deliver.

    Three halves, because two are not enough. It appears as a row in the matrix (so it is
    advertised), its default is the quiet mode (so it does not interrupt you about your own
    keystrokes), and `state.notify` is OBSERVED receiving its wire string on a real capture —
    which is the half that distinguishes "advertised and deliverable" from "advertised".
    """
    from personalclaw.notification_rules import rules_document

    row = next((r for r in rules_document()["rules"] if r["key"] == "user/note"), None)
    assert row is not None, "the kind must be configurable, or the user cannot change it"
    assert row["mode"] == "badge" and row["default_mode"] == "badge"
    assert row["label"] == "Note you captured"
    # `badge` persists and counts without a toast, so nothing is DECLINED — every other mode,
    # including `immediate`, is one click away in this same row.
    assert "immediate" in nk.MODES and "badge" in nk.MODES


@pytest.mark.asyncio
async def test_capturing_a_note_actually_delivers_through_notify(tmp_path):
    svc = _svc(tmp_path)
    state = _state(svc)
    _, body = await _post(state, {"text": "Reply to the landlord\nabout the boiler"})

    assert state.notify.call_count == 1
    wire, title, note_body = state.notify.call_args.args
    assert wire == "user_note", "an unregistered wire string resolves to system/generic"
    assert title == "Reply to the landlord"
    assert note_body == "about the boiler"
    assert state.notify.call_args.kwargs["meta"]["inbox_item"] == body["id"]
    assert state.notify.call_args.kwargs["meta"]["item_kind"] == ItemKind.USER_NOTE.value


def test_a_note_is_not_verifiable_so_the_skeptic_cannot_hide_it():
    """INU-6 files a REFUTED claim as FILTERED and withholds its notification. A note is not
    a claim about the world — it is what the user said — so a model that "refuted" one would
    hide the user's own words from them. `verifiable=False` is what makes the rules PUT
    refuse `verify:true` for this kind, so the hazard is unreachable, not merely unconfigured.
    """
    assert nk.resolve_kind("user", "note").verifiable is False


@pytest.mark.asyncio
async def test_a_new_note_is_pushed_so_an_open_dashboard_sees_it(tmp_path):
    """The tray can capture while the inbox page is already open in a browser; without the
    push the row would not appear until the next poll or reload."""
    svc = _svc(tmp_path)
    state = _state(svc)
    await _post(state, {"text": "Check the backup ran"})

    events = [c.args[0] for c in state.broadcast_ws.call_args_list]
    assert events == ["inbox_new_item"]
    assert state.broadcast_ws.call_args.args[1]["item_kind"] == ItemKind.USER_NOTE.value

"""The source→row item_kind seam (EIAT-6).

The defect this closes: the inbox shipped **live readers** for two kinds nothing could
ever write. ``inboxMeta.ts`` declares Mentions and Email chips, ``InboxPage`` filters on
``item_kind``, ``handlers_inbox`` filters and counts by it, and ``inbox.py`` persists it —
but ``IncomingMessage`` had no kind field at all, so every polled message became the
default ``message``. A mail source could not say "this is an email"; ``ItemKind.EMAIL`` and
``ItemKind.MENTION`` were unreachable by construction (both listed inert in
``inert-surface-baseline.json``).

So the tests here refuse to stop at the dataclass. Each round trip drives the REAL path —
a JSON batch dropped in ``<home>/inbox/incoming/`` → ``FilesystemSourceProvider.poll`` →
``InboxService._poll_once`` → the persisted row re-read from disk → ``GET /api/inbox`` —
and asserts the value the frontend filter actually compares (``it.item_kind`` against a
chip key). A unit test on the dataclass would have passed on the broken code.

The other half is containment: a source states its own kind, so a wrong or malicious value
must not become a row no chip can reach, and must not become one of core's own non-channel
attention kinds either. Refusal is asserted with the warning, because a *silent* fallback
to ``message`` is the very shape being fixed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from personalclaw.inbox import (
    NON_CHANNEL_KINDS,
    SOURCE_DECLARABLE_KINDS,
    InboxState,
    InboxStore,
    ItemKind,
)
from personalclaw.inbox_providers.base import IncomingMessage
from personalclaw.inbox_providers.filesystem_source import FilesystemSourceProvider
from personalclaw.inbox_service import InboxService, _resolve_source_kind

_REPO = Path(__file__).resolve().parent.parent
_INBOX_META = _REPO / "web" / "src" / "pages" / "inbox" / "inboxMeta.ts"


# ── the closed set ──


def test_source_declarable_kinds_is_exactly_the_channel_shaped_enum_members():
    """The allowlist is written out literally for legibility; this keeps it honest.

    A new ``ItemKind`` member must land on one side or the other deliberately — either a
    non-channel attention kind (core raises it via ``emit_attention_item``) or a kind a
    source may declare. Silence would mean a new channel-shaped kind that no source can
    ever set: the exact bug EIAT-6 exists to close.
    """
    every = {k.value for k in ItemKind}
    assert SOURCE_DECLARABLE_KINDS == every - NON_CHANNEL_KINDS
    assert SOURCE_DECLARABLE_KINDS == {"message", "mention", "email"}


def test_incoming_message_defaults_to_a_plain_message():
    msg = IncomingMessage(id="m1", channel_id="C1", channel_name="#ops")
    assert msg.kind == ItemKind.MESSAGE.value


@pytest.mark.parametrize("declared", sorted(SOURCE_DECLARABLE_KINDS))
def test_every_declarable_kind_resolves_to_itself(declared):
    assert _resolve_source_kind(declared, "filesystem") == declared


def test_unset_kind_resolves_to_message_without_complaint(caplog):
    """A source written before the field existed is not a misconfiguration."""
    with caplog.at_level("WARNING"):
        assert _resolve_source_kind("", "filesystem") == ItemKind.MESSAGE.value
    assert caplog.records == []


@pytest.mark.parametrize(
    "declared",
    ["mail", "Email", "EMAIL", "proposal", "needs_input", "system", "../../etc/passwd"],
)
def test_an_undeclarable_kind_is_refused_loudly_and_filed_as_message(declared, caplog):
    """Includes core's own non-channel kinds: a source claiming ``proposal`` would render a
    row with no refs, no deep-link and no reply — a dead row wearing a live kind's chip."""
    with caplog.at_level("WARNING"):
        assert _resolve_source_kind(declared, "mail-inbox") == ItemKind.MESSAGE.value
    assert len(caplog.records) == 1
    logged = caplog.records[0].getMessage()
    assert declared in logged and "mail-inbox" in logged


# ── the round trip: a source's declared kind → the value the FE filter compares ──


def _svc(tmp_path, monkeypatch, provider) -> InboxService:
    """A service on an isolated store/state, wired to *provider*.

    ``config_dir()`` is already redirected to a per-test tmp home by conftest's autouse
    isolation fixture, which is what makes the filesystem source's real ``inbox/incoming``
    directory safe to drive here.
    """
    from personalclaw import inbox_service as mod

    svc = InboxService(
        state=InboxState(tmp_path / "state.json"),
        store=InboxStore(tmp_path / "inbox.json"),
        provider=provider,
    )
    monkeypatch.setattr(InboxService, "_operator_name", staticmethod(lambda: ""))
    monkeypatch.setattr(mod, "_dashboard_state", lambda: None)
    return svc


def _drop_batch(*messages: dict) -> None:
    """Write a real incoming batch where the filesystem source polls it from."""
    from personalclaw.config.loader import config_dir

    incoming = config_dir() / "inbox" / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    (incoming / "batch.json").write_text(json.dumps({"messages": list(messages)}))


def _api_request(svc, query=None):
    from unittest.mock import MagicMock

    state = MagicMock()
    state._inbox_svc = svc  # the LIVE store the running gateway serves
    req = MagicMock()
    req.app = {"state": state}
    req.query = query or {}
    return req


async def _payload(resp):
    return json.loads(resp.body.decode())


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", ["email", "mention"])
async def test_declared_kind_survives_poll_persistence_and_the_api(declared, tmp_path, monkeypatch):
    from personalclaw.dashboard import handlers_inbox as h

    _drop_batch(
        {
            "id": "m1",
            "channel_id": "inbox@example.test",
            "channel_name": "mail",
            "text": "Invoice attached.",
            "sender_id": "billing@example.test",
            "sender_name": "Billing",
            "timestamp": 1700000000.5,
            "kind": declared,
        }
    )
    svc = _svc(tmp_path, monkeypatch, FilesystemSourceProvider())
    await svc._poll_once()

    # 1. persisted (a fresh store re-read from disk, not the in-memory instance)
    reloaded = InboxStore(tmp_path / "inbox.json")
    reloaded.load()
    assert [i.item_kind for i in reloaded.items.values()] == [declared]

    # 2. the unfiltered API row carries it, and the kind filter selects on it
    rows = await _payload(await h.api_inbox_list(_api_request(svc)))
    assert [r["item_kind"] for r in rows] == [declared]
    selected = await _payload(await h.api_inbox_list(_api_request(svc, {"kind": declared})))
    assert len(selected) == 1
    # This is the exact comparison InboxPage makes: (it.item_kind || 'message') === kind.
    assert (selected[0].get("item_kind") or "message") == declared
    assert await _payload(await h.api_inbox_list(_api_request(svc, {"kind": "message"}))) == []

    # 3. the chip the frontend builds from what's present now exists for this kind
    chips = (await _payload(await h.api_inbox_kinds(_api_request(svc))))["kinds"]
    assert [(c["kind"], c["open"], c["channel"]) for c in chips] == [(declared, 1, True)]


@pytest.mark.asyncio
async def test_a_source_declaring_nothing_still_lands_as_a_message(tmp_path, monkeypatch):
    """The pre-EIAT-6 behavior, unchanged — the seam is additive for existing sources."""
    from personalclaw.dashboard import handlers_inbox as h

    _drop_batch({"id": "m1", "channel_id": "C1", "channel_name": "#ops", "timestamp": 1.0})
    svc = _svc(tmp_path, monkeypatch, FilesystemSourceProvider())
    await svc._poll_once()

    rows = await _payload(await h.api_inbox_list(_api_request(svc, {"kind": "message"})))
    assert [r["item_kind"] for r in rows] == [ItemKind.MESSAGE.value]


@pytest.mark.asyncio
async def test_a_lying_source_cannot_produce_a_row_no_chip_can_reach(tmp_path, monkeypatch):
    """A refused kind loses the CLAIM, never the message: the row arrives, filed as
    ``message``, reachable behind the Messages chip like any other channel row."""
    from personalclaw.dashboard import handlers_inbox as h

    _drop_batch(
        {"id": "m1", "channel_id": "C1", "channel_name": "#ops", "timestamp": 1.0, "kind": "mail"},
        {
            "id": "m2",
            "channel_id": "C1",
            "channel_name": "#ops",
            "timestamp": 2.0,
            "kind": "proposal",
        },
    )
    svc = _svc(tmp_path, monkeypatch, FilesystemSourceProvider())
    await svc._poll_once()

    rows = await _payload(await h.api_inbox_list(_api_request(svc)))
    assert len(rows) == 2
    assert {r["item_kind"] for r in rows} == {ItemKind.MESSAGE.value}
    assert await _payload(await h.api_inbox_list(_api_request(svc, {"kind": "proposal"}))) == []


# ── the reader half: the frontend can render everything the seam can now write ──


@pytest.mark.skipif(not _INBOX_META.exists(), reason="web sources not present")
def test_every_declarable_kind_has_a_frontend_chip():
    """Parity with ``ITEM_KINDS`` in ``inboxMeta.ts``.

    Backend and frontend are the two ends of one closed enum: a kind a source may declare
    but the dashboard has no label/icon for would render through ``kindMeta``'s fallback as
    "Messages", mislabelling the row it just filtered.
    """
    text = _INBOX_META.read_text(encoding="utf-8")
    block = re.search(r"ITEM_KINDS:\s*KindMeta\[\]\s*=\s*\[(.*?)\n\]", text, re.S)
    assert block, "could not locate the ITEM_KINDS array in inboxMeta.ts"
    chips = set(re.findall(r"key:\s*'([^']+)'", block.group(1)))
    assert chips, "parsed ITEM_KINDS as empty — the regex drifted, not the data"
    assert chips == {k.value for k in ItemKind}
    assert SOURCE_DECLARABLE_KINDS <= chips

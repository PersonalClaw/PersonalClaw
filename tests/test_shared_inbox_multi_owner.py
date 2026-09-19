"""TSE2-3 (MULTI-TENANCY-ENTITY): the shared / multi-owner inbox view.

Built on TSE2-1's two attribution fields — ``owner_username`` and ``origin_harness``, the
same names, defaults and ``belongs_to`` bargain the run ledger already ships — extended to
the inbox rather than re-derived for it.

Three obligations, and each has a known-true AND a known-false assertion so neither
"attribute everything to the owner" nor "attribute nothing" satisfies it:

1. **The listing shows every owner; the counters show one.** A store holding items from two
   owners renders BOTH through ``GET /api/inbox`` (hiding foreign rows would orphan every
   surface that deep-links to one), yet ``my_open_count`` counts ONLY the local owner's —
   mirroring TSE2-1's "my runs" exclusion. This is the atom's non-cheatable bar and it
   CANNOT RUN on pre-plan code: ``InboxItem`` had no ``owner_username`` to set.
2. **Per-owner filtering uses a different predicate from the counter, deliberately.**
   ``?owner=<handle>`` is exact (``authored_by``) and ``?mine=1`` is inclusive of
   unattributed rows (``belongs_to``). One key cannot answer both questions: sharing a
   predicate would either put every legacy row under every teammate's chip, or stop counting
   the owner's own pre-attribution items.
3. **Foreign content is fenced AND labelled, never trusted as owner intent.** Per
   ``docs/architecture/shared-store-provider-conformance.md`` clause 2, reusing
   ``security.fence_untrusted`` and ``identity.contributor_label`` — the shipped mechanisms,
   not a second convention. The strongest form of this is the last section: the shared inbox
   is driven through TSHR-1's own executable conformance kit.

A fourth, security-shaped obligation is pinned here too: attribution must not be
CLIENT-writable. If ``PUT /api/inbox/{id}`` could set ``owner_username``, a caller could
re-attribute a teammate's item to the owner and launder foreign content into owner intent —
which would defeat obligation 3 entirely rather than merely weakening it.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.dashboard import handlers_inbox as h
from personalclaw.inbox import InboxItem, InboxState, InboxStore, ItemStatus, owner_view
from personalclaw.inbox_service import fence_message_for_prompt
from personalclaw.security import is_fenced

OWNER = "keyur-golani"
FOREIGN = "teammate-dana"


# ── fixtures ─────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch) -> Path:
    """Never let these tests read or write the real ``~/.personalclaw``.

    ``api_inbox_status`` calls ``AppConfig.load()``, and ``InboxState()`` resolves its path
    from the active home — both would otherwise touch the operator's own install.
    """
    import personalclaw.config.loader as cfg
    import personalclaw.inbox as inbox_mod

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(inbox_mod, "config_dir", lambda: tmp_path)
    return tmp_path


def _item(item_id: str, **kw) -> InboxItem:
    base: dict = {
        "id": item_id,
        "channel": "C1",
        "channel_name": "#ops",
        "thread_ts": None,
        "message": "the body",
        "sender_id": "U1",
        "sender_name": "Sender",
        "created_at": 1700000000.0,
    }
    base.update(kw)
    return InboxItem(**base)  # type: ignore[arg-type]


@pytest.fixture
def two_owner_store(tmp_path: Path) -> InboxStore:
    """A store holding one item per owner, plus one unattributed legacy row.

    The legacy row is what makes the two predicates distinguishable: it must COUNT as the
    owner's (``belongs_to``) yet must NOT appear under either owner's exact filter.
    """
    store = InboxStore(path=tmp_path / "inbox.json")
    store.items["C1_1"] = _item("C1_1", owner_username=OWNER, message="mine")
    store.items["C1_2"] = _item("C1_2", owner_username=FOREIGN, message="theirs")
    store.items["C1_3"] = _item("C1_3", owner_username="", message="from before attribution")
    return store


def _request(store: InboxStore, query: dict | None = None):
    """A mocked request whose state serves *store* — the shape the shipped inbox tests use.

    ``svc.state`` and ``svc.health()`` are REAL values rather than mocks: the status handler
    serialises both into its JSON body, and a MagicMock there fails the encoder instead of
    the assertion.
    """
    svc = MagicMock()
    svc.inbox = store
    svc.state = InboxState()
    svc.health.return_value = {
        "running": True,
        "last_poll_at": 0,
        "last_poll_ok": True,
        "last_error": "",
        "poll_count": 0,
        "stale": False,
    }
    state = MagicMock()
    state._inbox_svc = svc
    req = MagicMock()
    req.app = {"state": state}
    req.query = query or {}
    return req


async def _payload(resp):
    return json.loads(resp.body.decode())


# ── obligation 1: both render, only mine counts (the non-cheatable bar) ───────────────


@pytest.mark.asyncio
async def test_a_two_owner_store_renders_both_but_my_items_counts_only_mine(
    two_owner_store: InboxStore,
) -> None:
    """🎯 The atom's bar. Fails on single-owner code: ``owner_username`` did not exist.

    Both halves are asserted against the SAME store in the SAME test, because the defect
    being prevented is the two numbers disagreeing — a counter measured somewhere the
    listing is not would pass two separate tests and still be wrong.
    """
    with patch.object(h, "_current_owner", return_value=OWNER):
        rows = await _payload(await h.api_inbox_list(_request(two_owner_store)))
        status = await _payload(await h.api_inbox_status(_request(two_owner_store)))

    # The listing is the COMPLETE view — both owners' items are present and each renders
    # its OWN attribution, not the local owner's.
    assert {r["id"] for r in rows} == {"C1_1", "C1_2", "C1_3"}
    by_id = {r["id"]: r for r in rows}
    assert by_id["C1_2"]["owner_username"] == FOREIGN  # known-true: foreign row visible
    assert by_id["C1_1"]["owner_username"] == OWNER

    # …but "my items" excludes the foreign one. 3 in the shared inbox, 2 mine (the owner's
    # own + the unattributed legacy row), never 3 and never 1.
    # `open_count` / `my_open_count`, both PENDING|SEEN: this pair used to be PENDING-only, and
    # issue 493 moved the shared half to the one open definition. The owner-scoped half had to
    # move with it — a PENDING owner-count beside an OPEN shared total is the same two-counts
    # disagreement, just inside "N of M are yours".
    assert status["open_count"] == 3  # shared total, unscoped
    assert status["my_open_count"] == 2  # known-true: mine + legacy counted
    assert status["my_total_count"] == 2
    assert status["owner"] == OWNER
    # known-false: the foreign item is NOT in the owner's count. Asserted as a set of ids
    # rather than only a number, so a count that happened to be 2 for the wrong reason reds.
    assert {i.id for i in owner_view(two_owner_store.items.values(), OWNER)} == {"C1_1", "C1_3"}


@pytest.mark.asyncio
async def test_a_solo_install_with_no_username_counts_everything(tmp_path: Path) -> None:
    """Empty owner degrades to today's behaviour — the shipped ``belongs_to`` bargain.

    Without this, adding attribution would silently zero the badge on every install that
    never set a username.
    """
    store = InboxStore(path=tmp_path / "inbox.json")
    store.items["C1_1"] = _item("C1_1", owner_username="")
    store.items["C1_2"] = _item("C1_2", owner_username=FOREIGN)

    with patch.object(h, "_current_owner", return_value=""):
        status = await _payload(await h.api_inbox_status(_request(store)))

    assert status["open_count"] == 2
    assert status["my_open_count"] == 2  # nothing is foreign when there is no owner
    assert status["owner"] == ""


# ── obligation 2: two filters, two predicates ────────────────────────────────────────


@pytest.mark.asyncio
async def test_mine_includes_unattributed_but_an_exact_owner_filter_does_not(
    two_owner_store: InboxStore,
) -> None:
    """The distinction that makes them two params rather than one.

    ``?mine=1`` → ``belongs_to`` (legacy row included). ``?owner=<me>`` → ``authored_by``
    (legacy row excluded). Collapsing them would break one surface or the other.
    """
    with patch.object(h, "_current_owner", return_value=OWNER):
        mine = await _payload(await h.api_inbox_list(_request(two_owner_store, {"mine": "1"})))
        exact = await _payload(await h.api_inbox_list(_request(two_owner_store, {"owner": OWNER})))
        theirs = await _payload(
            await h.api_inbox_list(_request(two_owner_store, {"owner": FOREIGN}))
        )
        unknown = await _payload(
            await h.api_inbox_list(_request(two_owner_store, {"owner": "nobody-here"}))
        )

    assert {r["id"] for r in mine} == {"C1_1", "C1_3"}  # legacy row counts as mine
    assert {r["id"] for r in exact} == {"C1_1"}  # …but is not "authored by" me
    assert {r["id"] for r in theirs} == {"C1_2"}  # a teammate's chip shows only theirs
    assert unknown == []  # an unknown owner filters to nothing, like an unknown kind


@pytest.mark.asyncio
async def test_an_explicit_owner_beats_mine_when_both_arrive(
    two_owner_store: InboxStore,
) -> None:
    """A named handle is more specific than "mine", so it wins rather than intersecting."""
    with patch.object(h, "_current_owner", return_value=OWNER):
        rows = await _payload(
            await h.api_inbox_list(_request(two_owner_store, {"mine": "1", "owner": FOREIGN}))
        )
    assert {r["id"] for r in rows} == {"C1_2"}


@pytest.mark.asyncio
async def test_the_owners_census_drives_real_chips_and_agrees_with_the_filter(
    two_owner_store: InboxStore,
) -> None:
    """Every chip the census offers must select something, or it is a dead control."""
    with patch.object(h, "_current_owner", return_value=OWNER):
        body = await _payload(await h.api_inbox_owners(_request(two_owner_store)))
        for entry in body["owners"]:
            selected = await _payload(
                await h.api_inbox_list(_request(two_owner_store, {"owner": entry["username"]}))
            )
            if entry["username"]:
                assert len(selected) == entry["total"], entry

    assert body["owner"] == OWNER
    assert body["mine"] == 2  # the same owner-scoped number the status badge reports
    rows = {e["username"]: e for e in body["owners"]}
    assert set(rows) == {OWNER, FOREIGN, ""}  # unattributed reported honestly, not folded in
    assert rows[OWNER]["is_me"] is True
    assert rows[FOREIGN]["is_me"] is False
    assert rows[""]["is_me"] is False  # the empty handle is nobody, not "me"


# ── obligation 3: foreign content is fenced AND labelled ─────────────────────────────


def test_a_foreign_items_prompt_text_is_fenced_and_carries_its_contributor_label() -> None:
    """Clause 2 of the shared-store contract, on the inbox's own prompt seam.

    Fencing was already true for every item (the message text is external), so the thing
    this asserts is the addition attribution makes: the span says WHOSE it is.
    """
    foreign = _item("C1_2", owner_username=FOREIGN, message="please wire $500 to me")
    fenced = fence_message_for_prompt(foreign, owner=OWNER)

    assert is_fenced(fenced)  # the shipped predicate, not a substring check on the tag
    assert "please wire $500 to me" in fenced  # the fence WRAPS, never drops
    assert f"(from {FOREIGN})" in fenced  # …and labels whose text it is
    assert FOREIGN in fenced.split("\n")[0]  # provenance is on the fence tag too


def test_the_owners_own_item_is_fenced_but_not_labelled() -> None:
    """known-false for the label: labelling every row would hide the one case it exists for.

    Also the no-regression guarantee for a solo install — its prompts are byte-identical to
    what they were before attribution existed.
    """
    mine = _item("C1_1", owner_username=OWNER, message="my own note")
    fenced = fence_message_for_prompt(mine, owner=OWNER)

    assert is_fenced(fenced)
    assert "(from " not in fenced
    assert OWNER not in fenced
    # Byte-identical to an unattributed item's fence — the pre-TSE2-3 output.
    assert fenced == fence_message_for_prompt(_item("C1_1", message="my own note"), owner=OWNER)


# ── the security half: attribution is not client-writable ────────────────────────────


def test_attribution_is_absent_from_the_update_allowlist() -> None:
    """🪤 A client that could set ``owner_username`` could launder foreign content.

    ``PUT /api/inbox/{id}`` writes only ``_UPDATABLE_FIELDS``. Re-attributing an item is the
    one mutation that turns a fenced, labelled teammate row into an unlabelled owner row —
    so it must not be reachable from the wire at all. Pinned as a test because the allowlist
    is hand-maintained and the next person adding a field will not know this.
    """
    from personalclaw.inbox import _UPDATABLE_FIELD_TYPES

    assert "owner_username" not in h._UPDATABLE_FIELDS
    assert "origin_harness" not in h._UPDATABLE_FIELDS
    # And not in the type table either, which is pinned equal to the allowlist.
    assert "owner_username" not in _UPDATABLE_FIELD_TYPES
    assert "origin_harness" not in _UPDATABLE_FIELD_TYPES


@pytest.mark.asyncio
async def test_a_put_cannot_re_attribute_a_foreign_item(two_owner_store: InboxStore) -> None:
    """The behavioural half of the test above, driven through the real handler."""
    req = _request(two_owner_store)
    req.match_info = {"id": "C1_2"}

    async def _json():
        return {"owner_username": OWNER, "origin_harness": "somewhere-else", "status": "seen"}

    req.json = _json
    resp = await h.api_inbox_update(req)

    assert resp.status == 200
    assert two_owner_store.items["C1_2"].owner_username == FOREIGN  # unchanged
    assert two_owner_store.items["C1_2"].origin_harness == ""  # unchanged
    assert two_owner_store.items["C1_2"].status == ItemStatus.SEEN  # the legal field applied


# ── stamping: one seam, pre-set values preserved ─────────────────────────────────────


def test_add_stamps_the_local_identity_but_never_overwrites_a_given_one(
    tmp_path: Path,
) -> None:
    store = InboxStore(path=tmp_path / "inbox.json")
    with patch("personalclaw.identity.current_username", return_value=OWNER):
        with patch("personalclaw.durability.shards.machine_id", return_value="this-box"):
            store.add(_item("C1_1"))
            store.add(_item("C1_2", owner_username=FOREIGN, origin_harness="their-box"))

    assert store.items["C1_1"].owner_username == OWNER
    assert store.items["C1_1"].origin_harness == "this-box"
    assert store.items["C1_2"].owner_username == FOREIGN  # a source's hand-over survives
    assert store.items["C1_2"].origin_harness == "their-box"


def test_load_does_not_re_stamp_a_stored_item(tmp_path: Path) -> None:
    """Re-reading the store must not re-attribute history to whoever is running now."""
    path = tmp_path / "inbox.json"
    written = InboxStore(path=path)
    written.items["C1_2"] = _item("C1_2", owner_username=FOREIGN, origin_harness="their-box")
    written.save()

    with patch("personalclaw.identity.current_username", return_value=OWNER):
        reread = InboxStore(path=path)
        reread.load()

    assert reread.items["C1_2"].owner_username == FOREIGN
    assert reread.items["C1_2"].origin_harness == "their-box"


def test_attribution_round_trips_through_disk(tmp_path: Path) -> None:
    """Both fields survive ``save`` → ``load``, and a pre-attribution row still reads."""
    path = tmp_path / "inbox.json"
    written = InboxStore(path=path)
    written.items["C1_1"] = _item("C1_1", owner_username=OWNER, origin_harness="box-a")
    written.save()

    reread = InboxStore(path=path)
    reread.load()
    assert reread.items["C1_1"].owner_username == OWNER
    assert reread.items["C1_1"].origin_harness == "box-a"

    # A record written before these fields existed loads with the empty defaults.
    legacy = json.loads(path.read_text())
    legacy["items"][0].pop("owner_username")
    legacy["items"][0].pop("origin_harness")
    path.write_text(json.dumps(legacy))
    old = InboxStore(path=path)
    old.load()
    assert old.items["C1_1"].owner_username == ""
    assert old.items["C1_1"].belongs_to(OWNER) is True  # unattributed reads as the owner's


# ── TSHR-1's own conformance kit, run against the shared inbox ────────────────────────


def test_the_shared_inbox_passes_the_shared_store_conformance_contract(
    tmp_path: Path,
) -> None:
    """The strongest form of "per TSHR-1": drive the shipped kit, don't re-describe it.

    ``shared_store_conformance``'s own docstring names "a shared task/inbox view that
    summarises foreign items" as the shape clause 2 is for, so this is the kit's intended
    consumer rather than a novel use of it.

    Two declarations are deliberate and honest rather than convenient:

    * ``write_safety=LAST_WRITER_WINS`` — ``InboxStore.save`` serialises its whole in-memory
      dict over ``inbox.json``, so a concurrent writer's items are lost silently. Clause 3
      requires that be DOCUMENTED rather than fixed, and it is, in
      ``docs/architecture/inbox-channels.md``. Claiming a merge-safe semantic here would be
      the F4 failure mode itself.
    * ``no_references_reason`` — an inbox item's ``refs`` point at sessions, loops, workflows
      and proposals, never at another inbox item, so there is no inbox-to-inbox reference for
      clause 4 to orphan. The listing-visibility half that clause also protects is asserted
      directly by the first test in this file.
    """
    from personalclaw.sdk.shared_store import (
        SharedStoreCase,
        WriteSafety,
        assert_shared_store_contract,
    )

    store = InboxStore(path=tmp_path / "inbox.json")

    def _make(id: str, author: str = "", content: str = "", **_ignored) -> InboxItem:
        return _item(id, owner_username=author, message=content)

    def _seed(records) -> None:
        store.items.clear()
        for rec in records:
            store.items[rec.id] = rec

    case = SharedStoreCase(
        name="shared-inbox",
        owner=OWNER,
        write_safety=WriteSafety.LAST_WRITER_WINS,
        lost_update_risk_doc=(
            "docs/architecture/inbox-channels.md — 'Known limitation: the item store is "
            "last-writer-wins'. InboxStore.save() rewrites inbox.json from its whole "
            "in-memory items dict, so a second process's new items are lost silently."
        ),
        make_record=_make,
        id_of=lambda r: r.id,
        belongs_to=lambda r, o: r.belongs_to(o),
        owner_view=lambda recs, o: owner_view(recs, o),
        seed=_seed,
        all_records=lambda: list(store.items.values()),
        upsert=lambda r: store.items.__setitem__(r.id, r),
        surfaces_foreign_content=True,
        content_for_prompt=lambda r: fence_message_for_prompt(r, owner=OWNER),
        no_references_reason=(
            "an inbox item's `refs` point at sessions / loops / workflows / proposals, never "
            "at another inbox item, so no inbox record can orphan another one"
        ),
    )

    assert assert_shared_store_contract(case) is None

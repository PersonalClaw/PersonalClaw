"""Generating a channel digest is a POST, and each one is distinct (issue 337).

`/api/inbox/digest` was registered with `add_get`, and the handler behind it is not a read:
`InboxService.generate_digest` spends a model call (`one_shot_completion`) and then
`self.inbox.add(item)` + `flush()`. So a browser prefetch, a client retry, or a double render
each manufactured another digest item and repeated a paid call.

ARCC was queried first (a state-changing verb touching user data and paid inference is a
trigger domain). Two documents apply:

* the HTTP-verbs implementation — *"GET/HEAD methods are idempotent in operation, meaning that
  issuing the GET request multiple times SHOULD NOT modify any data stored server side"*, with
  the API table assigning "insert a new object" to POST. That article carries a DEPRECATION
  notice and is cited here only for HTTP semantics, not for the security claim.
* the CSRF guidance (not deprecated) — *"Do not use GET request for state changing
  operations"*, and its Get-based-CSRF section: a mutation reachable by GET *"causes any form
  of CSRF validations to be skipped"*. That is the sharper reason. This gateway can be bound
  to a private network (`PERSONALCLAW_BYPASS_LOCAL_NETWORKS`), so a state-changing GET was
  triggerable by any page the user happened to visit.

A SECOND defect, found while measuring the first: the id was `{channel}_digest_{int(ts)}` —
second-granular — and `InboxStore.items` is keyed by id. Measured directly: two adds in the
same second left ONE item, silently discarding a paid model call's output. Fixed by putting a
uuid8 BEFORE the timestamp, which keeps the `ts` rsplit contract and matches the id shape the
Inbox-Unification plan documents.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from personalclaw.inbox import InboxItem, InboxStore
from personalclaw.inbox_service import digest_item_id

REPO = Path(__file__).resolve().parents[1]


# ── the verb ─────────────────────────────────────────────────────────────────────────────


def test_the_digest_route_is_registered_as_post_not_get():
    """🔑 The defect. Asserted against the router registration rather than a mounted app so
    the test names the one thing that changed and cannot pass for an unrelated reason."""
    src = (REPO / "src" / "personalclaw" / "dashboard" / "server.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    assert 'add_post("/api/inbox/digest"' in code
    assert (
        'add_get("/api/inbox/digest"' not in code
    ), "the digest endpoint is a GET again — a prefetch or retry manufactures items"


def test_no_route_that_creates_an_inbox_item_is_a_get():
    """🪤 The rail, stated as the property rather than as this one route.

    Every inbox route that CREATES something is a POST. `digest` was the one exception, and it
    was the expensive one. A future author adding a generate-shaped endpoint has to break this
    to register it as a GET.
    """
    src = (REPO / "src" / "personalclaw" / "dashboard" / "server.py").read_text(encoding="utf-8")
    creating = ("digest", "draft", "apply", "restore", "send", "notes")
    gets = set(re.findall(r'add_get\("(/api/inbox[^"]*)"', src))
    offenders = sorted(p for p in gets if any(seg in p for seg in creating))
    assert offenders == [], f"these inbox routes create state behind a GET: {offenders}"


def test_the_frontend_calls_it_with_post():
    """The verb change is only real end to end. A `get()` here against a POST-only route is a
    405 the user sees as "digest failed", so the two sides have to move together."""
    api_ts = (REPO / "web" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
    call = re.search(r"digestInboxChannel:.*?\n.*?\n", api_ts, re.S)
    assert call, "digestInboxChannel disappeared from the API client"
    assert "post<InboxItem>" in call.group(
        0
    ), f"the frontend still GETs the digest: {call.group(0)}"


# ── the id collision ─────────────────────────────────────────────────────────────────────


def test_two_digests_in_the_same_second_are_two_items():
    """🔑 The second defect, measured the way it was found: `InboxStore.items` is keyed by id,
    so a second-granular id made the second digest REPLACE the first — no error, and a paid
    model call's output gone."""
    store = InboxStore(path=Path("/dev/null"))
    ids = {_digest_id("c1") for _ in range(2)}
    assert len(ids) == 2, f"two digests generated in the same second share an id: {ids}"
    for i in ids:
        store.add(_item(i))
    assert len(store.items) == 2, "the second digest replaced the first"


def test_the_id_still_ends_in_the_timestamp():
    """🪤 The contract the uuid had to be inserted AROUND, not appended to.

    `InboxItem.ts` is `id.rsplit("_", 1)[-1]`, and the Inbox-Unification plan calls that out
    explicitly ("keeps the `ts` rsplit contract"). A uuid appended at the END would have made
    every digest's `ts` a hex string — which sorts and renders as garbage rather than failing
    loudly.
    """
    before = int(time.time())
    item = _item(_digest_id("c1"))
    after = int(time.time())
    assert item.ts.isdigit(), f"the id no longer ends in the timestamp: {item.id}"
    assert before <= int(item.ts) <= after


def test_the_id_still_names_its_channel_and_kind():
    """The prefix is what the channel filter and the `source="digest"` reader match on."""
    got = _digest_id("chan-42")
    assert got.startswith("chan-42_digest_")


def test_generate_digest_builds_its_id_through_the_same_function():
    """🪤 What makes every id test above behavioural rather than a copy.

    The first version of this file mirrored the production f-string in a local helper. Mutation
    proved the cost: reverting the real id to `{channel}_digest_{int(ts)}` left every
    "behavioural" assertion GREEN, because they were exercising the mirror. So the id moved
    into `digest_item_id` and the tests call that; this asserts `generate_digest` still does
    too, which is the only remaining way the two could drift.
    """
    svc = (REPO / "src" / "personalclaw" / "inbox_service.py").read_text(encoding="utf-8")
    assert "id=digest_item_id(channel_id, ts)" in svc, (
        "generate_digest no longer builds its id through digest_item_id — the tests above are "
        "testing a function the production path does not use"
    )


# ── helpers ──────────────────────────────────────────────────────────────────────────────


def _digest_id(channel_id: str) -> str:
    """The REAL production id builder — not a copy of it."""
    return digest_item_id(channel_id, time.time())


def _item(item_id: str) -> InboxItem:
    return InboxItem(
        id=item_id,
        channel="c1",
        channel_name="general",
        thread_ts=None,
        message="summary",
        sender_id="",
        sender_name="Digest · last 4h",
        source="digest",
        can_reply=False,
    )

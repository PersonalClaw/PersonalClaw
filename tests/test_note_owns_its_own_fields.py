"""Caller-supplied `meta` cannot overwrite the fields the platform decided (issue 423).

`DashboardState.notify` built the note and then merged meta OVER it:

    note = {"kind": kind, "title": title, "body": body, "ts": ...}
    if meta:
        note.update(meta)          # <-- meta wins

`kind` is the worst of the four. `notification_allowed(kind)` is evaluated on the PARAMETER at the
top of the method, so an emitter could pass the gate as one kind and be persisted, broadcast and
rule-matched as another — the severity downgrade the issue names. `acked` is next: `notify` never
writes it (the ack path in `handlers/messaging.py` does), so a note carrying `acked: True` on
arrival is one the user never sees.

This is a trust boundary, not a hypothetical: `notify`'s own docstring calls itself "THE single
delivery choke point for every emitter (crons, loops, hooks, inbox alerts, heartbeats, **app
actions**)", and an app bundle is third-party code. `meta` is the one part of a note a caller
controls.

The fix puts meta in FIRST and assigns the platform's fields after, so authority is structural: a
field `notify` starts setting later is protected by the assignment itself, with no list to remember.
`_RESERVED_NOTE_KEYS` covers only what that cannot reach — the fields written further down under a
condition, plus `acked`.

Measured before the fix: `meta={"kind": "info"}` on a `chat.error` notification produced a persisted
note whose `kind` was `info`.

ARCC's input-validation guidance is the direction taken here — *"Always use an allowlisting approach
over a denylisting approach"* and *"only data fitting specific, approved criteria is processed"*.
"""

from __future__ import annotations

from typing import Any

import pytest

from personalclaw.dashboard.state import DashboardState


@pytest.fixture
def state(monkeypatch, tmp_path):
    """A state whose delivery is captured rather than broadcast or written to a real home."""
    st = DashboardState.__new__(DashboardState)
    st._notification_log = []
    st._sessions = {}
    broadcast: list[dict[str, Any]] = []
    persisted: list[dict[str, Any]] = []
    monkeypatch.setattr(st, "_broadcast", lambda note: broadcast.append(note), raising=False)
    monkeypatch.setattr(
        "personalclaw.dashboard.state._persist_notification", lambda note: persisted.append(note)
    )
    monkeypatch.setattr("personalclaw.identity.operator_name", lambda: "")
    monkeypatch.setattr(st, "_push_target", lambda kind, note: None, raising=False)
    st.captured = {"broadcast": broadcast, "persisted": persisted}
    return st


def _deliver(state: DashboardState, kind: str, meta: dict[str, Any] | None) -> dict[str, Any]:
    state.notify(kind, "Title", "Body", meta=meta)
    notes = state.captured["broadcast"] or state._notification_log
    assert notes, "nothing was delivered — the fixture stopped exercising the delivery path"
    return notes[-1]


# ── the four fields the platform decides ─────────────────────────────────────────────────────


def test_meta_cannot_change_the_kind_the_gate_already_evaluated():
    """🔑 The severity downgrade. `notification_allowed(kind)` ran on the parameter, so a note
    persisted under a different kind was admitted by one policy and recorded as another."""
    st = _fresh()
    note = _deliver(st, "chat.error", {"kind": "info"})
    assert note["kind"] == "chat.error"


def test_meta_cannot_rewrite_the_title_or_body():
    """The text is what the user reads and what the rule's keyword conditions match on
    (`rule.conditions.matches(f"{title}\\n{body}", ...)`), so an override changes both what is
    shown and whether an escalation fires."""
    st = _fresh()
    note = _deliver(st, "info", {"title": "spoofed", "body": "spoofed"})
    assert note["title"] == "Title"
    assert note["body"] == "Body"


def test_meta_cannot_backdate_the_timestamp():
    """`ts` orders the bell and drives expiry. A caller-set one sorts a fresh note into history."""
    st = _fresh()
    note = _deliver(st, "info", {"ts": "1999-01-01T00:00:00+00:00"})
    assert not note["ts"].startswith("1999")


def test_meta_cannot_pre_acknowledge_a_note():
    """🪤 `notify` never writes `acked`, so the authority-last assignment cannot protect it — it
    needs the reserved set. A note that arrives acknowledged is one the user never sees."""
    st = _fresh()
    note = _deliver(st, "info", {"acked": True})
    assert "acked" not in note


# ── the fields written further down, under a condition ───────────────────────────────────────


def test_meta_cannot_name_its_own_source():
    """`source` is the surface a consumer deep-links to, and the comment at its assignment says the
    RULE owns it. It is assigned only when a rule resolves, so a caller's value would survive on any
    branch that did not — which is exactly why it is in the reserved set rather than relying on the
    assignment.

    Two real emitters DO pass `source` in meta today (`inbound/bridge.py`,
    `knowledge/source_digest.py`). Their value was already overwritten whenever a rule resolved, so
    this changes nothing on the normal path; on the rule-unresolved path the note now carries no
    source rather than the caller's. That is the deliberate consequence: an unresolved rule has no
    authoritative source, and a spoofable one is worse than an absent one.
    """
    st = _fresh()
    note = _deliver(st, "info", {"source": "spoofed"})
    assert note.get("source") != "spoofed"


@pytest.mark.parametrize("key", ["mode", "targets", "escalated_by", "badge_only", "native"])
def test_meta_cannot_set_any_delivery_decision(key):
    """Each of these is a delivery decision the rule layer makes. `mode` is the sharpest: setting it
    to `never` from meta would let an emitter silence its own note after the gate admitted it."""
    st = _fresh()
    note = _deliver(st, "info", {key: "spoofed"})
    assert note.get(key) != "spoofed"


@pytest.mark.parametrize("key", ["mode", "targets", "source"])
def test_meta_cannot_set_a_delivery_decision_when_THE_RULE_FAILS_TO_RESOLVE(key, monkeypatch):
    """🪤 The branch that makes reserving these three worth anything.

    On the normal path `mode`/`targets`/`source` are assigned after the merge, so they are already
    safe and the test above passes with the reserved set EMPTY — measured, by mutating it to
    `set()`. Their entry in the set only earns its place on the path where rule resolution raises:
    `notify` catches that, sets `rule = None`, and then skips the `targets`/`source` assignment
    entirely, leaving whatever the caller supplied.

    Which is the more dangerous branch, not the less: `notify` fails open by design ("a policy layer
    that can't read its own config must not be able to silence the system"), so a broken rules file
    is exactly when an emitter's spoofed `source` would survive to a consumer's deep link.
    """
    import personalclaw.notification_rules as rules

    def _boom(_kind):
        raise RuntimeError("rules file unreadable")

    monkeypatch.setattr(rules, "resolve_rule_for_legacy", _boom)
    note = _deliver(_fresh(), "info", {key: "spoofed"})
    assert note.get(key) != "spoofed"
    assert note["mode"] == "immediate", "the fail-open default changed"


# ── what a caller CAN still do ───────────────────────────────────────────────────────────────


def test_ordinary_meta_still_rides_along():
    """Vacuity floor, and the whole point of `meta`. A fix that dropped everything would break the
    push target, which reads `item_id`/`inbox_item`/`session` out of exactly this dict."""
    st = _fresh()
    note = _deliver(st, "info", {"item_id": "abc123", "count": 7})
    assert note["item_id"] == "abc123"
    assert note["count"] == 7


def test_the_push_item_keys_are_all_carriable():
    """Named explicitly because `_PUSH_ITEM_KEYS` is the one documented contract on meta's contents:
    "the push payload carries the id and NOTHING else". Reserving one of them by accident would make
    a phone ping unable to open the thing it is about.
    """
    for key in DashboardState._PUSH_ITEM_KEYS:
        note = _deliver(_fresh(), "info", {key: "id-1"})
        assert note[key] == "id-1", key
    assert not set(DashboardState._PUSH_ITEM_KEYS) & DashboardState._RESERVED_NOTE_KEYS


def test_no_meta_at_all_is_unchanged():
    st = _fresh()
    note = _deliver(st, "info", None)
    assert note["kind"] == "info" and note["title"] == "Title"


# ── the rail ─────────────────────────────────────────────────────────────────────────────────


def test_every_field_notify_sets_is_protected_one_way_or_the_other():
    """🪤 What stops this returning: every field `notify` writes is either in the UNCONDITIONAL
    base assignment or in `_RESERVED_NOTE_KEYS`.

    That is deliberately stricter than "is it reachable by a caller". `note["mode"] = mode` is
    unconditional too, so reserving `mode` changes no behaviour — measured, by removing it and
    watching every behavioural test still pass. It stays because the invariant a person can apply
    without reasoning about branch reachability is "if you write it and it is not in the base block,
    reserve it". The alternative asks each future author to work out whether their new assignment is
    conditional, which is the reasoning that produced this bug.
    """
    import re
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "dashboard" / "state.py"
    ).read_text()
    body = src[src.index("    def notify(self, kind: str") : src.index("    #: Meta keys that can")]

    # The unconditional assignment block is the structural protection.
    structural = set(re.findall(r'^\s+"(\w+)": ', body, re.M))
    assert {
        "kind",
        "title",
        "body",
        "ts",
    } <= structural, "the authority-last assignment no longer sets the four base fields"

    written = set(re.findall(r'note\["(\w+)"\]\s*=', body))
    unprotected = written - structural - set(DashboardState._RESERVED_NOTE_KEYS)
    assert unprotected == set(), (
        f"notify writes {sorted(unprotected)} but neither assigns them after the meta merge nor "
        "reserves them — a caller can set those from meta"
    )


def test_meta_is_merged_before_the_platform_fields_not_after():
    """A source rail on the ORDER, which is the whole fix and is invisible to any single
    behavioural test: `note.update(meta)` reappearing after the assignment would restore the bug
    while every "ordinary meta rides along" test kept passing.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "dashboard" / "state.py"
    ).read_text()
    body = src[src.index("    def notify(self, kind: str") : src.index("    #: Meta keys that can")]
    # 🪤 Comments stripped first. The fix's own comment QUOTES the old `note.update(meta)` line to
    # explain what changed, so a prose match reported the bug as still present — this rail failed on
    # its first run for exactly that reason. A rail that reads documentation instead of code is
    # worse than none: it reds on an accurate explanation and greens on a renamed variable.
    code = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
    assert "note.update(meta)" not in code, "meta is being merged over the platform's fields again"
    assert code.index("supplied.items()") < code.index(
        '"kind": kind'
    ), "the platform's fields are no longer assigned after the meta merge"


def _fresh():
    """A per-test state. Built here rather than via the fixture so the parametrized cases each get
    a clean delivery log without the fixture's argument threading."""
    import personalclaw.dashboard.state as state_mod

    st = DashboardState.__new__(DashboardState)
    st._notification_log = []
    st._sessions = {}
    broadcast: list[dict[str, Any]] = []
    st._broadcast = lambda note: broadcast.append(note)  # type: ignore[method-assign]
    st._push_target = lambda kind, note: None  # type: ignore[method-assign]
    st.captured = {"broadcast": broadcast, "persisted": []}
    state_mod._persist_notification = lambda note: None  # type: ignore[assignment]
    return st

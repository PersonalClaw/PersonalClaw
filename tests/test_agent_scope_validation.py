"""`agent_scope` — declared, persisted, and enforcing nothing.

The intended design: chat-turn hooks fire agent-scoped (`fire_for_ids` over
`AgentProfile.triggers`); the substrate **preserves agent scoping as an optional
`spec.agent_scope`**
and does not silently introduce a global chat firing path.

🔴 THE DEFECT. `agent_scope` is in `SPEC_KEYS["event"]`, round-trips through the store, and was
validated by **nothing**. Every one of these stored with `ok: True` and zero issues:

    agent_scope="not-a-list"        # a bare string
    agent_scope=[]                  # an empty list
    agent_scope=[123]               # non-string entries
    agent_scope=["nonexistent"]     # an agent that does not exist

And no fire path reads it. A field that accepts any shape and is read by nothing does not *preserve*
scoping — it **promises** it, which is worse than its absence: an author who sets it believes their
trigger is fenced to one agent.

**The legacy path is genuinely fine**, verified rather than assumed: `chat_runner._fire`
resolves the
session agent's own trigger ids per fire and calls `fire_for_ids`, whose resolver returns `[]`
on any
failure precisely so a broken lookup cannot fall back to global firing. The substrate has not
introduced a global chat firing path — the store-backed `event` kind fires on DATA events (memory
writes and inbox arrivals), never on chat turns. So this file validates the field
and makes its unenforced state visible, rather than inventing a scoping mechanism for events that do
not exist yet.
"""

from __future__ import annotations

import pytest

from personalclaw.triggers.models import SPEC_KEYS, Trigger, validate_spec
from personalclaw.triggers.store import TriggerStore

BASE = {"source": "memory", "pattern": "MemoryUpdate"}


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


def _row(store, scope=...):
    spec = dict(BASE) if scope is ... else {**BASE, "agent_scope": scope}
    store.upsert(
        Trigger(
            id="event:v",
            name="v",
            kind="event",
            enabled=True,
            spec=spec,
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    return store.get("event:v")


# ── the shapes that used to pass silently ──


def test_a_BARE_STRING_scope_is_refused(store):
    """🔴 Coercing `"agent-a"` to `["agent-a"]` would make a malformed fence WORK, and a fence that
    tolerates the wrong shape teaches people to write it — the same reasoning `capability_allows`
    records for its own list check."""
    row = _row(store, "not-a-list")
    assert row.ok is False
    assert any("must be a list" in i.message for i in row.errors)


def test_an_EMPTY_scope_is_refused(store):
    """🔴 An error, not a warning, deliberately. In the legacy path an empty id list means
    `fire_for_ids` fires NOTHING — so `agent_scope: []` is an automation that can never fire,
    silently and forever. That is exactly the inert row the never-throw validation exists to
    surface."""
    row = _row(store, [])
    assert row.ok is False
    assert any("never fire" in i.message for i in row.errors)


def test_a_NON_STRING_entry_is_refused(store):
    row = _row(store, [123, "ok-agent"])
    assert row.ok is False
    assert any("non-empty agent ids" in i.message for i in row.errors)


def test_a_BLANK_entry_is_refused(store):
    """A whitespace id would match no agent while looking populated."""
    assert _row(store, ["   "]).ok is False


# ── the valid shapes still work ──


def test_a_VALID_scope_is_accepted(store):
    row = _row(store, ["research-agent", "triage-agent"])
    assert row.ok is True
    assert row.trigger.spec["agent_scope"] == ["research-agent", "triage-agent"]


def test_an_OMITTED_scope_is_fine(store):
    """The key is optional — an unscoped event trigger is the normal case."""
    assert _row(store).ok is True


def test_an_UNKNOWN_agent_id_is_NOT_refused(store):
    """Structure only, matching `validate_spec`'s stated contract. Whether the agent EXISTS is a
    semantic question the config layer answers, and refusing the id here would reject a trigger that
    becomes valid the moment the agent is installed."""
    assert _row(store, ["not-installed-yet"]).ok is True


def test_the_key_is_still_DECLARED_for_the_event_kind():
    """A guard on the premise: if `agent_scope` were dropped from `SPEC_KEYS`, this whole file would
    be validating a key nobody can author."""
    assert "agent_scope" in SPEC_KEYS["event"]


def test_only_the_EVENT_kind_validates_it():
    """`agent_scope` is meaningful on `event` alone; validating it elsewhere would flag a key that
    kind's own unknown-key check already reports.

    Asserted on the PATHS rather than on an empty list: a valid clock spec still carries the
    unrelated interval-floor warning, and a test that demanded zero issues would break the next
    time any advisory is added to another kind.
    """
    issues = validate_spec("clock", {"kind": "interval", "interval_secs": 3600})
    assert not [i for i in issues if "agent_scope" in i.path]


# ── the doctor makes the unenforced state visible ──


def test_the_doctor_reports_an_UNENFORCED_scope():
    """🔴 The honest part. The field is validated now, but still read by no fire path, so an author
    who set it is fenced by nothing. A validated-but-unenforced security field with no warning is
    the inert control wearing a clean shirt."""
    from personalclaw.triggers.calendar import diagnose

    rows = [{"id": "schedule:event:x", "spec": {**BASE, "agent_scope": ["a"]}}]
    finding = next(
        f
        for f in diagnose(rows, known_workflows=None).findings
        if f.code == "unenforced_agent_scope"
    )
    assert "NOT limited" in finding.detail
    assert finding.fix


def test_the_doctor_is_SILENT_for_an_unscoped_trigger():
    from personalclaw.triggers.calendar import diagnose

    rows = [{"id": "schedule:event:y", "spec": dict(BASE)}]
    assert not [
        f
        for f in diagnose(rows, known_workflows=None).findings
        if f.code == "unenforced_agent_scope"
    ]


# ── the legacy agent-scoped path is intact ──


async def _turn_firing(tmp_path, *, references: list[str], lookup_fails: bool) -> list[str]:
    """Run one real chat turn with one prompt-submit hook (``g1``) in the store, and return the
    ids of the hooks that ran. The agent references *references*; with *lookup_fails* every
    lookup of the agent's bindings the turn makes raises."""
    from unittest.mock import patch

    from reply_style_shared import FIRST, Conversation

    from personalclaw import hooks
    from personalclaw.dashboard import chat_runner

    chat = await Conversation(tmp_path, agent={"triggers": references}).start()
    store = hooks.ScriptHookStore(config_dir=tmp_path / "hooks-home")
    store.create(
        {
            "id": "g1",
            "name": "every prompt",
            "event": hooks.HOOK_EVENT_USER_PROMPT_SUBMIT,
            "provider": "bash",
            "provider_config": {"command": "true"},
        }
    )
    chat.state._hook_store = store
    ran: list[str] = []

    async def record(hook, context="", hook_event=None, **_kw):
        ran.append(hook.id)
        return hooks.ScriptHookResult(hook_id=hook.id, hook_name=hook.name, event="", exit_code=0)

    def fails(*_a, **_k):
        raise RuntimeError("the agent's bindings cannot be read")

    real = chat_runner.resolve_agent_bindings
    with (
        patch.object(hooks, "run_script_hook", record),
        patch.object(chat_runner, "resolve_agent_bindings", fails if lookup_fails else real),
    ):
        await chat.send(FIRST)
    assert chat.model.requests, "the turn never reached the model, so it proves nothing"
    return ran


@pytest.mark.asyncio
async def test_the_chat_path_has_NO_GLOBAL_firing_fallback(tmp_path):
    """A chat turn fires only the hooks its agent references. When the lookup of those
    references fails, the turn fires NOTHING rather than falling back to every hook in the
    store, and the turn itself still runs."""
    control = await _turn_firing(tmp_path / "control", references=["g1"], lookup_fails=False)
    assert control == ["g1"], "positive control: a referenced hook fires on this turn"
    assert await _turn_firing(tmp_path / "unref", references=[], lookup_fails=False) == []
    assert await _turn_firing(tmp_path / "broken", references=["g1"], lookup_fails=True) == []


def test_the_substrate_event_kind_has_no_chat_turn_source():
    """Why the scope has no reader yet: the store-backed `event` kind's sources are data origins
    (memory writes, inbox arrivals, app-contributed sources) — never chat turns. The vocabulary
    widened to inbox patterns and then to the app-source `AppEvent`; neither is a chat-turn
    source, so `agent_scope` remains unread and this guard stays valid. Pinned so that adding a
    *chat-turn* source is what is forced to confront the scope, not merely adding another data
    source.

    🔴 App-contributed sources are the interesting case for this guard, because an APP could
    plausibly contribute a chat-turn-shaped source. It cannot reach the scope:
    `trigger_sources.emit` namespaces every app event under `app:<name>:<event>` and emits with
    `source=SOURCE_APP`, so an app naming its event `chat_turn` still arrives as an `app` event
    and matches only `AppEvent` triggers. A chat-turn SOURCE — a new `EVENT_SOURCES` member — is
    what would break the pin, which is exactly right."""
    from personalclaw.event_triggers import EVENT_PATTERNS, EVENT_SOURCES

    assert EVENT_PATTERNS == (
        "MemoryUpdate",
        "MemoryKeyPattern",
        "ContentMatch",
        "InboxMessage",
        "InboxSender",
        "InboxAddress",
        "AppEvent",
    )
    # The invariant that keeps agent_scope legitimately unread: no source is a chat turn.
    assert "chat" not in EVENT_SOURCES and "chat_turn" not in EVENT_SOURCES

"""A trigger refused outside the dashboard says where it is allowed, not who may allow it.

#3702 refuses to switch on or run a trigger whose action it was not granted, anywhere but the
Triggers page, because that page is where the consent question is asked. The refusal read
outside the dashboard — a channel's ``cron resume`` (Slack calls ``sdk.channel.
set_automation_paused``), ``personalclaw cron resume`` in a terminal, the chat's
``automation_resume`` — said "Only the owner can allow it, on the Triggers page, which asks them
first." The one reading it is usually the owner, typing into their own channel or terminal, so
the sentence told them they were someone else. The same wording was in the notes an edit saved
switched off ("The owner allows it … which asks them first"), in the refusal for a row brought
over from an older version ("… asks the owner to allow it first") and in the chat's refusal of a
posture only the page can loosen ("The owner can make that change on the Triggers page, which asks
them first").

Each sentence now says where the trigger is allowed and which control does it there — Allow for a
trigger that is on, the switch for one that is off, the two the page shows — and the trigger is
still refused: the consent happens on the page, not in the reply.
"""

from __future__ import annotations

import pytest
import test_a_trigger_runs_only_what_it_was_granted as _granted

from personalclaw.triggers import tools as Tools

# #3702's harness: a scratch home with its trigger store, re-exported by name for pytest.
home = _granted.home
_NOTIFY = _granted._NOTIFY
_row = _granted._row
_schedule = _granted._schedule
_store = _granted._store

#: Wording that casts the reader as someone other than the owner.
_NOT_THE_READER = ("only the owner", "the owner", " them ", "them first")


def _addresses_the_reader(text: str) -> None:
    lowered = f" {text.lower()} "
    said = [w for w in _NOT_THE_READER if w in lowered]
    assert not said, f"the refusal speaks about the owner as someone else ({said}): {text}"
    assert "Triggers page" in text, text


def test_a_channel_cron_resume_says_to_switch_it_on_on_the_triggers_page(
    home,
) -> None:
    """The path a channel's ``cron resume`` takes: the SDK export Slack calls."""
    from personalclaw.sdk.channel import set_automation_paused

    _schedule(home, enabled=False)
    result = set_automation_paused(_store(home), trigger_id="nightly", paused=False)

    assert not result.ok
    _addresses_the_reader(result.text)
    assert result.text.endswith(
        "It can be allowed only on the Triggers page, which asks first: switch it on there."
    ), result.text
    # Still refused: the consent is the page's question, not this reply's.
    assert _row(home, "nightly").enabled is False
    assert _row(home, "nightly").capabilities == {}


def test_a_run_of_a_trigger_that_is_on_says_to_choose_allow_there(
    home,
) -> None:
    """The chat's ``automation_run``: on the page, a trigger that is on is allowed with Allow."""
    _schedule(home, enabled=True)
    result = Tools.run(_store(home), trigger_id="nightly", runner=lambda *_: None)

    assert not result.ok
    _addresses_the_reader(result.text)
    assert (
        "It can be allowed only on the Triggers page, which asks first: "
        "open it there and choose Allow." in result.text
    )


def test_an_edit_saved_switched_off_says_the_same(
    home,
) -> None:
    """The chat's ``automation_update``: an edit that needs a new grant is kept, switched off, and
    the note says where it is allowed."""
    _schedule(home, workflow=_NOTIFY)
    result = Tools.update(
        _store(home),
        trigger_id="nightly",
        patch={"workflow": {"inline": {"provider": "bash", "config": {"command": "id"}}}},
    )

    assert result.ok
    _addresses_the_reader(result.text)
    assert (
        "so it was saved switched off. It can be allowed only on the Triggers page" in result.text
    )
    assert _row(home, "nightly").enabled is False


def test_an_edit_that_changes_what_an_allowed_action_runs_says_the_same(
    home,
) -> None:
    """#3712's second note: the chat rewrote the command an allowed action runs, so the edit is
    kept, switched off, and the note says where the new version is allowed."""
    from personalclaw.triggers import grants

    _schedule(home)
    store = _store(home)
    trigger = _row(home, "nightly")
    grants.give(trigger)
    store.upsert(trigger)

    result = Tools.update(
        store,
        trigger_id="nightly",
        patch={"workflow": {"inline": {"provider": "bash", "config": {"command": "id"}}}},
    )

    assert result.ok
    _addresses_the_reader(result.text)
    assert (
        "runs changed, and the change has not been allowed, so it was saved switched off. "
        "It can be allowed only on the Triggers page, which asks first: switch it on there."
    ) in result.text
    assert _row(home, "nightly").enabled is False


def test_a_posture_the_chat_cannot_loosen_says_where_it_is_allowed(
    home,
) -> None:
    """#3712's posture refusal, read in the chat: nothing is saved, and it says where the change is
    allowed rather than who may allow it."""
    agent = {"inline": {"provider": "invoke-agent", "config": {"task_template": "check the build"}}}
    auto = {
        "provider": "invoke-agent",
        "config": {"task_template": "check the build", "approval_mode": "auto"},
    }
    _schedule(home, workflow=agent)

    result = Tools.update(_store(home), trigger_id="nightly", patch={"workflow": {"inline": auto}})

    assert not result.ok
    _addresses_the_reader(result.text)
    assert "approve its own tool calls" in result.text
    assert result.text.endswith(
        "That can be allowed only on the Triggers page, which asks first: make that change there."
    ), result.text
    assert _row(home, "nightly").workflow == agent


def test_a_row_brought_over_from_an_older_version_says_the_same(
    home,
) -> None:
    from personalclaw.triggers.legacy_import import IMPORTED_BY

    _schedule(home, enabled=False)
    store = _store(home)
    trigger = _row(home, "nightly")
    trigger.created_by = IMPORTED_BY
    store.upsert(trigger)

    result = Tools.set_paused(store, trigger_id="nightly", paused=False)

    assert not result.ok
    _addresses_the_reader(result.text)
    assert "switched on only from the Triggers page" in result.text
    assert _row(home, "nightly").enabled is False


@pytest.mark.parametrize("enabled", [True, False], ids=["on", "off"])
def test_the_dashboard_wording_is_unchanged(home, enabled) -> None:
    """On the dashboard the reader is at the page already; its sentence keeps addressing them."""
    from personalclaw.triggers import grants

    _schedule(home, enabled=enabled)
    text = grants.refusal(_row(home, "nightly"), ["bash"])
    _addresses_the_reader(text)
    assert ("choose Allow" in text) is enabled

"""A question about her plans reads the calendar in the folders she shared.

Asked where her daughter's Saturday game was, the agent searched the notes folder, found the time
in a weekly note and answered that it did not know the place, while the calendar file in a folder
she had shared with it (an allowed working directory) held the event and its place. The turn's
note named her folders and nothing in them, and no tool read a calendar: the morning brief that
did read it opened the raw ``.ics`` text and took an all-day event's exclusive end for its last
day.

Now the ``[file places]`` note names each calendar file in the shared folders by name and kind,
and ``calendar_events`` lists what is on them between two days: each repeat at its own time in its
own zone, shown in hers, with its place, and an all-day event through its last day.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw import calendar_files
from personalclaw.agents.native.builtin_tools import PLATFORM_CATEGORIES, NativeBuiltinToolProvider
from personalclaw.file_scope import FileScope, places_note
from personalclaw.timezones import zone_or_raise
from personalclaw.tool_providers.calendar_events import (
    TOOL,
    CalendarToolProvider,
    create_calendar_tools_provider,
)

_ZONE = "America/Toronto"

#: Her calendar's shape: a weekly event whose place is its LOCATION, an all-day trip whose DTEND
#: is the day after its last, and a talk after the clocks go back.
_FAMILY = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example//Calendar//EN
X-WR-CALNAME:Rivera family
X-WR-TIMEZONE:America/Toronto
BEGIN:VEVENT
UID:football-20261003@example.org
DTSTART;TZID=America/Toronto:20261003T090000
DTEND;TZID=America/Toronto:20261003T100000
SUMMARY:Maya football
LOCATION:Riverside Park\\, field 2
RRULE:FREQ=WEEKLY;UNTIL=20261031T000000Z
END:VEVENT
BEGIN:VEVENT
UID:trip-20261022@example.org
DTSTART;VALUE=DATE:20261022
DTEND;VALUE=DATE:20261102
SUMMARY:Lisbon trip
END:VEVENT
BEGIN:VEVENT
UID:talk-20261112@example.org
DTSTART;TZID=America/Toronto:20261112T192000
DTEND;TZID=America/Toronto:20261112T195500
SUMMARY:Meetup talk
LOCATION:Community hall, 12 Main St
END:VEVENT
END:VCALENDAR
"""


def _write_config(pc_home: Path, folders: list[str]) -> None:
    (pc_home / "config.json").write_text(
        json.dumps({"timezone": _ZONE, "agent": {"subagent_cwd_allowed_roots": folders}}),
        encoding="utf-8",
    )


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Her PersonalClaw home and her own home beside it: Documents shared as an allowed working
    directory with the family calendar in it, a weekly note, and a folder she shared with nobody."""
    root = Path(os.path.realpath(tmp_path))
    pc_home = root / "pc-home"
    workspace = pc_home / "workspace"
    workspace.mkdir(parents=True)
    user = root / "user"
    calendar = user / "Documents" / "Calendar"
    calendar.mkdir(parents=True)
    (calendar / "family.ics").write_text(_FAMILY, encoding="utf-8")
    weekly = user / "Notes" / "Weekly"
    weekly.mkdir(parents=True)
    (weekly / "W40.md").write_text("- Sat 09:00 Maya football\n", encoding="utf-8")
    private = user / "Private"
    private.mkdir()
    (private / "other.ics").write_text(_FAMILY.replace("Rivera family", "Other"), "utf-8")
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: pc_home)
    _write_config(pc_home, ["~/Documents", "~/Notes"])
    return SimpleNamespace(pc=pc_home, ws=workspace, user=user, calendar=calendar, private=private)


def _ask(home, **arguments) -> str:
    """``calendar_events`` called as a turn in her chat calls it: in the session's folder."""
    from personalclaw.agents.native.builtin_tools import bind_tool_context, reset_tool_context

    async def _call():
        tokens = bind_tool_context(cwd=home.ws)
        try:
            return await CalendarToolProvider().invoke(TOOL, arguments)
        finally:
            reset_tool_context(tokens)

    result = asyncio.run(_call())
    assert result.success, result.error
    return result.output


def _add_event(home, body: str) -> None:
    path = home.calendar / "family.ics"
    text = path.read_text(encoding="utf-8").replace(
        "END:VCALENDAR", body.strip() + "\nEND:VCALENDAR"
    )
    path.write_text(text, encoding="utf-8")


# ── the answer ───────────────────────────────────────────────────────────────────────────────


def test_the_saturday_game_comes_with_its_place(home):
    """🔴 Before: nothing read a calendar, so the place was "not known"."""
    out = _ask(home, start="2026-10-03", end="2026-10-03")
    assert out.splitlines()[0] == (
        "Rivera family (~/Documents/Calendar/family.ics), Sat 3 Oct 2026, times in "
        "America/Toronto: 1 event."
    ), out
    assert out.splitlines()[1] == (
        "- Sat 3 Oct 2026, 09:00–10:00: Maya football · at Riverside Park, field 2 · "
        "repeats weekly"
    ), out


def test_a_weekly_event_repeats_until_its_end(home):
    """UNTIL is the end of the night before the 31st, so the 24th is the last game."""
    out = _ask(home, start="2026-10-01", end="2026-10-31", query="football")
    days = [line.split(",")[0] for line in out.splitlines()[1:]]
    assert days == [
        "- Sat 3 Oct 2026",
        "- Sat 10 Oct 2026",
        "- Sat 17 Oct 2026",
        "- Sat 24 Oct 2026",
    ]
    assert "4 events matching 'football'" in out.splitlines()[0]


def test_a_repeat_keeps_its_time_when_the_clocks_change(home):
    """The clocks go back on 1 Nov in her zone: a 19:00 weekly event is still at 19:00."""
    _add_event(
        home,
        """
BEGIN:VEVENT
UID:choir@example.org
DTSTART;TZID=America/Toronto:20261022T190000
DTEND;TZID=America/Toronto:20261022T203000
SUMMARY:Choir
RRULE:FREQ=WEEKLY;COUNT=4
END:VEVENT
""",
    )
    out = _ask(home, start="2026-10-01", end="2026-11-30", query="choir")
    assert [line.split(": ")[0] for line in out.splitlines()[1:]] == [
        "- Thu 22 Oct 2026, 19:00–20:30",
        "- Thu 29 Oct 2026, 19:00–20:30",
        "- Thu 5 Nov 2026, 19:00–20:30",
        "- Thu 12 Nov 2026, 19:00–20:30",
    ], out


def test_an_all_day_event_ends_on_its_last_day(home):
    """🔴 Read raw, the trip "ran 22 Oct to 2 Nov": DTEND is the day after its last."""
    out = _ask(home, start="2026-10-25", end="2026-10-25")
    assert "- Thu 22 Oct – Sun 1 Nov 2026, all day: Lisbon trip" in out, out
    assert _ask(home, start="2026-11-02", end="2026-11-02").startswith("No events on Mon 2 Nov")


def test_an_event_in_another_zone_is_shown_in_hers(home):
    _add_event(
        home,
        """
BEGIN:VEVENT
UID:call@example.org
DTSTART;TZID=Europe/Lisbon:20261023T200000
DTEND;TZID=Europe/Lisbon:20261023T210000
SUMMARY:Family call
END:VEVENT
""",
    )
    out = _ask(home, start="2026-10-23", end="2026-10-23", query="call")
    assert "- Fri 23 Oct 2026, 15:00–16:00: Family call" in out, out


def test_a_skipped_or_moved_repeat_is_read_as_the_calendar_says(home):
    """EXDATE takes a repeat out, a RECURRENCE-ID entry moves one, a cancelled one is gone."""
    path = home.calendar / "family.ics"
    text = path.read_text(encoding="utf-8").replace(
        "RRULE:FREQ=WEEKLY;UNTIL=20261031T000000Z",
        "RRULE:FREQ=WEEKLY;UNTIL=20261031T000000Z\nEXDATE;TZID=America/Toronto:20261017T090000",
    )
    path.write_text(text, encoding="utf-8")
    _add_event(
        home,
        """
BEGIN:VEVENT
UID:football-20261003@example.org
RECURRENCE-ID;TZID=America/Toronto:20261024T090000
DTSTART;TZID=America/Toronto:20261024T110000
DTEND;TZID=America/Toronto:20261024T120000
SUMMARY:Maya football (final)
LOCATION:Northside Arena
END:VEVENT
BEGIN:VEVENT
UID:football-20261003@example.org
RECURRENCE-ID;TZID=America/Toronto:20261010T090000
DTSTART;TZID=America/Toronto:20261010T090000
DTEND;TZID=America/Toronto:20261010T100000
SUMMARY:Maya football
STATUS:CANCELLED
END:VEVENT
""",
    )
    out = _ask(home, start="2026-10-01", end="2026-10-31", query="football")
    assert out.splitlines()[1:] == [
        "- Sat 3 Oct 2026, 09:00–10:00: Maya football · at Riverside Park, field 2"
        " · repeats weekly",
        "- Sat 24 Oct 2026, 11:00–12:00: Maya football (final) · at Northside Arena",
    ], out


def test_a_rule_it_cannot_expand_is_said_not_guessed(home):
    _add_event(
        home,
        """
BEGIN:VEVENT
UID:meds@example.org
DTSTART;TZID=America/Toronto:20261005T080000
SUMMARY:Medicine
RRULE:FREQ=HOURLY;INTERVAL=8
END:VEVENT
""",
    )
    out = _ask(home, start="2026-10-05", end="2026-10-06", query="medicine")
    assert out.splitlines()[1] == (
        "- Mon 5 Oct 2026, 08:00: Medicine · it repeats by a rule this reader does not expand "
        "(FREQ=HOURLY;INTERVAL=8), so only its first time is listed"
    ), out


def test_an_event_it_cannot_read_is_left_out_and_the_rest_still_read(home):
    _add_event(
        home,
        """
BEGIN:VEVENT
UID:bad-date@example.org
DTSTART;TZID=America/Toronto:20261399T250000
SUMMARY:Garbled
END:VEVENT
BEGIN:VEVENT
UID:end-of-time@example.org
DTSTART;VALUE=DATE:99991231
SUMMARY:Last day there is
END:VEVENT
""",
    )
    out = _ask(home, start="2026-10-03", end="2026-10-03")
    assert "- Sat 3 Oct 2026, 09:00–10:00: Maya football" in out, out
    assert "Garbled" not in out and "Last day there is" not in out


def test_monthly_and_yearly_rules_land_on_their_days():
    """Second Tuesday, last day of the month, second Sunday of March, every other week twice."""
    zone = zone_or_raise(_ZONE)
    rules = {
        "FREQ=MONTHLY;BYDAY=2TU;COUNT=3": [date(2026, 1, 13), date(2026, 2, 10), date(2026, 3, 10)],
        "FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=3": [
            date(2026, 1, 31),
            date(2026, 2, 28),
            date(2026, 3, 31),
        ],
        "FREQ=YEARLY;BYMONTH=3;BYDAY=2SU;COUNT=2": [date(2026, 3, 8), date(2027, 3, 14)],
        "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;COUNT=4": [
            date(2026, 1, 5),
            date(2026, 1, 7),
            date(2026, 1, 19),
            date(2026, 1, 21),
        ],
        "FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1;COUNT=2": [
            date(2026, 1, 30),
            date(2026, 2, 27),
        ],
    }
    for rule, expected in rules.items():
        first = expected[0].strftime("%Y%m%d")
        text = (
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:r@example.org\n"
            f"DTSTART;TZID={_ZONE}:{first}T090000\nSUMMARY:R\nRRULE:{rule}\n"
            "END:VEVENT\nEND:VCALENDAR\n"
        )
        ref = calendar_files.CalendarRef("/nowhere/r.ics", "r.ics", "R")
        parsed = calendar_files.parse(text, ref, zone)
        found = calendar_files.occurrences(parsed, date(2026, 1, 1), date(2027, 12, 31), zone)
        assert [o.start.date() for o in found] == expected, rule


def test_a_counted_rule_seen_from_a_later_week_still_counts_its_earlier_repeats():
    """Ten daily repeats from Mon 28 Sep: a window from 5 Oct sees the last three."""
    zone = zone_or_raise(_ZONE)
    text = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:c@example.org\n"
        f"DTSTART;TZID={_ZONE}:20260928T073000\nSUMMARY:Physio exercises\n"
        "RRULE:FREQ=DAILY;COUNT=10\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    parsed = calendar_files.parse(
        text, calendar_files.CalendarRef("/nowhere/c.ics", "c", "C"), zone
    )
    found = calendar_files.occurrences(parsed, date(2026, 10, 5), date(2026, 10, 31), zone)
    assert [o.start.date() for o in found] == [date(2026, 10, d) for d in (5, 6, 7)]


# ── what she shares, and only that ───────────────────────────────────────────────────────────


def test_only_the_calendars_in_the_folders_she_shared_are_found(home):
    hidden = home.user / "Documents" / ".trash"
    hidden.mkdir()
    (hidden / "old.ics").write_text(_FAMILY, encoding="utf-8")
    vendored = home.user / "Documents" / "node_modules" / "pkg"
    vendored.mkdir(parents=True)
    (vendored / "fixture.ics").write_text(_FAMILY, encoding="utf-8")
    (home.user / "Documents" / "notes.ics").write_text("not a calendar\n", encoding="utf-8")
    found = calendar_files.find(FileScope([home.ws]), fresh=True)
    assert [(ref.name, ref.shown) for ref in found] == [
        ("Rivera family", "~/Documents/Calendar/family.ics")
    ]


def test_a_knowledge_source_lends_a_calendar_only_when_it_takes_ics_files_in(home):
    from personalclaw.knowledge import get_knowledge_store

    _write_config(home.pc, [])
    store = get_knowledge_store()
    source = store.create_source(
        name="Private",
        provider="watched-dir",
        kind="dir",
        spec={"path": "~/Private", "include": ["*.md"]},
        item_type="note",
    )
    assert calendar_files.find(FileScope([home.ws]), fresh=True) == []
    store.update_source(source, spec={"path": "~/Private", "include": ["*.md", "*.ics"]})
    assert [ref.name for ref in calendar_files.find(FileScope([home.ws]), fresh=True)] == ["Other"]


def test_a_calendar_outside_the_shared_folders_is_refused(home):
    async def _call():
        from personalclaw.agents.native.builtin_tools import bind_tool_context, reset_tool_context

        tokens = bind_tool_context(cwd=home.ws)
        try:
            return await CalendarToolProvider().invoke(TOOL, {"calendar": "~/Private/other.ics"})
        finally:
            reset_tool_context(tokens)

    result = asyncio.run(_call())
    assert not result.success
    assert "outside every folder the file tools reach" in result.error


def test_with_no_calendar_shared_it_says_how_to_share_one(home):
    _write_config(home.pc, [])
    out = _ask(home)
    assert out.startswith("No calendar (.ics) file is in the folders the user shares")
    assert "Allowed working directories" in out


def test_the_tool_only_reads_and_asks_nobody():
    (definition,) = asyncio.run(create_calendar_tools_provider().list_tools())
    assert definition.name == TOOL and definition.requires_approval is False
    assert definition.risk_level == "safe"


# ── what the model is told ───────────────────────────────────────────────────────────────────


def test_the_turn_note_names_her_calendar_by_name_and_kind(home):
    """🔴 Before: the note named her folders and nothing in them."""
    note = places_note(FileScope([home.ws]), calendars=True)
    assert "- 'Rivera family': an .ics calendar file, ~/Documents/Calendar/family.ics" in note
    assert "call calendar_events" in note
    assert "Rivera family" not in places_note(FileScope([home.ws])), "no tool, no calendar named"


def test_a_calendar_search_that_fails_leaves_the_folders_named(home, monkeypatch):
    """The note is built for every request: a search that cannot run costs the turn nothing."""

    def _broken(scope, *, fresh=False):
        raise PermissionError("the folder cannot be listed")

    monkeypatch.setattr(calendar_files, "find", _broken)
    note = places_note(FileScope([home.ws]), calendars=True)
    assert "- ~/Documents: an allowed working directory" in note
    assert "calendar_events" not in note


def test_a_scripted_turn_is_told_of_the_calendar_and_reads_the_game_from_it(home):
    """Driven through the native loop: the request names the calendar, the model calls the tool,
    and the result it reads holds Saturday's game and its place, with no approval asked."""
    from test_native_runtime import _defn, _drain, _ScriptedModel

    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.llm.events import (
        EVENT_COMPLETE,
        EVENT_PERMISSION_REQUEST,
        EVENT_TEXT_CHUNK,
        EVENT_TOOL_CALL,
        EVENT_TOOL_RESULT,
        AgentEvent,
    )

    call = json.dumps({"start": "2026-10-03", "end": "2026-10-03"})
    model = _ScriptedModel(
        [
            [
                AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title=TOOL, tool_input=call),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
            [
                AgentEvent(kind=EVENT_TEXT_CHUNK, text="09:00 at Riverside Park"),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
        ]
    )
    platform = NativeBuiltinToolProvider(cwd=home.ws, categories=PLATFORM_CATEGORIES)

    async def _turn():
        runtime = NativeAgentRuntime(
            definition=_defn(),
            model_provider=model,
            tool_providers=[platform, create_calendar_tools_provider()],
            cwd=home.ws,
        )
        await runtime.start()
        return await asyncio.wait_for(
            _drain(runtime, "Where is Maya's game tomorrow, and when?"), timeout=10
        )

    events = asyncio.run(_turn())
    notes = [m["content"] for m in model.seen_messages[0] if m.get("role") == "system"]
    told = next((n for n in notes if "[file places]" in n), "")
    assert "'Rivera family': an .ics calendar file, ~/Documents/Calendar/family.ics" in told, notes
    assert "call calendar_events" in told
    assert EVENT_PERMISSION_REQUEST not in [e.kind for e in events]
    result = next(e for e in events if e.kind == EVENT_TOOL_RESULT)
    assert "- Sat 3 Oct 2026, 09:00–10:00: Maya football · at Riverside Park, field 2" in str(
        result.tool_output
    )

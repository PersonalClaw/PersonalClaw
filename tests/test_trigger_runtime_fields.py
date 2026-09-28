"""A snapshot merge leaves behind every stamp of what happened to a trigger in another home.

`snapshot._merge_triggers` brings rows in from another home and drops
`triggers.store.RUNTIME_FIELDS` from each, so an armed fire or a run count from elsewhere never
arrives as if it had happened here.
The list was written by hand and fell behind the entity: `last_fired_at`, `park_retry_after`,
`last_alert_hash` and `last_alert_at` all travelled. So an imported event trigger's debounce spaced
its first fire here from a fire on the other machine, an outage there parked it here, and the other
home's alert dedupe could swallow this home's first alert of the same failure.

The second test is the rail: every field of the `Trigger` dataclass must be classified — runtime, or
part of what the trigger is — so the next stamp added to the entity fails here until someone decides
whether a merge carries it. Derived from the dataclass, so it cannot drift from it.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from personalclaw.snapshot import _merge_triggers
from personalclaw.triggers.models import Trigger, parse_trigger
from personalclaw.triggers.store import RUNTIME_FIELDS

#: What a trigger IS — authored, or attributed where it was made — and so what a merge carries into
#: another home. Everything else on the entity is one home's (`RUNTIME_FIELDS`): what has happened
#: to it there, and the owner's switch and grant there. The grant (`capabilities`) is not here: a
#: yes is given where the owner is shown what runs, so it does not travel.
DEFINITION_FIELDS = frozenset(
    {
        "id",
        "name",
        "kind",
        "created_by",
        "author",
        "origin_harness",
        "spec",
        "gates",
        "workflow",
        "overlap",
        "session",
        "model_tier",
        "delivery",
        "failure_delivery",
        "retry",
        "failure_policy",
        "yield_to_user",
        "resource_slots",
        "skip_if_active",
        "catch_up",
        "expires_at",
    }
)

#: The four stamps the hand-kept list missed, with a value that proves each one arrived.
MISSED = {
    "last_fired_at": "2026-09-01T09:00:00+00:00",
    "park_retry_after": 1_900_000_000.0,
    "last_alert_hash": "5f1c0ffee",
    "last_alert_at": 1_800_000_000.0,
}


def _elsewhere(**runtime: object) -> dict:
    """A trigger row as another home wrote it: armed, run, alerted, parked."""
    row = Trigger(
        id="event:watch-inbox",
        name="Watch my inbox",
        kind="event",
        spec={"pattern": "memory"},
        workflow={"inline": {"provider": "notify", "config": {"title": "new"}}},
    ).to_dict()
    row.update(
        {
            "next_fire_at": "2026-09-01T10:00:00+00:00",
            "run_count": 42,
            "last_success_at": "2026-09-01T09:00:00+00:00",
            **runtime,
        }
    )
    return row


def _merged(tmp_path: Path, row: dict) -> dict:
    src = tmp_path / "src.json"
    dst = tmp_path / "dst.json"
    src.write_text(json.dumps({"triggers": [row]}))
    dst.write_text(json.dumps({"triggers": []}))
    _merge_triggers(src, dst)
    (merged,) = json.loads(dst.read_text())["triggers"]
    return merged


def test_a_merge_drops_every_stamp_of_what_happened_in_the_other_home(tmp_path) -> None:
    """🔴 Red on main: the four stamps below arrived with the row."""
    merged = _merged(tmp_path, _elsewhere(**MISSED))
    arrived = {name: merged[name] for name in MISSED if name in merged}
    assert arrived == {}, f"another home's stamps came with the row: {arrived}"
    # And the row reads back as one that has never fired, parked or alerted here.
    trigger, _issues = parse_trigger(merged)
    assert trigger.last_fired_at == ""
    assert trigger.park_retry_after == 0.0
    assert (trigger.last_alert_hash, trigger.last_alert_at) == ("", 0.0)


def test_a_merge_keeps_what_the_trigger_is(tmp_path) -> None:
    """CONTROL: dropping runtime state must not drop the automation."""
    merged = _merged(tmp_path, _elsewhere(**MISSED))
    assert merged["name"] == "Watch my inbox"
    assert merged["workflow"] == {"inline": {"provider": "notify", "config": {"title": "new"}}}
    assert merged["enabled"] is False, "an imported automation arrives switched off"


def test_every_trigger_field_is_classified_as_runtime_or_definition() -> None:
    """The rail. A field on `Trigger` in neither set fails here, naming the field — decide whether
    it is what has happened to a trigger in one home (add it to `RUNTIME_FIELDS`, which a merge
    drops) or part of what the trigger is (add it to `DEFINITION_FIELDS` above)."""
    fields = {field.name for field in dataclasses.fields(Trigger)}
    runtime = set(RUNTIME_FIELDS)
    unclassified = sorted(fields - runtime - DEFINITION_FIELDS)
    assert unclassified == [], f"Trigger fields a snapshot merge has no rule for: {unclassified}"
    assert runtime & DEFINITION_FIELDS == set(), "a field cannot be both"
    stale = sorted((runtime | DEFINITION_FIELDS) - fields)
    assert stale == [], f"classified fields Trigger no longer has: {stale}"
    assert len(RUNTIME_FIELDS) == len(runtime), "RUNTIME_FIELDS lists a field twice"


def test_the_rail_would_see_an_unclassified_field() -> None:
    """Positive control: the derivation reads the dataclass, so a new field IS seen."""
    extended = dataclasses.make_dataclass(
        "Extended", [("last_tuned_at", str, dataclasses.field(default=""))], bases=(Trigger,)
    )
    fields = {field.name for field in dataclasses.fields(extended)}
    assert sorted(fields - set(RUNTIME_FIELDS) - DEFINITION_FIELDS) == ["last_tuned_at"]

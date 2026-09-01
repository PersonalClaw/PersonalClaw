"""Every field the schedule-trigger form puts on the wire must be read by the handler.

Four issues on ONE seam, all the same shape — the form sends a field, the handler never reads it,
the request returns 200, and the setting the user chose is simply gone:

* issue 587 — `enabled` was sent at create and read by nobody, so "create it switched off" armed a
  live trigger.
* issue 689 — the reverse direction: the form described an action it could not edit, and the server
  dutifully applied it, destroying a `notify` action on rename.
* issue 272 — `skip_dates` was read by `_create_schedule` and preserved by `_carried` across a
  cadence change, but absent from `_update_schedule` entirely. A holiday list could be set once and
  never edited.
* issue 268 — `approval_mode` was dropped in the BROWSER: `_scheduleBodyToWire` destructures it as
  action config, and the create page's body already carried its own `action`, so the branch that
  returns early discarded it before any request was made.

Each was found by a human driving the UI and noticing a switch that would not stick. That is an
expensive way to find a one-line omission, and it scales with the number of fields. So this census
holds the two sets against each other in source: what the frontend SENDS, and what the handlers
READ. A new field wired into one side and not the other fails here instead of in someone's browser.

It is a source-text census, like `test_durability_inventory_census.py` — it parses rather than
imports, because the sending side is TypeScript and cannot be called from pytest. That makes the
extraction regexes load-bearing, which is why every region and every set below has a floor: if a
refactor moves a function or renames a variable so a pattern stops matching, the floor fails loudly
rather than reporting an empty diff as agreement.

🪤 THE FAKE VERSION OF THIS TEST asserts a hardcoded list of field names appears in
`triggers.py`. That passes forever while the frontend grows a field the backend never learns about
— which is the entire bug, four times over. The test has to derive BOTH sides from source and
compare them; a list written by hand only records what someone already knew.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_HANDLERS = _REPO_ROOT / "src" / "personalclaw" / "dashboard" / "handlers" / "triggers.py"
_SCHEDULE_FORM = _REPO_ROOT / "web" / "src" / "pages" / "schedule" / "ScheduleForm.tsx"
_CREATE_PAGE = _REPO_ROOT / "web" / "src" / "pages" / "triggers" / "TriggerCreatePage.tsx"
_API = _REPO_ROOT / "web" / "src" / "lib" / "api.ts"

#: Wire fields the handlers deliberately do NOT read, each with the reason it is allowed to be
#: unread. A field belongs here only when leaving it unread is a decision someone made on purpose —
#: never because a test went red. Rows are the exception surface; keep it short and keep the
#: reasons true.
_DELIBERATELY_UNREAD: dict[str, str] = {
    "at": (
        "One-shot scheduling is a declared-future axis: `scheduleMeta.ts` flags the `at` kind "
        "`soon: true`, so the picker offers it behind a SoonTag and the payload carries it for "
        "forward compatibility. `_create_schedule` reads it (a one-shot CAN be created); "
        "`_update_schedule` does not, so its fire time is not editable yet. Landing one-shot edit "
        "means reading it there and deleting this row."
    ),
}

#: Floors. Each is the size of the set as measured when this test was written, minus a little slack
#: for churn. They exist because every number below is produced by a regex over source text, and a
#: regex that silently stops matching turns this whole file into a test that asserts nothing.
_MIN_SENT = 8
_MIN_READ_CREATE = 8
_MIN_READ_UPDATE = 8
_MIN_ACTION_CONFIG = 5


def _region(path: Path, start: str, end: str) -> str:
    """Source between two anchors, or a hard failure naming the anchor that moved.

    A missing anchor is the failure mode that would otherwise make this file vacuous, so it raises
    instead of returning "" — an empty region compares equal to everything.
    """
    text = path.read_text(encoding="utf-8")
    i = text.find(start)
    if i < 0:
        pytest.fail(
            f"{path.relative_to(_REPO_ROOT)}: anchor {start!r} not found. The census cannot see "
            "this region any more — re-point the anchor at whatever replaced it, then re-check "
            "that the fields below still line up."
        )
    j = text.find(end, i + len(start))
    if j < 0:
        pytest.fail(
            f"{path.relative_to(_REPO_ROOT)}: closing anchor {end!r} not found after {start!r}."
        )
    return text[i : j + len(end)]


def _object_literal_keys(region: str) -> set[str]:
    """Keys of an object literal: `foo:` following a `{`, a `,`, or a line start.

    Anchoring on the preceding delimiter is what keeps a ternary's `? … :` out of the result. Keys
    are also matched mid-line, because the codebase packs several onto one line.
    """
    return set(re.findall(r"(?:^|[{,])\s*([a-z_][a-z_0-9]*)\s*:", region, re.M))


def _assigned_keys(region: str) -> set[str]:
    """`body.foo = …` / `body.foo=` assignments made after the literal."""
    return set(re.findall(r"\bbody\.([a-z_][a-z_0-9]*)\s*=(?!=)", region))


def _sent_fields() -> set[str]:
    """Every key either frontend payload builder can put on a schedule create/update body."""
    edit = _region(_SCHEDULE_FORM, "export function draftToPayload(", "\n  return body\n}")
    create = _region(_CREATE_PAGE, "if (kind === 'schedule') {", "await api.createSchedule(body)")
    sent: set[str] = set()
    for region in (edit, create):
        literal = _region_literal(region)
        sent |= _object_literal_keys(literal) | _assigned_keys(region)
    return sent


def _region_literal(region: str) -> str:
    """The `const body… = { … }` literal inside a payload-builder region."""
    i = region.find("= {")
    if i < 0:
        pytest.fail("payload builder has no `= {` body literal; the census cannot read its keys")
    depth = 0
    for k in range(i + 2, len(region)):
        if region[k] == "{":
            depth += 1
        elif region[k] == "}":
            depth -= 1
            if depth == 0:
                return region[i + 2 : k + 1]
    pytest.fail("unbalanced braces in the payload-builder literal")


def _action_config_fields() -> set[str]:
    """Keys `_scheduleBodyToWire` folds into the action, so they never reach the wire top level.

    `action` itself is destructured and put straight back, so it is excluded — it IS a top-level
    wire field, and the whole point of issue 689 is that the server reads it.
    """
    region = _region(_API, "function _scheduleBodyToWire(", "\n}")
    m = re.search(r"const\s*\{([^}]*)\}\s*=\s*body", region)
    if m is None:
        pytest.fail("`_scheduleBodyToWire` no longer destructures `body`; re-point this extractor")
    names = {n.strip() for n in m.group(1).split(",") if n.strip() and "..." not in n}
    return names - {"action"}


def _handler_reads(func: str) -> set[str]:
    """Body keys a handler reads: `body.get("x")`, `body["x"]`, `"x" in body`, and tuple loops.

    The tuple-loop pattern is here because `_update_schedule`'s allowlist is
    `for key in ("name", "channel", …)`. Without it the four fields that path DOES read would read
    as unread, and the census would fail on fields that work fine.
    """
    region = _region(_HANDLERS, f"async def {func}(", "\n\nasync def ")
    keys = set(re.findall(r'body\.get\(\s*"([a-z_][a-z_0-9]*)"', region))
    keys |= set(re.findall(r'body\[\s*"([a-z_][a-z_0-9]*)"\s*\]', region))
    keys |= set(re.findall(r'"([a-z_][a-z_0-9]*)"\s+in\s+body', region))
    for tup in re.findall(r"for\s+\w+\s+in\s+\(([^)]*)\)", region):
        keys |= set(re.findall(r'"([a-z_][a-z_0-9]*)"', tup))
    return keys


def _wire_fields() -> set[str]:
    """What reaches the server: everything sent, minus what is folded into the action."""
    return (_sent_fields() - _action_config_fields()) | {"action"}


def test_the_census_can_still_see_both_sides() -> None:
    """The vacuity floor. Every assertion below is a set difference, and a regex that stopped
    matching produces an empty set, which differs from nothing. Measure the sets first."""
    sent, action_cfg = _sent_fields(), _action_config_fields()
    create, update = _handler_reads("_create_schedule"), _handler_reads("_update_schedule")
    assert len(sent) >= _MIN_SENT, f"only {len(sent)} sent fields parsed: {sorted(sent)}"
    assert len(action_cfg) >= _MIN_ACTION_CONFIG, f"action config: {sorted(action_cfg)}"
    assert len(create) >= _MIN_READ_CREATE, f"_create_schedule reads {sorted(create)}"
    assert len(update) >= _MIN_READ_UPDATE, f"_update_schedule reads {sorted(update)}"
    # The two known-good anchors of the whole seam: if `name` and `action` are not in both sides,
    # the extraction is wrong, not the code.
    assert {"name", "action"} <= _wire_fields()


@pytest.mark.parametrize("func", ["_create_schedule", "_update_schedule"])
def test_every_field_the_form_sends_is_read_or_declared_unread(func: str) -> None:
    read = _handler_reads(func)
    unread = sorted(f for f in _wire_fields() if f not in read and f not in _DELIBERATELY_UNREAD)
    assert not unread, (
        f"{func} never reads {unread}, but the schedule form sends them. The request will "
        f"succeed and the setting will be silently discarded — issues 268/272/587 were each "
        f"exactly this. Read the field, or add it to `_DELIBERATELY_UNREAD` with the reason it "
        f"is allowed to be ignored."
    )


def test_skip_dates_is_read_by_both_paths() -> None:
    """issue 272, pinned directly. It is the field with the widest gap between how carefully it is
    PRESERVED (`_carried` exists for it) and how easily it was un-editable, so it gets its own row
    rather than relying on the census's set arithmetic to keep covering it."""
    for func in ("_create_schedule", "_update_schedule"):
        assert "skip_dates" in _handler_reads(func), f"{func} dropped skip_dates again"


def test_approval_mode_never_rides_the_wire_top_level() -> None:
    """issue 268. `approval_mode` is `invoke-agent` action config — `schedule.py`'s property
    returns '' for every other provider, and `invoke-agent-action/app.json` declares the field. So
    the ONLY correct place for it is inside the action, and a handler that grew a top-level reader
    for it would be building a second home for one setting."""
    assert "approval_mode" in _action_config_fields()
    assert "approval_mode" not in _wire_fields()
    for func in ("_create_schedule", "_update_schedule"):
        assert "approval_mode" not in _handler_reads(func), (
            f"{func} grew a top-level `approval_mode` reader. The field lives in the action "
            "config; two addresses for one setting is how it ends up disagreeing with itself."
        )


def test_action_config_keys_never_ride_a_body_that_carries_its_own_action() -> None:
    """issue 268, the half the set arithmetic above CANNOT see.

    The census compares wire fields to handler reads, and `approval_mode` is filtered out of the
    wire set by definition — it is action config. So no amount of set difference notices that the
    create page was sending it. The bug is one layer earlier: `_scheduleBodyToWire` returns
    `{...rest, action}` immediately when the body already has an `action`, and every action-config
    key was destructured out of `rest` on the line above. Any such key on that page's body is
    therefore deleted in the browser, before a request exists.

    So the rule is structural, not arithmetic: a payload that supplies its own `action` must not
    also carry loose action-config keys. There is exactly one right place for them — inside the
    `config` the Action block already edits.
    """
    create = _region(_CREATE_PAGE, "if (kind === 'schedule') {", "await api.createSchedule(body)")
    assert "body.action" in create, (
        "the create page no longer sets `body.action`, so this test is no longer checking the "
        "branch that drops fields — re-point it at whatever builds the create payload now"
    )
    sent_here = _object_literal_keys(_region_literal(create)) | _assigned_keys(create)
    leaked = sorted(_action_config_fields() & sent_here)
    assert not leaked, (
        f"the trigger-create payload carries {leaked} alongside its own `action`. "
        f"`_scheduleBodyToWire` destructures those keys and returns before using them, so they "
        f"never leave the browser — this is issue 268 exactly. Put them in the action's `config`."
    )


def test_draft_payload_keeps_action_config_behind_its_mode_guard() -> None:
    """The same rule for the edit path. `draftToPayload`'s unconditional literal goes out in EVERY
    mode, and only the invoke-agent branch of `_scheduleBodyToWire` has anywhere to put an
    action-config key — so a key in the base literal is silently dropped for every other mode. That
    is how `approval_mode` came to be sent on a `notify` trigger's rename and quietly vanish."""
    edit = _region(_SCHEDULE_FORM, "export function draftToPayload(", "\n  return body\n}")
    base = _object_literal_keys(_region_literal(edit))
    assert len(base) >= 5, f"base literal parsed as {sorted(base)}; the extractor is broken"
    leaked = sorted(_action_config_fields() & base)
    assert not leaked, (
        f"`draftToPayload` puts {leaked} in its UNCONDITIONAL literal. Those are action-config "
        f"keys, so every mode except the one whose action carries them drops them silently. Move "
        f"them inside the mode guard that can actually persist them."
    )


def test_deliberately_unread_rows_are_still_unread() -> None:
    """A stale exception is worse than none: it reads as a documented gap that has actually been
    closed, and it hides the next real one."""
    read_by_either = _handler_reads("_create_schedule") | _handler_reads("_update_schedule")
    sent = _wire_fields()
    for field, reason in _DELIBERATELY_UNREAD.items():
        assert reason.strip(), f"{field} needs a real reason, not a placeholder"
        assert field in sent, (
            f"`{field}` is pinned as deliberately-unread but the form no longer sends it. "
            "Delete the row."
        )
    fully_landed = [
        f
        for f in _DELIBERATELY_UNREAD
        if f in _handler_reads("_create_schedule") and f in _handler_reads("_update_schedule")
    ]
    assert not fully_landed, (
        f"{fully_landed} are read by BOTH handlers now, so the exception is stale — delete the "
        f"row(s) from `_DELIBERATELY_UNREAD`. (read by at least one: "
        f"{sorted(set(_DELIBERATELY_UNREAD) & read_by_either)})"
    )

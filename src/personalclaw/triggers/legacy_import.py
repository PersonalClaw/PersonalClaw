"""A legacy automation file is imported once per home, and what it would run waits for you.

Before `triggers.json` was the one store, three files held automations, each with an engine of its
own: `crons.json` (the schedule service), `event_triggers.json` (data-event triggers) and
`autonudge.json` (a loop's auto-nudge timers). Nothing writes any of them now. A home upgraded from
an older version still carries them, so the boot imports each one into the store.

🔴 WHAT THIS REPLACED (measured on a scratch home before a line of it was written). Every one of
those imports ran on EVERY boot that found its file, and wrote the file's rows as the owner's own
automations. An `event_triggers.json` row that ran `bash` came back switched on, `created_by: user`,
with `bash` frozen into its capability block; a `crons.json` job did the same on every restart and,
because the file was re-read each time, put back its own copy over any edit the owner had made
since; an `autonudge.json` loop was switched back on. None of those files records that the owner
ever allowed what a row runs — the old engines ran whatever the file said — and a file reaches a
home without the owner writing it: a restored snapshot, an imported bundle, a copy from another
machine. A boot is not the owner, so it must not be the one who says yes.

One rule per clause of the contract:

* **At most once per home.** A file is imported once and then renamed `<name>.imported-<date>` —
  kept, never deleted, so renaming it back gives an older build its store again. A copy under that
  name (or `<name>.migrated`, the rename an earlier build used) means the import has happened, so a
  file found again later is left where it is and not read, and the Doctor names it
  (`resilience.doctor`, `automations.legacy_files`).
* **No authority the owner did not give.** A row is written `created_by: import`, with no
  capability block, and without the step keys that loosen whether its agent asks
  (`automation_posture.loosened_keys`: `approval_mode: auto`, `capability: mutating`). A row that
  would run anything needing a grant — every action that is not read-only, and a nudge that types
  into a chat — arrives switched off and waits for review; its action is kept, so the owner can see
  what it would do. Switching it on is the only grant: the Triggers page's toggle asks first
  (`dashboard.handlers.triggers.api_trigger_toggle`) and :func:`adopt` makes the row the owner's.
  Neither the boot's capability backfill nor a chat tool gives one.
* **Idempotent.** Rows keep ids derived from the file, and a row already in the store is left
  exactly as it is, so a second boot, or one after a crash midway, writes nothing twice. The Inbox
  item is derived from the store — the rows still waiting — rather than from the run that imported
  them, so a crash between the import and the announcement still announces, once.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: `Trigger.created_by` for a row a legacy import wrote. `created_by` records WHAT wrote a row, and
#: the answer for these is the import, not the owner: nothing in the file says the owner made it.
IMPORTED_BY = "import"

#: The files an import reads, and what each held — the Doctor's words for them.
LEGACY_FILES: dict[str, str] = {
    "crons.json": "scheduled jobs",
    "event_triggers.json": "data-event triggers",
    "autonudge.json": "auto-nudge loops",
}

#: What an imported file is renamed to: `<name>.imported-<YYYY-MM-DD>`.
RETIRED_MARK = ".imported-"
#: The rename an earlier build gave an imported file. Still an import that happened.
EARLIER_RETIRED_SUFFIX = ".migrated"

_REVIEW_KEY_PREFIX = "legacy_import:"


def retired_copies(home: Path | str, name: str) -> list[Path]:
    """The copies an import of `name` left in `home`, oldest first. Empty when it never ran."""
    root = Path(home)
    found = sorted(p for p in root.glob(f"{name}{RETIRED_MARK}*") if p.is_file() and p.name != name)
    earlier = root / f"{name}{EARLIER_RETIRED_SUFFIX}"
    return ([earlier] if earlier.is_file() else []) + found


def already_imported(home: Path | str, name: str) -> bool:
    """Whether `home` has imported `name` before — the once-per-home check every import asks."""
    return bool(retired_copies(home, name))


def retire(legacy: Path, *, now: float = 0.0) -> Path | None:
    """Rename an imported `legacy` file to `<name>.imported-<date>`. Returns the new path.

    A name already taken gets a counter rather than being replaced: `rename` silently replaces an
    existing target, and the copy it would replace is the record of an earlier import. None when
    the rename failed, which is logged: the file then stays, and the next boot's import finds every
    row already in the store and retires it then.
    """
    day = time.strftime("%Y-%m-%d", time.localtime(now or time.time()))
    target = legacy.with_name(f"{legacy.name}{RETIRED_MARK}{day}")
    counter = 2
    while target.exists():
        target = legacy.with_name(f"{legacy.name}{RETIRED_MARK}{day}-{counter}")
        counter += 1
    try:
        legacy.rename(target)
    except OSError:
        logger.warning("could not rename the imported %s", legacy, exc_info=True)
        return None
    return target


def admit(trigger: Any) -> bool:
    """Strip from an imported row what nobody allowed, in place. True when it waits for review.

    Called on every row an import is about to write, before it is written. The row becomes the
    import's (`IMPORTED_BY`), carries no capability block, and loses the step keys that loosen
    whether its agent asks. If it would run anything that needs a grant it is switched off and
    un-armed, so nothing fires before the owner has looked at it.
    """
    trigger.created_by = IMPORTED_BY
    trigger.capabilities = {}
    _drop_loosened_keys(trigger)
    waits = withholds(trigger)
    if waits:
        trigger.enabled = False
        trigger.next_fire_at = ""
    return waits


def withholds(trigger: Any) -> bool:
    """Whether switching `trigger` on would give it something the owner has not given yet.

    A write-capable action with no grant for it, or a nudge — a message typed into one of the
    owner's chats whenever it goes quiet, which no capability block covers. A read-only action
    needs nothing, which is why an imported row that only notifies keeps its switch as it was.
    """
    from personalclaw.triggers import screen

    if screen.ungranted_providers(trigger):
        return True
    spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
    return getattr(trigger, "kind", "") == "idle" and bool(str(spec.get("message") or "").strip())


def needs_review(trigger: Any) -> bool:
    """Whether `trigger` is an imported row the owner has not switched on yet.

    What the Triggers page badges, what the review item lists, and what a chat resume refuses. The
    owner's own switch-on (:func:`adopt`) makes the row theirs, so it never reads as waiting again.
    """
    if getattr(trigger, "created_by", "") != IMPORTED_BY or getattr(trigger, "enabled", False):
        return False
    return withholds(trigger)


def adopt(trigger: Any) -> None:
    """The owner switched an imported row on: from here it is theirs, like one they made."""
    if getattr(trigger, "created_by", "") == IMPORTED_BY:
        trigger.created_by = "user"


def _step(workflow: dict[str, Any]) -> dict[str, Any]:
    """The step a row runs: its `inline` block, or the flat block an older shape used."""
    inline = workflow.get("inline")
    return inline if isinstance(inline, dict) else workflow


def _drop_loosened_keys(trigger: Any) -> None:
    from personalclaw.automation_posture import loosened_keys

    workflow = getattr(trigger, "workflow", None)
    if not isinstance(workflow, dict):
        return
    step = _step(workflow)
    config = step.get("config")
    if not isinstance(config, dict):
        return
    dropped = loosened_keys(config)
    if dropped:
        step["config"] = {k: v for k, v in config.items() if k not in dropped}


# ── the review item ──


def review_key(home: Path | str) -> str:
    """The review item's dedup key: the imports that have happened in `home`.

    It changes only when a new file is imported, so a dismissed item is not raised again on the
    next boot while the same rows wait, and a later import (a `crons.json` restored into a home
    that had never had one) raises a new one.
    """
    copies = sorted(copy.name for name in LEGACY_FILES for copy in retired_copies(home, name))
    return _REVIEW_KEY_PREFIX + "+".join(copies)


def review_text(waiting: list[Any]) -> tuple[str, str]:
    """The review item's title and body for the rows in `waiting`.

    The body is Markdown — the Inbox and the Notifications page render it, raw HTML included — and
    every name in it comes from the file, which anyone could have written. So a row is a list item
    and each name is a code span (:func:`_literal`): a planted `[Update now](https://…)` reads as
    text, never as a link in an item PersonalClaw raised.
    """
    count = len(waiting)
    if count == 1:
        title = (
            "1 trigger was brought over from an older version and needs your review before it runs"
        )
    else:
        title = (
            f"{count} triggers were brought over from an older version and need your review "
            "before they run"
        )
    lines = [
        f"- {_literal(trigger.name or trigger.id)}: {describe(trigger)}" for trigger in waiting
    ]
    body = "\n".join(
        [
            *lines,
            "",
            f"{'It is' if count == 1 else 'They are'} switched off. Open "
            f"{'it' if count == 1 else 'each one'} on the Triggers page to see what "
            f"{'it' if count == 1 else 'each'} would run, and switch on the ones you want: "
            "PersonalClaw asks you to allow what a trigger runs before it is switched on. A "
            "setting that let one approve its own tool calls or write files was not brought over.",
        ]
    )
    return title, body


def _literal(text: str) -> str:
    """`text` as one Markdown code span, which renders it verbatim: no link, emphasis or HTML.

    The fence is one backtick longer than the longest run inside, and padded when the text starts
    or ends with one, which is how CommonMark lets a code span hold backticks. Whitespace is
    collapsed so a newline in a planted name cannot end the list item and start markup after it.
    """
    flat = " ".join(str(text).split())
    fence = "`" * (max((len(run) for run in re.findall(r"`+", flat)), default=0) + 1)
    pad = " " if flat.startswith("`") or flat.endswith("`") else ""
    return f"{fence}{pad}{flat}{pad}{fence}"


def describe(trigger: Any) -> str:
    """What `trigger` would do, in one Markdown clause: `runs Bash Command on a schedule`."""
    spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
    kind = getattr(trigger, "kind", "")
    if kind == "idle" and str(spec.get("message") or "").strip():
        scope = str(spec.get("scope") or "")
        chat = scope.split(":", 1)[1] if scope.startswith("session:") else scope
        where = f"the chat {_literal(chat)}" if chat.strip() else "a chat"
        return f"types a message into {where} each time it goes quiet"
    when = {"clock": "on a schedule", "event": "when a matching event happens"}.get(kind, "")
    action = f"runs {_action_label(trigger)}"
    return f"{action} {when}" if when else action


def _action_label(trigger: Any) -> str:
    workflow = getattr(trigger, "workflow", None)
    workflow = workflow if isinstance(workflow, dict) else {}
    provider = str(_step(workflow).get("provider") or "").strip()
    if not provider:
        return "a workflow" if workflow.get("ref") else "nothing"
    label = provider_label(provider)
    # A registered provider's name is the product's own words; an unknown one is the file's.
    return _literal(label) if label == provider else label


def provider_label(provider: str) -> str:
    """An action provider's display name (`bash` → `Bash Command`), or its id when none is known.

    Through `dispatchable_action_providers` first: the built-ins register lazily on the first action
    run, so a boot that has run none would otherwise label every action by its bare id.
    """
    try:
        from personalclaw.action_providers.registry import (
            dispatchable_action_providers,
            get_action_provider,
        )

        dispatchable_action_providers()
        found = get_action_provider(provider)
    except Exception:  # noqa: BLE001 - a label must not fail the sentence it is in
        found = None
    label = str(getattr(found, "display_name", "") or "") if found is not None else ""
    return label or provider


def announce(state: Any, *, store: Any, home: Path | str) -> str:
    """Raise the review item for the imported rows still waiting, once. Returns its id, or "".

    Needs the RUNNING inbox (`inbox.live_store`): the dedup check reads it, and a row written to
    the file behind a running service is overwritten by that service's next save. So with no
    dashboard state, or no inbox service yet, this does nothing — the rows keep waiting on the
    Triggers page, and the next boot with a dashboard announces them.
    """
    if state is None:
        return ""
    from personalclaw.inbox import emit_attention_item, live_store

    inbox = live_store(state)
    if inbox is None:
        return ""
    try:
        waiting = [row.trigger for row in store.load() if needs_review(row.trigger)]
    except Exception:  # noqa: BLE001 - the announcement is best-effort; the rows are listed anyway
        logger.warning("legacy import: could not read the trigger store to announce", exc_info=True)
        return ""
    if not waiting:
        return ""
    key = review_key(home)
    # Any status, not only open: a dismissed item is the owner's answer to this import, and raising
    # it again on every boot while the same rows wait would be nagging.
    if any(item.refs.get("dedup_key") == key for item in inbox.items.values()):
        return ""
    title, body = review_text(waiting)
    # The pair is spelled as literals so the registry's emitter sweep can check it
    # (`test_notification_kinds::test_EVERY_EMITTER_IN_THE_TREE_IS_REGISTERED`).
    return emit_attention_item(
        state,
        source="cron",
        kind="trigger_import",
        item_kind="system",
        title=title,
        body=body,
        refs={"triggers": [trigger.id for trigger in waiting]},
        dedup_key=key,
    )


# ── what the Doctor reports ──


@dataclass(frozen=True)
class Reappeared:
    """A legacy file present in a home that has already imported it."""

    name: str
    held: str
    retired: str


def reappeared(home: Path | str) -> list[Reappeared]:
    """Every legacy file back in `home` after its import: ignored, and named by the Doctor."""
    root = Path(home)
    out: list[Reappeared] = []
    for name, held in LEGACY_FILES.items():
        copies = retired_copies(root, name)
        if copies and (root / name).is_file():
            out.append(Reappeared(name=name, held=held, retired=copies[-1].name))
    return out


def audit_import(name: str, *, imported: int, waiting: int, retired: Path | None) -> None:
    """One security-audit row per import: rows written from a file are a change to what can run."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="system",
            operation="trigger.import",
            outcome="imported",
            source="background",
            resources=f"{name}: {imported} imported, {waiting} waiting for review",
            metadata={
                "file": name,
                "imported": imported,
                "waiting_for_review": waiting,
                "renamed_to": retired.name if retired is not None else "",
            },
        )
    except Exception:  # noqa: BLE001 - an audit failure must not undo the import
        logger.debug("legacy import: SEL audit failed for %s", name, exc_info=True)

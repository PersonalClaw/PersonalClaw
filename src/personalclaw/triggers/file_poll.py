"""The file-watch poll runtime — what actually FIRES a `file` trigger.

`file_watch.py` came first: glob expansion, content-hash dedup, the three-way delta. Its own
PARTIAL note recorded why it stopped there — "there is no unified trigger store" to enumerate
`file` triggers from. That store followed, and file triggers became CREATABLE in chat. Measured
here before writing a line: `file_watch.changed_files` has **zero live callers**, and the tick
clock (`service.due_ids`) only surfaces triggers with a `next_fire_at` — a `file` trigger has none,
so nothing ever polls it. The file-change automation is present and inert: the user can create
"when a file in ~/notes changes…" and it will never fire.

This closes that gap and ONLY that gap. It is deliberately DISJOINT from `ScheduleService`, which
fires clock crons and reads no `file` trigger — so wiring this into boot beside it **cannot
double-fire** anything. That is what makes it the additive, completable cutover rather than the
class-B clock switch-over.

**What it owns:** enumerate enabled `file` triggers, poll each one's globs against its persisted
`WatchState`, and hand a real change to the shipped dispatch→executor chain. **What it does not:**
the LLM turn (the executor, injected as a runner) and the clock fire path (`ScheduleService`).

**WatchState is a SIDECAR, not the trigger.** `config_dir()/trigger-watch/<safe-id>.json`, matching
the `trigger-spool.jsonl` / `task_leases/` convention. A watch's hash map is high-churn runtime
state; writing it back onto the trigger entity would rewrite `triggers.json` on every poll and race
every unrelated edit — the same reason leases are sidecars.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_json_write
from personalclaw.triggers.file_watch import WatchState, changed_files, fire_payload, should_fire
from personalclaw.triggers.models import watched_paths
from personalclaw.triggers.provider import armable

logger = logging.getLogger(__name__)

#: How often the poll loop wakes. A watch is not a clock: a minute of latency on "a file changed"
#: is invisible, and polling every glob every few seconds is the `broad_watch_glob` cost
#: `automation doctor` warns about. The file kind is explicitly a poll, not an inotify watch.
POLL_INTERVAL_SECS = 60.0

#: A watch id → filesystem-safe sidecar name. The trigger id carries a `:` (`file:my-notes`), which
#: is legal in a filename on macOS/Linux but breaks on Windows and reads badly in a directory
#: listing, so it is folded to `-` the same way the lease store folds its ids.
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _watch_dir(base_dir: Path | str | None) -> Path:
    from personalclaw.config.loader import config_dir

    root = Path(base_dir) if base_dir else config_dir()
    return root / "trigger-watch"


def _state_path(trigger_id: str, base_dir: Path | str | None) -> Path:
    safe = _SAFE_RE.sub("-", trigger_id) or "watch"
    return _watch_dir(base_dir) / f"{safe}.json"


def load_state(trigger_id: str, *, base_dir: Path | str | None = None) -> WatchState:
    """Revive a watch's persisted state, or an unseeded state if there is none.

    Never raises: a missing or corrupt sidecar degrades to unseeded, which SEEDS on the next poll
    and fires nothing. A watch that crashed the poll loop for every other trigger because one
    sidecar was truncated is the failure this mirrors `WatchState.from_dict`'s own guard to avoid.
    """
    path = _state_path(trigger_id, base_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return WatchState()
    return WatchState.from_dict(raw)


def save_state(trigger_id: str, state: WatchState, *, base_dir: Path | str | None = None) -> None:
    """Persist a watch's state atomically, through the one JSON writer, so a crash mid-write
    cannot corrupt it.

    A half-written hash map read back as unseeded would re-fire the whole directory once, which
    is the reason every store writes through that writer.
    """
    atomic_json_write(_state_path(trigger_id, base_dir), state.to_dict())


def file_triggers(store: Any) -> list[Any]:
    """Enabled, parseable, OWNER-AUTHORED `file` triggers from the store.

    Broken rows are skipped (they load DISABLED under the store's lenient parse), and a disabled row
    is not polled — pausing a watch must actually stop the filesystem work, or "paused" is a lie the
    user pays for on every poll.

    Reads `provider.armable` rather than `store.load()` so a foreign row is never
    polled at all: this loop dispatches straight to the gateway's fire path, so filtering only in
    `service.tick` would leave the `file` kind able to tick for somebody else.
    """
    out = []
    for trigger in armable(store):
        if trigger.kind == "file" and trigger.enabled:
            out.append(trigger)
    return out


def poll_one(trigger: Any, *, base_dir: Path | str | None = None) -> dict[str, Any] | None:
    """Poll one `file` trigger. Returns a fire payload when it should fire, else None.

    The seeding pass NEVER fires: a freshly enabled watch that reported every existing file as new
    would run the automation over the whole directory the first time — `WatchState.seeded` exists
    precisely to prevent that, and it is the caller's job (here) to honour it. State is persisted
    on every poll, seeding included, so the seed is remembered across a restart.
    """
    paths = watched_paths(trigger.spec)
    if not paths:
        # Refused where it is made (`models.validate_spec`), and a stored one loads broken and
        # switched off. One that reaches here anyway is skipped rather than made to scan the cwd.
        logger.debug("file trigger %s has no paths; skipping", trigger.id)
        return None

    state = load_state(trigger.id, base_dir=base_dir)
    dedup = trigger.spec.get("dedup") if isinstance(trigger.spec, dict) else None
    delta, new_state = changed_files(paths, state)
    save_state(trigger.id, new_state, base_dir=base_dir)

    if not should_fire(delta):
        return None
    payload = fire_payload(delta, trigger_id=trigger.id, trigger_name=trigger.name)
    # The dedup mode rides along so the executor's workflow sees whether content or mtime decided
    # the change — a content-dedup automation that summarizes only real edits needs to know.
    payload["dedup"] = str(dedup or "content")
    return payload


def poll_all(store: Any, *, base_dir: Path | str | None = None) -> list[dict[str, Any]]:
    """Poll every enabled `file` trigger once. Returns the fire payloads to dispatch.

    One trigger's failure (a glob over an unreadable tree, a vanished mount) must not strand the
    rest: each poll is isolated, because a poll loop that died on one bad watch would silently stop
    firing every other file automation the user has.

    Each fire is counted here, where it is decided (`run_record.note_fire`), as an admission
    counts a clock fire: nothing else would, and a file trigger that had run read as never fired.
    """
    from personalclaw.triggers.run_record import note_fire

    fires: list[dict[str, Any]] = []
    for trigger in file_triggers(store):
        try:
            payload = poll_one(trigger, base_dir=base_dir)
        except Exception:  # noqa: BLE001 - one bad watch must not stop the loop for the others
            logger.warning("file-watch poll failed for %s", trigger.id, exc_info=True)
            continue
        if payload is not None:
            note_fire(store, trigger.id, at=time.time())
            fires.append(payload)
    return fires

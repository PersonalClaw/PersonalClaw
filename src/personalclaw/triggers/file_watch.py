"""The `file` trigger kind's runtime — poll, dedup, delta payload (AUTO §7 crit 2 — S83).

Criterion 2: "*When a file in ~/notes changes, summarize it into my knowledge base*" is creatable in
chat in one message.

**Measured before writing.** The `file` kind is fully DECLARED — it is in `models.KINDS`, its spec
keys are `{paths, dedup}`, a `file` trigger parses and stays `enabled=True` — and nothing watches a
filesystem on its behalf. All nine `schedule_*` chat tools are clock-only (`schedule_natural`
converts a cadence to cron; nothing expresses "when a file changes"), and the trigger handler has
zero references to the `file` kind. So a user could author one through the API and it would never
fire.

**This reuses `fs_watch.ConfigFsWatcher`'s mechanism rather than adding a dependency.** That module
already solved this problem for the config tree: poll + `(mtime, size)` signature, a SEEDED first
pass so startup does not report "everything changed", and deletions detected as "seen before, gone
now". `watchdog` is not a dependency of this project and adding one for a trigger kind would be a
platform-specific runtime (inotify/FSEvents/kqueue) in a package that currently runs anywhere.

**What this module adds over that watcher, and why each is required by the plan:**

* **Glob roots, not directory roots.** §2's table specifies `{paths: [globs]}`. `~/notes/**` has to
  expand, and `~` has to expand too — a trigger authored in chat will contain a tilde.
* **CONTENT-HASH dedup, keyed on `(path, content_hash)`** — the plan says explicitly "not path-only
  (R12)". A path-only key re-fires when a file is touched, rewritten identically, or saved twice by
  an editor; the hash makes a no-op save a no-op fire.
* **A changed-file DELTA payload**, so "fired workflows foreach only over new items" (§2). The fire
  carries the specific paths, not "something under ~/notes changed".
* **A `vcs` preset** watching `.git/refs/heads/*` for on-commit automations, also from §2's table.

Pure mechanics: this decides WHAT changed and whether it is new. Firing the action is the trigger
service's job, exactly as `project_occurrences` computes a week without dispatching anything.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Max files one watch may track. A `~/**` glob on a real home directory is hundreds of thousands
#: of paths, and hashing them every poll would make the gateway unusable — the failure mode
#: `automation doctor` already flags as `broad_watch_glob`. The cap is REPORTED (`truncated`),
#: never silent: a partial watch that looked complete is the S65 rule that keeps being re-learned
#: on new surfaces.
MAX_WATCHED_FILES = 2_000

#: Bytes hashed per file. A full hash of a 2 GB video is a stalled poll loop; the first 64 KiB
#: plus the size is enough to distinguish an edit from a touch, which is what the dedup key needs.
#: Content appended past the window still changes the SIZE, so a growing log is not missed.
HASH_BYTES = 65_536

#: The `vcs` preset from §2's table: watch the refs that move on commit. Relative to a repo root,
#: so a caller supplies the repo and this supplies the pattern — the alternative is every user
#: hand-writing a glob into `.git`, which is exactly the kind of detail an automation should not
#: need.
VCS_GLOBS: tuple[str, ...] = (".git/refs/heads/*", ".git/HEAD")


def expand_globs(patterns: list[str] | tuple[str, ...], *, base: Path | None = None) -> list[Path]:
    """Every existing FILE matching `patterns`, sorted, deduped.

    `~` is expanded because a chat-authored trigger says `~/notes/**` — a literal `~` directory is
    not what the user meant, and silently watching nothing is the worst outcome. Relative patterns
    resolve against `base` (the repo root for the `vcs` preset, cwd otherwise).

    Directories are skipped: a directory's mtime changes when any child changes, so including it
    would double-fire alongside the child that actually changed.
    """
    root = base or Path.cwd()
    seen: dict[str, Path] = {}
    for raw in patterns or ():
        pattern = str(raw or "").strip()
        if not pattern:
            continue
        pattern = os.path.expanduser(pattern)
        try:
            if os.path.isabs(pattern):
                # `Path.glob` refuses absolute patterns, so anchor at the filesystem root and make
                # the pattern relative to it.
                anchor = Path(Path(pattern).anchor)
                matches = anchor.glob(str(Path(pattern).relative_to(anchor)))
            else:
                matches = root.glob(pattern)
            for p in matches:
                if p.is_file():
                    seen[str(p)] = p
        except (OSError, ValueError, IndexError):
            # A malformed pattern watches nothing rather than raising: a trigger with a typo'd glob
            # must not take down the poll loop that serves every other trigger.
            logger.debug("file-watch pattern %r could not be expanded", pattern)
    return [seen[k] for k in sorted(seen)]


def content_hash(path: Path) -> str:
    """A cheap content signature: size + the first `HASH_BYTES`.

    Returns "" for an unreadable path, which `changed_files` treats as a deletion rather than a
    change — a permissions error is not an edit, and reporting one as new content would fire an
    automation against a file it cannot read.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            head = fh.read(HASH_BYTES)
    except OSError:
        return ""
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    digest.update(head)
    return digest.hexdigest()


@dataclass
class WatchState:
    """What a watch has already seen. Serialized onto the trigger's state by the caller.

    `hashes` is `path -> content_hash`, which IS the dedup key the plan requires: "(path,
    content_hash), not path-only (R12)". Keeping a map rather than a set of composite strings lets
    a changed file replace its own entry instead of accumulating one row per version.
    """

    hashes: dict[str, str] = field(default_factory=dict)
    #: False until the first scan completes, so a fresh watch does not report every existing file
    #: as new. The bug `ConfigFsWatcher` documents as "no spurious everything-changed storm on
    #: startup" — for a trigger it would be worse: an automation firing over a whole directory the
    #: first time it is enabled.
    seeded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"hashes": dict(self.hashes), "seeded": self.seeded}

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "WatchState":
        """Revive persisted state. Never raises on a malformed record.

        The `isinstance` check guards the ITERATION, not each item — a first pass put it inside the
        comprehension, where `.items()` had already been called on a string and raised. A corrupt
        state record must degrade to "unseeded" (which seeds and fires nothing) rather than crash
        the poll loop for every other trigger.
        """
        data = raw if isinstance(raw, dict) else {}
        hashes = data.get("hashes")
        pairs = hashes.items() if isinstance(hashes, dict) else ()
        return cls(
            hashes={str(k): str(v) for k, v in pairs},
            seeded=bool(data.get("seeded")),
        )


@dataclass
class Delta:
    """One poll's result: what changed, and whether the watch is complete.

    Separate `added`/`modified`/`removed` rather than one list, because §2 says fired workflows
    "foreach only over new items" — a summarize-on-change automation wants added and modified, and
    a cleanup automation wants removed. One merged list would force every consumer to re-derive
    this.
    """

    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    #: True when the glob matched more than `MAX_WATCHED_FILES`. Named on the delta so a caller can
    #: surface it; a partial watch that looks complete is a lie about coverage.
    truncated: bool = False
    #: True for the seeding pass. A seeding scan reports NO changes by construction, and a caller
    #: that could not tell "nothing changed" from "first look" would either fire on startup or
    #: never learn it was seeded.
    seeding: bool = False

    @property
    def changed(self) -> list[str]:
        """Added + modified, sorted — the "new content" set most automations act on."""
        return sorted(self.added + self.modified)

    @property
    def any_change(self) -> bool:
        return bool(self.added or self.modified or self.removed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": list(self.added),
            "modified": list(self.modified),
            "removed": list(self.removed),
            "changed": self.changed,
            "truncated": self.truncated,
            "seeding": self.seeding,
        }


def changed_files(
    patterns: list[str] | tuple[str, ...],
    state: WatchState,
    *,
    base: Path | None = None,
    cap: int = MAX_WATCHED_FILES,
) -> tuple[Delta, WatchState]:
    """One poll pass. Returns `(delta, new_state)` — pure apart from reading the filesystem.

    Returns a NEW state rather than mutating, so a caller that fails to persist cannot
    half-advance a watch: either the new state is stored and the delta is consumed, or neither
    happened. A mutating version would drop fires on a crash between poll and persist.
    """
    files = expand_globs(patterns, base=base)
    truncated = len(files) > cap
    if truncated:
        # Deterministic truncation (sorted order), so the same subset is watched every poll. A
        # random or set-ordered subset would make files appear and disappear from the watch, which
        # reads as constant churn.
        files = files[:cap]

    current: dict[str, str] = {}
    for path in files:
        digest = content_hash(path)
        if digest:
            current[str(path)] = digest

    if not state.seeded:
        return (
            Delta(truncated=truncated, seeding=True),
            WatchState(hashes=current, seeded=True),
        )

    added: list[str] = []
    modified: list[str] = []
    for key, digest in current.items():
        prior = state.hashes.get(key)
        if prior is None:
            added.append(key)
        elif prior != digest:
            modified.append(key)
    removed = [key for key in state.hashes if key not in current]

    return (
        Delta(
            added=sorted(added),
            modified=sorted(modified),
            removed=sorted(removed),
            truncated=truncated,
        ),
        WatchState(hashes=current, seeded=True),
    )


def fire_payload(delta: Delta, *, trigger_id: str = "", trigger_name: str = "") -> dict[str, Any]:
    """The payload a `file`-kind fire carries.

    Carries the PATHS, not their contents. A fired workflow reads what it needs through the normal
    file tools, under the normal capability checks; passing file bodies through a trigger payload
    would route arbitrary disk content into an action's arguments — the same rule the lifecycle
    payloads
    follow (S82).
    """
    return {
        "trigger_id": trigger_id,
        "trigger_name": trigger_name,
        "kind": "file",
        "changed": delta.changed,
        "added": list(delta.added),
        "modified": list(delta.modified),
        "removed": list(delta.removed),
        "count": len(delta.changed) + len(delta.removed),
        "truncated": delta.truncated,
    }


def should_fire(delta: Delta) -> bool:
    """Whether this delta warrants a fire.

    A SEEDING pass never fires — that is the whole point of the seeded flag. Neither does an empty
    delta, which is the common case on a quiet directory: a trigger that fired every poll interval
    regardless would be a timer wearing a file-watch costume.
    """
    return not delta.seeding and delta.any_change


def vcs_patterns(repo_root: str | Path) -> list[str]:
    """§2's `vcs` preset — the globs that move on commit, rooted at `repo_root`.

    `.git/HEAD` is included alongside `refs/heads/*` because a branch SWITCH moves HEAD without
    touching any ref, and an on-commit automation that ignored it would fire on the next commit
    with a stale idea of which branch it is on.
    """
    root = Path(os.path.expanduser(str(repo_root)))
    return [str(root / pattern) for pattern in VCS_GLOBS]

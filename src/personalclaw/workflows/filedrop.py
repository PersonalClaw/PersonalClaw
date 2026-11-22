"""Per-run inbound file drop and outbound artifact listing.

The two directions a run exchanges files with its human, kept in ONE module because they are one
feature with one enable/disable decision — split across two, "drop is on but the outbox is off"
becomes a state nobody designed.

**In — the file drop.** Approval-gated multipart ingestion. Files land in the run's
``dropped/`` zone, whose lifecycle is ``immutable``: the agent may READ what a human handed
it and may not rewrite it, because reference material an agent can edit is reference material that
cannot be cited later. A step reads what was dropped through ``{{run.dropped}}``
(:func:`dropped_for_steps`), and only there: each file's text passes the one door text from
outside takes into a prompt (``outside_text.admit``), read by the injection screen and then fenced
with its name, since a dropped file is by definition content the operator did not author. A
step's file tools do not reach the zone.

Named "file drop", not "inbox": PersonalClaw already has an Inbox feature and a second noun with the
same name would collide in every conversation about either.

**Out — the outbox.** The run's PUBLISHED-artifact listing, derived from what ``apply_publish``
recorded rather than from a second registry: an outbox that maintained its own list would drift from
the artifacts the run actually created, and the drift would always favour claiming more than
happened.

Pure-ish functions plus a thin filesystem layer, matching ``publish.py``: the gating and naming
rules are testable without a gateway.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personalclaw import bounded_log
from personalclaw.workflows import store

logger = logging.getLogger(__name__)

#: The run-dir subdirectory dropped files land in. A sibling of ``outputs``/``artifacts`` rather
#: than inside them: those are the run's OWN products, and mixing human-supplied input into them
#: would make "did the agent produce this?" unanswerable from the layout.
DROP_DIR = "dropped"

#: The drop manifest filename. One JSON record per ingested file, so the listing does not have to
#: re-hash the whole zone on every read — and so an ingestion's audit facts (who, when, what size)
#: survive independently of the file's mtime, which a later copy would reset.
DROP_MANIFEST = "manifest.json"

#: Per-run ceiling on dropped files. A drop zone is reference material, not a bulk-upload target;
#: without a cap the run dir becomes an unbounded store the snapshot machinery then tries to copy.
MAX_DROPPED_FILES = 50

#: Characters a stored filename may keep. Everything else collapses to ``_``: the name is used to
#: build a path under the run dir, and a name carrying a separator is a traversal, not a filename.
_SAFE_NAME_RE = re.compile(r"[^\w.\-]")

#: The spec key that turns the drop on and names auto-accepted MIME types. Absent = drop DISABLED,
#: deliberately: a run that never declared it accepts files should not accept files because a route
#: exists. The disabled response is honest about why rather than a bare 404.
SPEC_KEY = "file_drop"


def safe_filename(raw: str) -> str:
    """A dropped file's stored name — basename only, sanitized, never empty, and never the drop's
    own manifest (:data:`DROP_MANIFEST`), which sits in the same folder: a file dropped under that
    name overwrote the manifest, and the manifest's next write overwrote the file."""
    base = Path(str(raw or "")).name
    cleaned = (_SAFE_NAME_RE.sub("_", base).strip("._") or "dropped")[:120]
    return f"dropped_{cleaned}" if cleaned.lower() == DROP_MANIFEST else cleaned


@dataclass
class DropPolicy:
    """Whether a run accepts dropped files, and which of them skip the human gate.

    ``auto_accept_mimes`` is the template's declaration, NOT a user preference: the author of a
    workflow that ingests screenshots every run should not have to approve each one, while nothing
    the author did not name gets in without a human saying so. An empty list means every file is
    gated, which is the right default for a feature whose whole risk is untrusted input.
    """

    enabled: bool = False
    auto_accept_mimes: list[str] = field(default_factory=list)
    reason: str = ""

    def auto_accepts(self, mime: str) -> bool:
        m = (mime or "").strip().lower()
        if not m:
            return False
        for allowed in self.auto_accept_mimes:
            a = allowed.strip().lower()
            if not a:
                continue
            if a.endswith("/*") and m.startswith(a[:-1]):
                return True
            if a == m:
                return True
        return False


def parse_policy(spec: dict[str, Any] | None) -> DropPolicy:
    """Read a run spec's ``file_drop`` block into a policy.

    A MALFORMED block disables the drop and says so, rather than falling back to enabled-with-
    defaults. The failure mode of guessing here is accepting untrusted files into a run whose author
    wrote something they thought was restrictive.
    """
    raw = (spec or {}).get(SPEC_KEY)
    if raw is None:
        return DropPolicy(reason="this workflow does not declare a file drop")
    if raw is True:
        return DropPolicy(enabled=True)
    if raw is False:
        return DropPolicy(reason="the workflow disabled its file drop")
    if not isinstance(raw, dict):
        return DropPolicy(reason=f"{SPEC_KEY} must be a boolean or an object")
    if raw.get("enabled") is False:
        return DropPolicy(reason="the workflow disabled its file drop")
    mimes = raw.get("auto_accept_mimes") or []
    if not isinstance(mimes, list):
        return DropPolicy(reason="auto_accept_mimes must be a list of MIME types")
    return DropPolicy(enabled=True, auto_accept_mimes=[str(m) for m in mimes if str(m).strip()])


def approval_required(
    policy: DropPolicy, mime: str, *, confirmed: bool, by_app: bool = False
) -> tuple[bool, str]:
    """Whether this file still needs a human, and what the human is being asked to accept.

    Ordered so an auto-accepted MIME never consults ``confirmed``: a template that declared
    ``image/*`` gets its screenshots without a prompt, and a caller cannot use ``confirm:true`` to
    widen that declaration to types the author excluded — the confirm flag ANSWERS the gate, it does
    not bypass the policy. ``confirmed`` is the owner's own confirmation, never a sender's
    (``handlers.api_run_drop``).

    🔴 A file an app hands a run (*by_app*) always waits for her, whatever the template
    auto-accepts: a step reads what was dropped (``{{run.dropped}}``), and an app's own template
    could auto-accept ``text/*`` and let in every file it sent without her.
    """
    if by_app:
        return True, "a file an app hands this run waits for you: it lands only when you drop it"
    if policy.auto_accepts(mime):
        return False, "auto-accepted by the workflow's declared MIME types"
    if confirmed:
        return False, "approved by the operator"
    return True, "this workflow gates every file it did not declare as auto-accepted"


def drop_dir(run_id: str) -> Path:
    return store.run_dir(run_id) / DROP_DIR


def _manifest_path(run_id: str) -> Path:
    return drop_dir(run_id) / DROP_MANIFEST


def read_manifest(run_id: str) -> list[dict[str, Any]]:
    path = _manifest_path(run_id)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("run %s: unreadable drop manifest", run_id, exc_info=True)
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def record_drop(run_id: str, entry: dict[str, Any]) -> None:
    """Append one ingestion record, replacing any earlier record for the same stored name.

    Replace-by-name rather than append-always: a re-drop of the same filename overwrote the bytes on
    disk, so two manifest rows would disagree about a single file's size and digest, and the older
    row is the one that lies.
    """
    from personalclaw.atomic_write import atomic_write

    rows = [r for r in read_manifest(run_id) if r.get("filename") != entry.get("filename")]
    rows.append(entry)
    path = _manifest_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = bounded_log.newest(rows, MAX_DROPPED_FILES, at="accepted_at")
    atomic_write(path, json.dumps(kept, indent=2))


def store_dropped_bytes(run_id: str, filename: str, data: bytes) -> dict[str, Any]:
    """Land one dropped file in the run's immutable zone and return its manifest record.

    Written through ``atomic_write_bytes`` so a partial write never presents itself as a
    complete reference file, and containment-checked against the drop dir even though the name is
    already sanitized — the sanitizer and the containment check fail differently, and the one that
    matters here is the one that cannot be argued about.
    """
    from personalclaw.atomic_write import atomic_write_bytes

    safe = safe_filename(filename)
    ddir = drop_dir(run_id)
    ddir.mkdir(parents=True, exist_ok=True)
    dest = ddir / safe
    if not dest.resolve().is_relative_to(ddir.resolve()):
        raise ValueError(f"dropped filename escapes the drop dir: {filename!r}")
    atomic_write_bytes(dest, data)
    return {
        "filename": safe,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "lifecycle": "immutable",
    }


#: How much of one dropped file's text a step is handed: 64 KiB of characters. Reference material
#: past that is a document to work through in steps, not a prompt's paragraph.
MAX_FILE_CHARS = 64 * 1024

#: How much text ``{{run.dropped}}`` hands a step for all the run's files together. Fifty files of
#: 64 KiB would be 3 MiB in every prompt that binds it; a file past this is named and left out.
MAX_DROPPED_CHARS = 128 * 1024

#: What ``{{run.dropped}}`` reads in a run that accepts files and has been handed none yet: a
#: sentence the step can act on, rather than an empty string a prompt reads past.
NOTHING_DROPPED = "[No file has been dropped into this run yet.]"


def dropped_for_steps(run_id: str, spec: dict[str, Any] | None) -> str | None:
    """What ``{{run.dropped}}`` hands a step: each file dropped into the run, oldest first, its
    text read by the injection screen and fenced with its name (:func:`dropped_text`), or
    :data:`NOTHING_DROPPED`. None when the run's workflow accepts no files, where the field is
    absent and a read of it says so.

    Read when a step that binds it starts, so a file dropped while a step runs reaches the steps
    after it. A file the drop's text has no room left for (:data:`MAX_DROPPED_CHARS`) is named and
    left out, and so is one that is not text.
    """
    if not parse_policy(spec).enabled:
        return None
    rows = sorted(read_manifest(run_id), key=lambda r: str(r.get("accepted_at") or ""))
    if not rows:
        return NOTHING_DROPPED
    parts = [f"The files dropped into this run ({len(rows)}):"]
    room = MAX_DROPPED_CHARS
    for row in rows:
        name = safe_filename(str(row.get("filename") or ""))
        if room <= 0:
            parts.append(
                f"[{name} was left out: the files before it hold all the text a step is handed"
                " of this drop.]"
            )
            continue
        text = dropped_text(run_id, name, limit=min(MAX_FILE_CHARS, room))
        room -= len(text)
        parts.append(text)
    return "\n\n".join(parts)


def dropped_text(run_id: str, filename: str, *, limit: int = MAX_FILE_CHARS) -> str:
    """One dropped file as a step is handed it: its text read by the injection screen and fenced
    with its name (``outside_text.admit``), or a sentence saying why it is not there (the screen
    refused it, it is not text, or it is gone).

    At the READ rather than at ingestion, so the bytes on disk stay exactly what the human handed
    over (a fenced-on-disk file could not be diffed against its original), while no step reaches
    the content without the screen and the fence. The two rules only compose in this order.
    """
    from personalclaw.outside_text import admit, withheld

    name = safe_filename(filename)
    try:
        data = (drop_dir(run_id) / name).read_bytes()
    except OSError:
        return f"[{name} is no longer in this run's drop.]"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    if not data:
        return f"[{name} is empty.]"
    if not text or "\x00" in text:
        return f"[{name} is not text, so only its name is handed on.]"
    cut = text[:limit]
    admitted = admit(cut, source=f"dropped file {name}", source_type="file_drop", source_id=run_id)
    if admitted.refused:
        return withheld(f"The dropped file {name}", admitted.refused)
    if len(cut) < len(text):
        return f"{admitted.text}\n[{name}: its first {len(cut):,} characters of {len(text):,}.]"
    return admitted.text


def outbox_entries(run_id: str) -> list[dict[str, Any]]:
    """The run's published artifacts, newest-first, with the content type each renders through.

    Derived from the run's OWN journal of publish outcomes, so the listing cannot claim an artifact
    the run did not publish. The ``kind`` rides along because the FE resolves its preview through
    the
    ``contentTypes`` registry — the listing declares the type and never the renderer.
    """
    seen: dict[str, dict[str, Any]] = {}
    for record in store.read_jsonl(run_id, "publishes.jsonl"):
        slug = str(record.get("slug") or "")
        if not slug:
            continue
        seen[slug] = {
            "slug": slug,
            "artifact": str(record.get("artifact") or ""),
            "kind": str(record.get("kind") or ""),
            "action": str(record.get("action") or ""),
            "change_note": str(record.get("change_note") or ""),
            "node_id": str(record.get("node_id") or ""),
            "updated_at": str(record.get("ts") or ""),
            "self_contained": bool((record.get("media") or {}).get("self_contained", True)),
        }
    rows = list(seen.values())
    rows.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return rows

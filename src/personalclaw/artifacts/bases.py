"""What a write over an artifact's body is based on, and who made the body it would replace.

A write that replaces an artifact's whole body is built from a copy of it: the agent read it with
``artifact_get``, the owner's editor loaded it, an app fetched it. When the body changed after
that copy was read (the owner saved an edit in the Artifacts editor, an app wrote it, the agent in
another chat made its next version), writing the copy back undoes that change without a word. So
every read the agent is given names its **base**, and a write over an existing artifact names the
base it was built from:

* :func:`base_of` is the base of a body as a read showed it: ``v<version>-<revision>``. The version
  is the number the owner sees; the revision (``stale_write.revision_of`` of the masked body, the
  revision the Artifacts editor's own saves name) is what makes it exact, because an edit saved
  without a new version (a plain Save, a workflow's refresh) changes the body and keeps the number.
  A binary body's read is the URL of its version, so its base changes with every version, which
  is every write it takes.
* :func:`parse_base` reads one back, :func:`names_a_base` says whether a call names one at all, and
  :func:`base_outcome` is how the audit log records a write refused for the base it names.

The store compares a base under its lock (``update(expect_revision=…)`` for a text body,
``update_binary(expect_version=…)`` for a binary one), so nothing lands between the comparison and
the write.

:func:`newest_body_change` names who made the body a write would replace: the newest event that
changed it. A refused write names that writer. And whoever writes over a body another writer left
that no version holds, the store first keeps it as a version of its own
(``NativeArtifactProvider.update``), and :func:`kept_change` names that version to the writer: a
current base proves the writer read the body, not that what it wrote kept it, and a write built
from no copy at all (a workflow's, which writes what its run made) never read it. Only the owner's
own save from her editor replaces what the editor showed her as it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.artifacts.models import Artifact

__all__ = [
    "BODY_EVENT_TYPES",
    "BodyChange",
    "base_for",
    "base_of",
    "kept_change",
    "newest_body_change",
    "base_outcome",
    "names_a_base",
    "parse_base",
]

#: The events that change an artifact's body or cut a version of it. ``referenced`` records a read
#: and changes nothing.
BODY_EVENT_TYPES = frozenset({"created", "edited", "iterated", "reverted"})

#: ``v3-1a2b3c4d5e6f7a8b``: the version, then the 16 hex digits of the body's revision. The ``v``
#: is optional on the way back in, since a model may drop it.
_BASE_RE = re.compile(r"^v?(\d+)-([0-9a-f]{16})$")


def base_of(version: int, shown: str) -> str:
    """The base of a body a read showed as *shown* (already masked, as every read hands it out)
    at *version*."""
    from personalclaw.stale_write import revision_of

    return f"v{version}-{revision_of(shown)}"


def base_for(art: Artifact) -> str:
    """The base of *art* as it was read: its version and its body, masked as a read shows it.

    *art* is what a read or a write of the store returned, so ``content`` is the body (for a
    binary kind, the URL of the version it names).
    """
    from personalclaw.artifacts.models import redacted

    return base_of(art.version, redacted(art.content or ""))


def parse_base(raw: object) -> tuple[int, str] | None:
    """``(version, revision)`` of a base as :func:`base_of` writes it, or ``None`` when *raw* is
    not one. Quotes and surrounding space a caller added are ignored."""
    if not isinstance(raw, str):
        return None
    found = _BASE_RE.match(raw.strip().strip("'\"").strip().lower())
    if found is None:
        return None
    return int(found.group(1)), found.group(2)


def names_a_base(raw: object) -> bool:
    """Whether a call names a base at all: an absent or blank one names none."""
    return raw is not None and bool(str(raw).strip())


def base_outcome(raw: object) -> str:
    """The audit outcome of a write refused for the base *raw* before the store was asked, in the
    two words every whole-document write records (`stale_write.refusal_outcome`): a call that names
    no base is refused for want of one, and a base other than the version the artifact holds now
    (an older one, one that is not a base, a version it never had) is the stale refusal, as an
    ``If-Match`` that is not the current revision is."""
    from personalclaw.stale_write import OUTCOME_REVISION_REQUIRED, OUTCOME_STALE_WRITE

    return OUTCOME_STALE_WRITE if names_a_base(raw) else OUTCOME_REVISION_REQUIRED


@dataclass(frozen=True)
class BodyChange:
    """Who made an artifact's live body, as its store recorded it.

    ``by`` is the writer's label (``user`` for the owner, ``app:<name>`` for an app, ``agent``
    for the agent, ``workflow`` for a workflow's step), ``session_id`` the chat it came from,
    ``kind`` the event (created, edited, iterated or reverted), ``version`` the version the body
    is at and ``from_version`` the version a revert restored. ``outside`` is True when the body is
    a file that changed outside the artifact store since it last wrote it: then ``by`` is empty,
    since nothing recorded who changed it.
    """

    by: str = ""
    session_id: str = ""
    kind: str = ""
    version: int = 0
    from_version: int = 0
    run_id: str = ""
    outside: bool = False


def newest_body_change(art: Artifact) -> BodyChange | None:
    """The newest event in *art*'s timeline that changed its body, or ``None`` when it records
    none (an artifact whose events were all trimmed away)."""
    for event in reversed(art.events):
        if event.type in BODY_EVENT_TYPES:
            run_id = event.metadata.get("run_id") if isinstance(event.metadata, dict) else ""
            return BodyChange(
                by=event.by,
                session_id=event.session_id,
                kind=event.type,
                version=event.version,
                from_version=event.from_version,
                run_id=str(run_id or ""),
            )
    return None


def kept_change(art: Artifact, floor: int, writer: str) -> BodyChange | None:
    """The version the store cut of someone else's text before *writer*'s write replaced it, as
    who made that text, or ``None`` when the write cut none.

    The store records it as an ``edited`` event naming the writer it was kept from
    (``kept_before``), newer than version *floor*, the version the artifact was at before the write
    (``NativeArtifactProvider._keep_unversioned``).
    """
    for event in reversed(art.events):
        if event.version <= floor:
            break
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        if metadata.get("kept_before") == writer:
            run_id = metadata.get("run_id") or ""
            return BodyChange(
                by=event.by,
                session_id=event.session_id,
                kind=event.type,
                version=event.version,
                run_id=str(run_id),
            )
    return None

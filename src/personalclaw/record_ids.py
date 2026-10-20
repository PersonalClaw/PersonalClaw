"""The ONE resolver from an untrusted record id to a path inside its store.

**Why this module exists.** Every JSON-file store in PersonalClaw named its records
by concatenating an id into a directory: ``_dir() / f"{record_id}.json"``. Thirty-odd
sites did it, each with its own idea of whether the id had been checked, and four were
reachable from a route parameter with no check at all. Because ``pathlib``'s ``/``
**discards the left operand when the right side is absolute**, that expression is not
a join — it is an unbounded filesystem address::

    base / "p-abc"        → /…/projects/p-abc
    base / "/tmp/zz56"    → /tmp/zz56              ← base discarded
    base / "../../../tmp" → /…/projects/../../../tmp

Measured consequences, all reproduced against live routes: ``DELETE /api/projects``
``shutil.rmtree``-ing an arbitrary directory (#455), ``GET``/``DELETE /api/tasks/{id}``
reading and unlinking any ``.json`` on the disk (#471), and the same read+unlink pair
through the learning and skill proposal stores (#459). One class, four proven doors.

**The containment lives in the store, not the route.** A check in the handler layer
would leave the MCP tools, the workflow action providers and the CLI as unguarded doors
to the same stores — the reasoning ``artifacts/native.py`` already records for its own
guard. :func:`record_path` is therefore called by the path builder every store already
has, so a new caller of that store inherits the guard instead of having to remember it.

**:class:`UnsafeRecordId` is deliberately NOT a ``ValueError``.** The stores swallow
``(OSError, ValueError, TypeError)`` — or bare ``Exception`` — around their reads and
return ``None``, which is how this class survived: a traversal attempt answered ``404``
and was indistinguishable from a missing record, so probes recorded "this store
validates its ids" when it did not (#459 documents nearly missing it for exactly this
reason). A refusal that reads as *absence* is a refusal nobody can audit. Basing the
error on ``Exception`` directly means those enumerated ``except`` clauses do not catch
it and the refusal propagates to the caller, which maps it to a ``400``. Do not
"tidy" it under ``ValueError``.

**What a safe id is.** Exactly one path segment: non-empty, within
:data:`MAX_RECORD_ID_LEN`, no path separator (either platform's), no ``.``/``..``
segment, no NUL, and not absolute. That is a deliberately *narrower* rule than
"resolves inside the root" — a resolved-containment check alone still accepts
``sub/dir/id``, which silently invents a subdirectory layout no store reads back.
Containment is then asserted anyway, as defense in depth, because the shape rule is a
statement about ids and the containment is a statement about the filesystem, and only
the second one is still true after a symlink.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "MAX_RECORD_ID_LEN",
    "UnsafeRecordId",
    "is_path_in_store",
    "is_safe_record_id",
    "is_safe_relative_path",
    "record_path",
    "require_safe_record_id",
]

# One path segment on every filesystem PersonalClaw supports (ext4/APFS/NTFS all cap a
# component at 255 bytes). A longer id cannot name a file, so accepting it only defers
# the failure to an ENAMETOOLONG deep inside a write — the shape of #652.
MAX_RECORD_ID_LEN = 200

# Both separators always, not `os.sep`: a store written on Linux is read on Windows via
# a synced home, and `\` is a separator there. Checking only the host's separator makes
# the guard's strength depend on where it runs.
_SEPARATORS = ("/", "\\", os.sep, os.altsep or "/")


class UnsafeRecordId(Exception):
    """An untrusted record id could not be resolved to a path inside its store.

    Subclasses ``Exception`` and not ``ValueError`` on purpose — see this module's
    docstring. Handlers map it to a ``400`` with code ``invalid_id``; a store that
    catches it and returns ``None`` turns an auditable refusal back into a ``404``.
    """


def is_safe_record_id(record_id: object) -> bool:
    """True when ``record_id`` is a single, safe path segment.

    Total on any input — callers hand this raw JSON bodies and route parameters, so a
    non-string is a refusal rather than a ``TypeError``.
    """
    if not isinstance(record_id, str):
        return False
    if not record_id or len(record_id) > MAX_RECORD_ID_LEN:
        return False
    if record_id in (".", ".."):
        return False
    if "\x00" in record_id:
        return False
    if any(sep in record_id for sep in _SEPARATORS if sep):
        return False
    # `C:x` is drive-relative on Windows and `Path("C:x").is_absolute()` is False there,
    # so the separator check above is not sufficient on its own.
    return not Path(record_id).is_absolute() and not os.path.splitdrive(record_id)[0]


def require_safe_record_id(record_id: object, *, kind: str = "id") -> str:
    """Return ``record_id`` unchanged, or raise :class:`UnsafeRecordId`.

    Use at a store's public entry point when the path builder is several frames down
    and a broad ``except`` sits between them — the refusal must reach the caller, not
    become a ``None``. ``kind`` names the parameter in the message (``"task_id"``), so
    the ``400`` tells the client which field it got wrong.
    """
    if not is_safe_record_id(record_id):
        shown = record_id if isinstance(record_id, str) else type(record_id).__name__
        raise UnsafeRecordId(
            f"{kind} must be a single path segment with no separators, "
            f"no '..' and at most {MAX_RECORD_ID_LEN} characters — got {shown!r}"
        )
    return str(record_id)


def record_path(
    root: Path,
    record_id: object,
    *,
    prefix: str = "",
    suffix: str = ".json",
    kind: str = "id",
) -> Path:
    """Resolve ``root / f"{prefix}{record_id}{suffix}"``, or raise :class:`UnsafeRecordId`.

    The replacement for every ``_dir() / f"{record_id}.json"`` in the codebase.
    ``prefix``/``suffix`` are trusted literals owned by the store (``"_comments_"``,
    ``".json"``, ``".runner.json"``); only ``record_id`` is untrusted, and it is
    checked before it is interpolated so a separator cannot be smuggled through the
    template. Pass ``suffix=""`` for a record stored as a *directory* (the project
    store), which is the case that made #455 an ``rmtree`` rather than an ``unlink``.

    Containment is asserted on the result as well as the id — cheap, and it is the only
    half that still holds once ``root`` itself contains a symlink.
    """
    safe = require_safe_record_id(record_id, kind=kind)
    candidate = root / f"{prefix}{safe}{suffix}"
    # `strict=False`: the record legitimately may not exist yet (every create path
    # resolves its destination before writing it).
    resolved = candidate.resolve(strict=False)
    root_resolved = root.resolve(strict=False)
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise UnsafeRecordId(
            f"{kind} {safe!r} resolves outside its store ({resolved} not under {root_resolved})"
        )
    return candidate


def is_safe_relative_path(rel: object) -> bool:
    """True when ``rel`` is a relative path of one or more segments, each a safe record id
    (:func:`is_safe_record_id`), joined by ``/``: the shape of a path another machine, an
    export or an archive names inside a store — ``tasks/entities.jsonl``, a nested skill's
    ``utils/tiny-url``. No segment can climb out, none is empty, and it is not absolute."""
    return isinstance(rel, str) and bool(rel) and all(map(is_safe_record_id, rel.split("/")))


def is_path_in_store(root: Path, rel: object, *, follow_last: bool = True) -> bool:
    """True when ``root / rel`` names a path inside ``root``: *rel* of the safe shape
    (:func:`is_safe_relative_path`), and the path inside ``root`` once every symlink on the way to
    it is followed. The path itself need not exist.

    The shape is a statement about the name and the containment one about the filesystem: a
    safe name under a folder that is a symlink to somewhere else still leads there, so both are
    asked. ``root`` is taken where it is on disk — a store its owner moved behind a symlink is
    still that store.

    ``follow_last=False`` follows the folders on the way and takes the last segment as it is
    named, for a caller that only ever replaces or removes the entry there — a rename over a
    link replaces the link — and reads a link there as the entry it is."""
    if not isinstance(rel, str) or not is_safe_relative_path(rel):
        return False
    parts = rel.split("/")
    try:
        root_resolved = root.resolve(strict=False)
        if follow_last:
            resolved = root.joinpath(*parts).resolve(strict=False)
        else:
            resolved = root.joinpath(*parts[:-1]).resolve(strict=False) / parts[-1]
    except (OSError, RuntimeError):  # a symlink loop, on a Python that raises for one
        return False
    return resolved != root_resolved and root_resolved in resolved.parents

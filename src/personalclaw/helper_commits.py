"""The commits a helper made under an identity other than the owner's, named in its report.

A commit and its sign-off are given in the owner's name. A helper fixing a README once committed
under an address it made up, signed off with it, and the chat that started it reviewed the commit
and called it signed. The command reading (`command_identity`) asks before a command that gives
git an author or committer of its own runs, but git takes them from places a command never
spells: a settings file a command wrote, a raw commit object, an interpreter's own git call.

So every commit is checked once it is made. As a helper starts in its folder, the identity git
commits as there (`loop.worktree.commit_identity`) and the commit HEAD is at are read, before the
helper runs anything that could change them (:func:`started`). As it ends, every commit made on
the folder's branches since then (`loop.worktree.commits_made_since`) is read for its author, its
committer and its ``Signed-off-by`` lines (:func:`ended`), and each one that names anyone else is
listed in a note the helper's report carries, in PersonalClaw's own words after the helper's
(:func:`note_for`, ``subagent_report``): to the chat that started it, which is told to tell the
owner rather than call those commits hers, and in the owner's own note about the run. With no
identity configured when the helper started, every commit it made is listed: git commits under
none, so each one carries an identity something gave it.

The folder checked is the helper's own (``SubagentInfo.cwd``); a commit it made in another
repository is not read.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: The most commits a note names; the rest are counted.
_SHOWN = 5
#: The most notes kept for reports still to be read.
_KEPT = 500


@dataclass(frozen=True)
class _Start:
    folder: str
    head: str
    identity: tuple[str, str] | None
    at: float


_STARTS: dict[str, _Start] = {}
_NOTES: OrderedDict[str, str] = OrderedDict()


def _read_start(folder: str) -> _Start | None:
    from personalclaw.loop.worktree import commit_identity, head_commit

    head = head_commit(folder)
    if head is None:
        return None
    return _Start(folder, head, commit_identity(folder), time.time() - 1)


async def started(info: Any) -> None:
    """Read who the helper *info*'s folder commits as, and where its HEAD is, as it starts."""
    folder = str(getattr(info, "cwd", "") or "")
    if not folder:
        return
    try:
        start = await asyncio.to_thread(_read_start, folder)
    except Exception:  # noqa: BLE001 - the check is best-effort; the helper runs regardless
        logger.debug("helper commits: could not read %s", folder, exc_info=True)
        return
    if start is not None:
        _STARTS[str(info.id)] = start


def _differs(person: str, owner: tuple[str, str]) -> bool:
    name, _, rest = person.partition("<")
    email = rest.rstrip(">").strip()
    return name.strip() != owner[0] or email.lower() != owner[1].lower()


def _not_hers(commit: Any, owner: tuple[str, str] | None) -> list[str]:
    """What of *commit* names someone other than *owner*, in words; all of it with no owner."""
    said = []
    for role, (name, email) in (("author", commit.author), ("committer", commit.committer)):
        if owner is None or _differs(f"{name} <{email}>", owner):
            said.append(f"{role} {name} <{email}>")
    said += [
        f"signed off by {who}" for who in commit.signed_off if owner is None or _differs(who, owner)
    ]
    return said


def _review(start: _Start) -> str:
    from personalclaw.loop.worktree import commits_made_since

    found = [
        (commit.sha[:9], said)
        for commit in commits_made_since(start.folder, start.head, start.at)
        if (said := _not_hers(commit, start.identity))
    ]
    if not found:
        return ""
    listed = "; ".join(f"`{sha}`: {', '.join(said)}" for sha, said in found[:_SHOWN])
    more = f"; and {len(found) - _SHOWN} more" if len(found) > _SHOWN else ""
    whose = (
        f"other than the identity git commits as there, {start.identity[0]} <{start.identity[1]}>"
        if start.identity
        else "though git had no identity to commit as there"
    )
    return (
        f"PersonalClaw checked the commits this helper made in {start.folder}: {len(found)} "
        f"name an author, a committer or a sign-off {whose} ({listed}{more}). They are not signed "
        "by the user. Tell the user which commits these are before anything is pushed, and do not "
        "call them signed or the user's."
    )


async def ended(info: Any) -> None:
    """Check the commits the helper *info* made since it started (:func:`note_for`)."""
    start = _STARTS.pop(str(info.id), None)
    if start is None:
        return
    try:
        note = await asyncio.to_thread(_review, start)
    except Exception:  # noqa: BLE001 - a check that cannot run says nothing either way
        logger.warning("helper commits: could not read the commits in %s", start.folder)
        return
    if note:
        _NOTES[str(info.id)] = note
        while len(_NOTES) > _KEPT:
            _NOTES.popitem(last=False)


def note_for(agent_id: str) -> str:
    """The note the report of the helper *agent_id* carries about its commits, ``""`` for none."""
    return _NOTES.get(str(agent_id), "")

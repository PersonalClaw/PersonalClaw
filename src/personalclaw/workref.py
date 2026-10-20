"""How a piece of work is named: ``<kind>:<id>``, written here and read back here.

A phase names the work it is about (``work_phase`` frames' ``work``: ``chat:<session>``), and a
trigger's history row names what its fire started (``ActionResult.work_id``: ``subagent:<id>``,
``workflow:<run>``). There are six kinds of work, and a name of any other kind is refused, so a
string that only looks like a work's name never reads as one.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The kinds of work there are.
WORK_KINDS = ("chat", "loop", "workflow", "subagent", "trigger", "webhook")


@dataclass(frozen=True)
class WorkRef:
    """One piece of work: its kind, and its id within that kind."""

    kind: str
    id: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}"


def work_ref(kind: str, ident: str) -> str:
    """The name of the *kind* work *ident*. Raises ``ValueError`` for a kind there is not, or no
    id."""
    return str(_checked(kind, ident))


def parse(name: str) -> WorkRef:
    """The work *name* names. Raises ``ValueError`` for a name of no kind there is, or with no
    id."""
    kind, sep, ident = str(name).partition(":")
    if not sep:
        raise ValueError(f"{name!r} names no kind of work")
    return _checked(kind, ident)


def _checked(kind: str, ident: str) -> WorkRef:
    if kind not in WORK_KINDS:
        raise ValueError(f"{kind!r} is not a kind of work: one of {', '.join(WORK_KINDS)}")
    if not ident:
        raise ValueError(f"a {kind} is named with its id")
    return WorkRef(kind, ident)

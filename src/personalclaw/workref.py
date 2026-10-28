"""How a piece of work is named: ``<kind>:<id>``, written here and read back here.

A phase names the work it is about (``work_phase`` frames' ``work``: ``chat:<session>``), and a
trigger's history row names what its fire started (``ActionResult.work_id``: ``subagent:<id>``,
``workflow:<run>``). The kinds of work are shared contracts §2.2's closed set, and a name of any
other kind is refused, so a string that only looks like a work's name never reads as one. Every
producer builds a name with :func:`work_ref` and every reader reads one with :func:`parse`
(``tests/rails/test_work_references_have_one_parser.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

#: The kinds of work there are. A ``job`` is a background command, named by its ``job_`` id.
WORK_KINDS = ("chat", "loop", "workflow", "subagent", "trigger", "webhook", "job")


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


def read(name: str) -> tuple[str, str]:
    """The kind and id *name* names, or ``("", "")`` when it names no work: for a reader that has a
    fallback for a name that is missing or of no kind (a row with no work, a record of another
    shape), so the fallback is taken instead of a refusal."""
    try:
        ref = parse(name)
    except ValueError:
        return "", ""
    return ref.kind, ref.id


def _checked(kind: str, ident: str) -> WorkRef:
    if kind not in WORK_KINDS:
        raise ValueError(f"{kind!r} is not a kind of work: one of {', '.join(WORK_KINDS)}")
    if not ident:
        raise ValueError(f"a {kind} is named with its id")
    return WorkRef(kind, ident)

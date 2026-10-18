"""A piece of work is named one way, ``<kind>:<id>``, and read by one parser.

The phase a piece of work is in names the work it is about (``work_phase``'s ``work``), and so does
a trigger's history row naming what its fire started (``ActionResult.work_id``). Every surface that
reads one such name reads it through ``personalclaw.workref``: the six kinds of work there are, and
a refusal for anything else, so a name no kind owns never passes for one.
``tests/rails/test_every_work_id_has_a_known_writer.py`` holds the gateway's own writers of a name
to the ones this parser reads back.
"""

from __future__ import annotations

import pytest
from table_rows import row

from personalclaw import workref

KINDS = ("chat", "loop", "workflow", "subagent", "trigger", "webhook")

#: Names of no kind of work, each with why: no kind, an unknown kind, no id.
REFUSED = [
    ("cron:abc", "a kind there is not"),
    ("dashboard:chat-1", "a session key, not a work's name"),
    ("Chat:a1", "kinds are lower case"),
    ("chat", "no separator"),
    ("chat:", "no id"),
    (":a1", "no kind"),
    ("", "nothing"),
    (" chat:a1", "a kind with a space in it"),
]


def test_each_kind_of_work_is_named_and_read_back(subtests) -> None:
    assert workref.WORK_KINDS == KINDS
    for kind in KINDS:
        with row(subtests, kind=kind):
            name = workref.work_ref(kind, "a1:b2")
            assert name == f"{kind}:a1:b2"
            assert workref.parse(name) == workref.WorkRef(kind, "a1:b2")
            assert str(workref.parse(name)) == name


def test_any_other_name_is_refused(subtests) -> None:
    assert len(REFUSED) == 8, "a refused name was dropped from the table"
    for name, why in REFUSED:
        with row(subtests, name=name, why=why), pytest.raises(ValueError):
            workref.parse(name)


def test_no_name_is_written_for_a_kind_or_id_that_is_not_one(subtests) -> None:
    rows = [("cron", "a1"), ("chat", ""), ("", "a1")]
    for kind, ident in rows:
        with row(subtests, kind=kind, ident=ident), pytest.raises(ValueError):
            workref.work_ref(kind, ident)

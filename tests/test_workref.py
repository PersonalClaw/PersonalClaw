"""A piece of work is named one way, ``<kind>:<id>``, and read by one parser.

The phase a piece of work is in names the work it is about (``work_phase``'s ``work``), and so does
a trigger's history row naming what its fire started (``ActionResult.work_id``). Every surface that
reads one such name reads it through ``personalclaw.workref``: the seven kinds of work shared
contracts §2.2 closes, and a refusal for anything else, so a name no kind owns never passes for one.
``tests/rails/test_every_work_id_has_a_known_writer.py`` holds the gateway's own writers of a name
to the ones this parser reads back, and ``tests/rails/test_work_references_have_one_parser.py``
holds every producer to ``work_ref`` and every reader to ``parse``.
"""

from __future__ import annotations

import pytest
from table_rows import row

from personalclaw import subagent, workref
from personalclaw.workflows import models

KINDS = ("chat", "loop", "workflow", "subagent", "trigger", "webhook", "job")

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


def test_a_name_keeps_the_colons_in_its_id() -> None:
    assert workref.parse("chat:dashboard:chat-1") == workref.WorkRef("chat", "dashboard:chat-1")
    assert workref.parse("job:job_01j9abcdefghjkmnpqrstvwxyz") == workref.WorkRef(
        "job", "job_01j9abcdefghjkmnpqrstvwxyz"
    )


def test_schedule_runs_name_their_work_through_ref(subtests) -> None:
    """A trigger's history row names what its fire started with ``agent_work_id`` and
    ``run_work_id``. Both build the name through ``work_ref`` now (the parser rail holds that), and
    write the same string as before, which an unsettled row recorded before the change still
    matches."""
    for write, kind, ident, before in (
        (subagent.agent_work_id, "subagent", "a1b2c3", "subagent:a1b2c3"),
        (models.run_work_id, "workflow", "r-9f8e", "workflow:r-9f8e"),
    ):
        with row(subtests, writer=write.__name__):
            assert write(ident) == before
            assert workref.parse(write(ident)) == workref.WorkRef(kind, ident)


def test_a_reader_with_a_fallback_reads_a_non_name_as_none(subtests) -> None:
    """``read`` is ``parse`` for a reader that falls back where a name is missing or foreign."""
    assert workref.read("chat:dashboard:chat-1") == ("chat", "dashboard:chat-1")
    for name, why in REFUSED:
        with row(subtests, name=name, why=why):
            assert workref.read(name) == ("", "")

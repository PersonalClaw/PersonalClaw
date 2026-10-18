"""No bounded log in core trims by position: every trim keeps the newest rows by their own time.

A bounded log let its oldest rows go by cutting the tail off what it read — ``rows[-cap:]``,
``del rows[:-cap]``, ``while len(rows) > cap: rows.pop(0)`` — and wrote what was left back. That is
right only while the rows are in the order they happened, and a merge restore or a sync adds older
rows after newer ones: the notification log kept an archive's old notes and deleted thirteen hours
of the newest. The rule now lives in one place, ``personalclaw.bounded_log`` (:func:`newest`,
:func:`trim_jsonl`, :func:`prune_table`), and this rail finds a trim that does not go through it.

**What it flags.** A function that cuts the tail off a list by position AND writes (a call whose
name says it writes, saves or persists), unless it puts the rows in time order through
``bounded_log`` first. A cut with no write is a view of something, not a trim of a log; a function
that orders the rows through the helper and then cuts (the run history's per-class quota) is the
rule applied. The same for SQL: a ``DELETE`` that keeps the highest row ids.

**What is exempt, and why.** :data:`NOT_A_LOG` names each function the shape matches that trims no
log of rows — a text cut to its last characters, a page sent to a client — each with its reason.
An entry that no longer matches fails, so the list cannot outlive what it excuses.
"""

from __future__ import annotations

import textwrap

from bounded_log_rail_trims_by_time_shared import _PRUNE_BY_ID, position_trims

# ── the rail ─────────────────────────────────────────────────────────────────────────────────


# ── the rail's controls ──────────────────────────────────────────────────────────────────────


def test_the_rail_flags_each_way_a_log_was_cut_by_position() -> None:
    """Positive controls: the shapes the tree held before the rule, each in a few lines."""
    source = textwrap.dedent("""
        def append_note(self, note):
            self.log.append(note)
            if len(self.log) > CAP * 2:
                self.log = self.log[-CAP:]
                _rewrite_notifications(self.log)

        def maybe_trim(p):
            lines = p.read_text().splitlines()
            if len(lines) > 2 * CAP:
                atomic_write(p, "\\n".join(lines[-CAP:]) + "\\n")

        def record(book):
            book["log"].append(1)
            del book["log"][:-CAP]
            ctl._save_run()

        def take(profile):
            profile.history.append(1)
            while len(profile.history) > CAP:
                profile.history.pop(0)
            _write(profile)

        def queue(path, q):
            del q[: len(q) - CAP]
            path.write_text(str(q))
        """)
    assert set(position_trims(source)) == {"append_note", "maybe_trim", "record", "take", "queue"}


def test_the_rail_passes_a_trim_by_time_and_a_cut_that_writes_nothing() -> None:
    source = textwrap.dedent("""
        def by_time(self, note):
            self.log = bounded_log.newest(self.log + [note], CAP, at="ts")
            _rewrite_notifications(self.log)

        def ordered_then_cut(rows):
            rows = bounded_log.in_time_order(rows, at="started_at")
            _write_jsonl(PATH, rows[-CAP:])

        def a_view(rows):
            return rows[-10:]
        """)
    assert position_trims(source) == {}


def test_the_sql_rail_flags_a_prune_to_the_highest_ids() -> None:
    assert _PRUNE_BY_ID.search(
        "DELETE FROM allocation_samples WHERE id <= (SELECT MAX(id) - ? FROM allocation_samples);"
    )
    assert _PRUNE_BY_ID.search(
        "DELETE FROM memory_events WHERE id IN "
        "(SELECT id FROM memory_events ORDER BY id ASC LIMIT ?)"
    )
    assert not _PRUNE_BY_ID.search(
        'DELETE FROM "t" WHERE rowid NOT IN (SELECT rowid FROM "t" ORDER BY "ts" DESC, rowid DESC '
        "LIMIT ?)"
    )

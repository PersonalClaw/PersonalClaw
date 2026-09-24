"""Tests for the project living context — overview, ledgers, injected block (S46).

These write to a real (monkeypatched) config dir rather than mocking the store, because the two
properties under test are about FILES: the overview is revised in place while the ledgers are
append-only, and a mocked store would let both pass while neither held on disk.

The load-bearing distinction: overview is current state, the decisions ledger is history. Collapsing
them means either losing the history or making the current state something a reader
reconstructs from
a log — and a reader who has to reconstruct it will not.
"""

import pytest

from personalclaw import project_context as pctx


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A real project in an isolated home. Never touches the user's actual config dir."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: home)
    from personalclaw.tasks.hierarchy import HierarchyStore

    store = HierarchyStore()
    monkeypatch.setattr(store, "_projects_dir", lambda: _mk(home / "projects"))
    created = store.create_project("Ingest rework")
    monkeypatch.setattr(
        "personalclaw.tasks.hierarchy.HierarchyStore._projects_dir",
        lambda self: _mk(home / "projects"),
    )
    return created.id


def _mk(path):
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── the overview is revised IN PLACE ──


def test_an_absent_overview_reads_as_empty():
    """Not an error and not a placeholder: a project with no overview yet is the normal state, and a
    placeholder would be injected into prompts as though it said something."""
    assert pctx.read_overview("p-nonexistent") == ""


def test_an_overview_round_trips(project):
    assert pctx.write_overview(project, "auth is done; ingest is next") is True
    assert pctx.read_overview(project) == "auth is done; ingest is next"


def test_writing_an_overview_REPLACES_it(project):
    """In place is the whole point — this is current state. An append would turn the overview into
    the history it is defined against."""
    pctx.write_overview(project, "first")
    pctx.write_overview(project, "second")
    assert pctx.read_overview(project) == "second"


def test_the_overview_is_capped(project):
    """An unbounded overview eventually crowds out the rest of the prompt, and the thing it would
    crowd out is the user's actual message."""
    pctx.write_overview(project, "x" * (pctx.MAX_OVERVIEW_CHARS + 500))
    assert len(pctx.read_overview(project)) <= pctx.MAX_OVERVIEW_CHARS


def test_writing_to_an_unknown_project_FAILS_rather_than_creating_one(tmp_path, monkeypatch):
    """`resolve_project_id` auto-creates projects. A context write that also did would mean a typo'd
    id silently invents a project and puts the user's overview in it."""
    assert pctx.write_overview("p-nope", "text") is False


def test_reading_an_unknown_project_does_not_create_a_directory(project, tmp_path, monkeypatch):
    """A READ path that materialized a project would mean opening a project page invents one."""
    pctx.read_overview("p-doesnotexist")
    assert not (tmp_path / "home" / "projects" / "p-doesnotexist").exists()


# ── the ledgers are APPEND-ONLY ──


def test_a_ledger_entry_appends(project):
    assert pctx.append_ledger(project, "decisions", "chose sqlite over postgres") is True
    assert pctx.read_ledger(project, "decisions") == ["chose sqlite over postgres"]


def test_appending_twice_keeps_BOTH(project):
    """There is no update and no delete. A ledger whose entries could be edited would stop being
    evidence of what was decided when — which is the one question it exists to answer."""
    pctx.append_ledger(project, "decisions", "first")
    pctx.append_ledger(project, "decisions", "second")
    assert pctx.read_ledger(project, "decisions") == ["first", "second"]


def test_the_three_ledgers_are_SEPARATE_files(project):
    """One file would make every append a read-modify-write of all three, and a torn write would
    lose two ledgers to fix one."""
    pctx.append_ledger(project, "decisions", "d")
    pctx.append_ledger(project, "fog", "f")
    pctx.append_ledger(project, "out_of_scope", "o", reason="too big")
    assert pctx.read_ledger(project, "decisions") == ["d"]
    assert pctx.read_ledger(project, "fog") == ["f"]
    assert len(pctx.read_ledger(project, "out_of_scope")) == 1


def test_an_out_of_scope_entry_renders_its_reason(project):
    pctx.append_ledger(project, "out_of_scope", "mobile app", reason="no device to test on")
    assert "no device to test on" in pctx.read_ledger(project, "out_of_scope")[0]


def test_a_decisions_entry_renders_its_run_link(project):
    pctx.append_ledger(project, "decisions", "chose sqlite", link="/runs/r-42")
    assert "/runs/r-42" in pctx.read_ledger(project, "decisions")[0]


def test_an_unknown_ledger_kind_is_refused(project):
    """A typo'd kind silently creating a fourth ledger would split the decisions log in two, and
    neither half would be complete."""
    assert pctx.append_ledger(project, "milestones", "q1") is False


def test_an_empty_entry_is_not_appended(project):
    """A blank bullet in the decisions index is a line the reader has to check to learn it says
    nothing."""
    assert pctx.append_ledger(project, "decisions", "   ") is False
    assert pctx.read_ledger(project, "decisions") == []


def test_a_ledger_line_is_capped(project):
    """The decisions ledger is "an index, not a store" by contract. Without a cap a run could paste
    its whole output into the index."""
    pctx.append_ledger(project, "decisions", "y" * (pctx.MAX_LEDGER_LINE + 200))
    assert len(pctx.read_ledger(project, "decisions")[0]) <= pctx.MAX_LEDGER_LINE


def test_the_ledger_file_carries_its_own_purpose_as_a_header(project):
    """Someone opening `not-yet-specified.md` on disk needs to know what belongs in it, or it
    becomes
    the place things go to be forgotten."""
    pctx.append_ledger(project, "fog", "how do retries interact with the cache?")
    from personalclaw.tasks.hierarchy import HierarchyStore

    text = (HierarchyStore().context_dir(project) / "not-yet-specified.md").read_text()
    assert "promote" in text


def test_reading_an_absent_ledger_is_empty(project):
    assert pctx.read_ledger(project, "decisions") == []


def test_an_unknown_project_ledger_read_is_empty():
    assert pctx.read_ledger("p-nope", "decisions") == []


# ── the injected block ──


def test_the_block_carries_brief_and_overview_together(project):
    from personalclaw.tasks.hierarchy import HierarchyStore

    HierarchyStore().update_project(project, brief="rework the ingest path")
    pctx.write_overview(project, "batching is done")
    block = pctx.context_block(project)
    assert "rework the ingest path" in block
    assert "batching is done" in block


def test_the_block_is_empty_for_a_project_with_nothing_set(project):
    assert pctx.context_block(project) == ""


def test_the_block_is_empty_for_an_unknown_project():
    assert pctx.context_block("p-nope") == ""


def test_the_block_never_raises(monkeypatch):
    """It feeds `context.build_message`, where the never-break-a-turn contract applies: a corrupt
    overview must cost the block, never the user's message."""

    def explode(_):
        raise RuntimeError("store is broken")

    monkeypatch.setattr("personalclaw.tasks.hierarchy.HierarchyStore.get_project", explode)
    assert pctx.context_block("p-1") == ""


# ── the handoff snapshot ──


def test_the_snapshot_labels_where_each_field_CAME_FROM(project):
    """A snapshot that presented a derived guess as a recorded fact would be the same
    guess-as-requirement failure the planner's Step-0 schema guards against."""
    snapshot = pctx.handoff_snapshot(project)
    assert "overview.md" in snapshot["focus_source"]
    assert "append-only" in snapshot["decisions_source"]


def test_the_snapshot_does_NOT_invent_next_actions(project):
    """Ordered next actions need live run/task state. Fabricating them from the ledgers would put a
    plausible list in front of someone about to act on it."""
    snapshot = pctx.handoff_snapshot(project)
    assert snapshot["next_actions"] == []
    assert "caller" in snapshot["next_actions_note"]


def test_the_snapshot_shows_the_most_recent_decisions(project):
    for index in range(15):
        pctx.append_ledger(project, "decisions", f"decision {index}")
    snapshot = pctx.handoff_snapshot(project)
    assert len(snapshot["decisions"]) == 10
    assert snapshot["decisions"][-1] == "decision 14"


def test_the_snapshot_surfaces_open_questions(project):
    pctx.append_ledger(project, "fog", "how should retries interact with the cache?")
    assert pctx.handoff_snapshot(project)["open_questions"] == [
        "how should retries interact with the cache?"
    ]


# ── the preamble does not recommend redundant reads ──


def test_only_files_that_were_ACTUALLY_inlined_are_excluded(project):
    """CONTENT-based, not name-based. A blanket exclusion on the reserved filenames looked
    equivalent and was not: a hand-authored `decisions.md` that is not in ledger line format
    inlines nothing, and excluding it by name would hide a file the agent has never seen.
    Hiding an unread file is the worse failure of the two — a redundant pointer wastes a tool
    call, a hidden file loses the context entirely."""
    from personalclaw.tasks.hierarchy import HierarchyStore

    context = HierarchyStore().context_dir(project)
    # The reserved NAME with no ledger lines in it: nothing is inlined, so it stays listed.
    (context / "decisions.md").write_text("# Decisions\nUse minimax.\n")
    assert pctx.inlined_context_files(project) == frozenset()

    # Once real ledger lines exist the content IS inlined, and the pointer is redundant.
    pctx.append_ledger(project, "decisions", "chose sqlite")
    pctx.write_overview(project, "batching is done")
    assert pctx.inlined_context_files(project) == frozenset({"decisions.md", "overview.md"})


def test_the_chat_preamble_does_not_list_files_it_already_INLINED(project):
    """Measured on a live project: the overview and all three ledgers appeared both as inlined text
    and under "read any for continuity", inviting four tool calls to re-read what the agent had
    already been given. A listing that recommends redundant work is one an agent learns to ignore
    wholesale, taking the genuinely unread files with it."""
    from personalclaw.dashboard.chat_utils import _project_context_preamble
    from personalclaw.tasks.hierarchy import HierarchyStore

    pctx.write_overview(project, "batching is done")
    pctx.append_ledger(project, "decisions", "chose sqlite")
    # A file whose content is NOT inlined must still be listed — the exclusion is targeted, not a
    # blanket suppression of the listing.
    (HierarchyStore().context_dir(project) / "scratch-notes.md").write_text("hand-written")

    preamble = _project_context_preamble(project)
    listed = [line.strip()[2:].split(" ")[0] for line in preamble.splitlines() if "•" in line]
    assert "scratch-notes.md" in listed
    assert "overview.md" not in listed
    assert "decisions.md" not in listed
    # The CONTENT is still there — the fix removed a redundant pointer, not the context.
    assert "batching is done" in preamble
    assert "chose sqlite" in preamble

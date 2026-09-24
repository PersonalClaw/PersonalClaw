"""Reader/writer reservations + the wave partition for a turn's tool calls (HC-6).

The tests are grouped by the property they defend, because each of the four is a different
kind of defect:

* the reader/writer rule itself — the thing the feature IS;
* **the pattern trap** — a PATTERN is never normalized. This is the class the atom names and
  the one a plausible implementation gets wrong, so it gets its own class and an explicit
  statement of what a normalizing implementation would do instead;
* fail-safe classification — an unclassified call touches EVERYTHING and therefore runs
  alone, which is what makes an unknown tool degrade to the old behaviour rather than race;
* the ordering guarantee — the partition may never move a conflicting pair past each other.
"""

from __future__ import annotations

import os

import pytest

from personalclaw.agents.native.dispatch_plan import (
    EVERYTHING,
    KIND_EVERYTHING,
    KIND_PATTERN,
    MODE_CONCURRENT,
    MODE_SERIAL,
    READ,
    WRITE,
    Reservation,
    conflicts,
    plan,
    reservations_for,
)

CWD = "/ws"


def _res(tool: str, args: dict):
    return reservations_for(tool, args, cwd=CWD)


def _read(path: str):
    return _res("read_file", {"path": path})


def _write(path: str):
    return _res("write_file", {"path": path})


# ── the reader/writer rule ──


class TestReaderWriterRule:
    def test_two_reads_of_different_files_overlap(self):
        assert not conflicts(_read("a.py"), _read("b.py"))

    def test_two_reads_of_the_same_file_overlap(self):
        """Spelled out because it is the case a naive "same key ⇒ serialize" gets wrong."""
        assert not conflicts(_read("a.py"), _read("a.py"))

    def test_a_write_serializes_against_a_reader_of_the_same_path(self):
        assert conflicts(_write("a.py"), _read("a.py"))
        assert conflicts(_read("a.py"), _write("a.py"))

    def test_a_write_serializes_against_another_writer_of_the_same_path(self):
        assert conflicts(_write("a.py"), _write("a.py"))

    def test_a_write_does_not_serialize_against_a_different_path(self):
        assert not conflicts(_write("a.py"), _read("b.py"))

    def test_an_edit_and_a_write_of_one_path_are_the_same_reservation(self):
        assert conflicts(_res("edit_file", {"path": "a.py"}), _read("a.py"))

    def test_a_relative_and_an_absolute_spelling_are_the_same_key(self):
        """The ITEM side IS normalized — that half of the rule has to hold too, or the same
        file under two spellings would look like two resources and race."""
        assert conflicts(_write("a.py"), _read(os.path.join(CWD, "a.py")))

    def test_a_dotdot_in_a_queried_path_collapses(self):
        assert conflicts(_write("pkg/../a.py"), _read("a.py"))

    def test_a_walk_covers_paths_under_it(self):
        assert conflicts(_res("list_dir", {"path": "src"}), _write("src/deep/a.py"))
        assert conflicts(_res("repo_map", {"path": "src"}), _write("src/a.py"))
        assert not conflicts(_res("list_dir", {"path": "src"}), _write("other/a.py"))

    def test_an_unfiltered_grep_reserves_the_whole_workspace(self):
        """A grep with no `glob` walks everything, so a write anywhere collides with it."""
        assert conflicts(_res("grep", {"query": "x"}), _write("deep/nested/a.py"))

    def test_two_unfiltered_greps_still_overlap(self):
        assert not conflicts(_res("grep", {"query": "x"}), _res("grep", {"query": "y"}))

    def test_namespaces_are_not_paths(self):
        k_read = _res("knowledge_search", {"query": "x"})
        k_write = _res("knowledge_create", {"title": "x"})
        t_read = _res("task_list", {})
        assert not conflicts(k_read, k_read)
        assert conflicts(k_write, k_read)
        assert not conflicts(k_write, t_read)
        # …and a namespace never collides with a file, in either direction.
        assert not conflicts(k_write, _read("a.py"))
        assert not conflicts(k_write, _res("grep", {"query": "x"}))

    def test_a_pure_meta_tool_touches_nothing(self):
        assert _res("tool_search", {"query": "x"}) == ()
        assert not conflicts(_res("tool_search", {"query": "x"}), _write("a.py"))


# ── the pattern trap (the class the atom names) ──


class TestPatternIsNeverNormalized:
    """A glob is reserved AS A GLOB.

    Every test here fails if ``reservations_for`` normalizes the pattern into a path key,
    because a path key is compared by EQUALITY and a glob compared by equality matches only
    a file literally named ``**/*.py``.
    """

    def test_the_reservation_key_is_the_pattern_verbatim(self):
        (r,) = _res("glob", {"pattern": "**/*.py"})
        assert r == Reservation(READ, KIND_PATTERN, "**/*.py")
        # The two things normalization would have done, named so the rail cannot pass by
        # accident: no absolutizing, and no normpath collapse.
        assert r.key == "**/*.py" != os.path.abspath("**/*.py")
        assert not r.key.startswith("/")

    def test_a_glob_read_serializes_a_write_it_matches(self):
        """THE TRAP. Normalize the pattern and this pair compares ``/ws/**/*.py`` against
        ``/ws/pkg/mod.py`` by equality, finds them different, and lets the write overlap a
        read it had to serialize against."""
        assert conflicts(_res("glob", {"pattern": "**/*.py"}), _write("pkg/mod.py"))

    def test_a_glob_read_does_not_serialize_a_write_it_cannot_match(self):
        """The other half — without this the rule could be satisfied by "patterns conflict
        with everything", which is safe but would concede the whole atom."""
        assert not conflicts(_res("glob", {"pattern": "**/*.py"}), _write("pkg/notes.md"))

    def test_a_filtered_grep_is_a_pattern_too(self):
        assert conflicts(_res("grep", {"query": "x", "glob": "src/*.py"}), _write("src/a.py"))
        assert not conflicts(_res("grep", {"query": "x", "glob": "src/*.py"}), _write("doc/a.md"))

    def test_a_pattern_matches_at_depth_the_way_the_shared_matcher_says(self):
        """Anchoring is the shared matcher's business (``registries.path_glob``), not a
        second opinion held here: an unanchored pattern matches at any depth."""
        assert conflicts(_res("glob", {"pattern": "mod.py"}), _write("deep/pkg/mod.py"))

    def test_a_dotdot_pattern_degrades_to_everything(self):
        """PHF's collapse case: ``normpath`` turns ``a/**/../b`` into ``a/b``, dropping the
        ``**``. Rather than normalize it (which changes what the reservation covers) or match
        it verbatim (which covers nothing, because a normalized item never contains ``..``),
        the pattern is refused into EVERYTHING and the call serializes."""
        (r,) = _res("glob", {"pattern": "a/**/../b/*.py"})
        assert r == EVERYTHING
        assert r.kind == KIND_EVERYTHING
        # …so it collides with a write that a collapsed `a/b/*.py` would have matched AND
        # with one it would have missed. Either direction of the collapse is refused.
        assert conflicts(_res("glob", {"pattern": "a/**/../b/*.py"}), _write("a/b/x.py"))
        assert conflicts(_res("glob", {"pattern": "a/**/../b/*.py"}), _write("z/other.md"))

    def test_an_empty_pattern_degrades_to_everything(self):
        assert _res("glob", {"pattern": ""}) == (EVERYTHING,)

    def test_two_patterns_are_assumed_to_intersect(self):
        """Glob-language intersection is not something this module gets to guess at, so a
        WRITE through a pattern conflicts with any other pattern. (Reads still overlap.)"""
        a = (Reservation(WRITE, KIND_PATTERN, "src/*.py"),)
        b = (Reservation(READ, KIND_PATTERN, "doc/*.md"),)
        assert conflicts(a, b)

    def test_a_pattern_is_assumed_to_reach_into_any_tree(self):
        a = (Reservation(WRITE, KIND_PATTERN, "src/*.py"),)
        assert conflicts(a, _res("list_dir", {"path": "totally/elsewhere"}))


# ── fail-safe classification ──


class TestUnclassifiedIsEverything:
    @pytest.mark.parametrize("tool", ["bash", "reset_tools", "some_app_tool", "", "web_fetch"])
    def test_an_unknown_tool_touches_everything(self, tool):
        assert _res(tool, {"anything": 1}) == (EVERYTHING,)

    def test_an_unknown_tool_conflicts_with_every_other_call(self):
        unknown = _res("bash", {"command": "ls"})
        assert conflicts(unknown, _read("a.py"))
        assert conflicts(unknown, _res("knowledge_search", {"query": "x"}))
        assert conflicts(unknown, unknown)

    def test_a_malformed_argument_dict_degrades_rather_than_raising(self):
        bad: dict = None  # type: ignore[assignment]
        assert reservations_for("read_file", bad, cwd=CWD) == (EVERYTHING,)

    def test_a_missing_required_path_degrades_rather_than_reserving_the_root(self):
        """Not ``/ws`` as if the workspace directory were the file: a reservation keyed on
        the root would collide with nothing under it and let every write race the read."""
        assert _read("") == (EVERYTHING,)
        assert _write("   ") == (EVERYTHING,)
        assert conflicts(_read(""), _write("anywhere/at/all.py"))

    def test_an_unknown_tool_runs_alone(self):
        sets = [_read("a.py"), _res("bash", {"command": "ls"}), _read("b.py")]
        assert plan(sets).waves == ((0,), (1,), (2,))


# ── the ordering guarantee ──


class TestPartitionOrdering:
    def test_disjoint_reads_share_one_wave(self):
        sets = [_read("a.py"), _read("b.py"), _read("c.py")]
        p = plan(sets)
        assert p.waves == ((0, 1, 2),)
        assert p.mode == MODE_CONCURRENT
        assert p.widest == 3
        assert p.call_count == 3

    def test_a_write_splits_the_readers_of_its_path(self):
        sets = [_read("a.py"), _read("b.py"), _write("a.py"), _read("a.py")]
        assert plan(sets).waves == ((0, 1), (2,), (3,))

    def test_every_conflicting_pair_keeps_its_relative_order(self):
        sets = [
            _read("a.py"),
            _res("grep", {"query": "x"}),
            _write("a.py"),
            _read("b.py"),
            _write("b.py"),
            _res("knowledge_search", {"query": "x"}),
        ]
        p = plan(sets)
        wave_of = {i: w for w, wave in enumerate(p.waves) for i in wave}
        assert sorted(wave_of) == list(range(len(sets)))
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                if conflicts(sets[i], sets[j]):
                    assert wave_of[i] < wave_of[j], (i, j, p.waves)

    def test_max_width_bounds_a_wave(self):
        sets = [_read(f"{i}.py") for i in range(10)]
        p = plan(sets, max_width=3)
        assert [len(w) for w in p.waves] == [3, 3, 3, 1]
        assert p.widest == 3

    def test_max_width_one_is_the_pre_hc6_behaviour(self):
        """The baseline arm: every call in its own wave, and the mode reports it."""
        sets = [_read(f"{i}.py") for i in range(4)]
        p = plan(sets, max_width=1)
        assert p.waves == ((0,), (1,), (2,), (3,))
        assert p.mode == MODE_SERIAL
        assert p.widest == 1

    def test_a_nonsense_width_falls_back_to_serial_rather_than_crashing(self):
        assert plan([_read("a.py"), _read("b.py")], max_width=0).waves == ((0,), (1,))

    def test_an_empty_turn_plans_nothing(self):
        p = plan([])
        assert p.waves == ()
        assert p.call_count == 0
        assert p.mode == MODE_SERIAL

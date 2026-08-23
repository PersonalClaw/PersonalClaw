"""MODEL-ROUTING-TELEMETRY §4.2 / MRT-5 — the feedback third of the routing score.

``stats._score`` already owns ``0.60·success_rate + 0.40·feedback`` AND the renormalisation onto
pure ``success_rate`` when ``feedback_n`` is 0. ``routing.feedback`` owns the missing input: the
``(feedback, feedback_n)`` pair per ``(use_case, query_class, ref)``, extracted from the WF2 Run
Ledger's ``judge_verdict`` events.

These lock the parts that would otherwise steer real routing on a number nobody chose: what
increments ``feedback_n`` (an inflated count crosses the downstream ``n >= 5`` floor early), what a
corrupt or half-written line does (a reader that dies on a partial tail loses every signal in the
file), what an unattributable or unrecognised record does, and that the many-cell index and the
per-cell reader can never report different numbers.

Every test drives an isolated ``tmp_path`` home. The real ``~/.personalclaw`` is never read or
written — asserted directly by ``TestWritesNothing``.
"""

from __future__ import annotations

import json
from pathlib import Path

from personalclaw.routing import feedback
from personalclaw.routing.stats import _score, ref_of

_UC = "reasoning"
_QC = "summarize"
_PROVIDER = "ollama-models"
_MODEL = "qwen3:8b"
_REF = ref_of(_PROVIDER, _MODEL)
_CELL = (_UC, _QC, _REF)


def _verdict(
    *,
    event_id: str,
    verdict: str = "PASS",
    use_case: str | None = _UC,
    query_class: str | None = _QC,
    provider: str | None = _PROVIDER,
    model: str | None = _MODEL,
    **extra: object,
) -> dict:
    """One ``judge_verdict`` ledger record, shaped as the writer stamps it.

    The three routing coordinates are keyword-optional so a test can build the UNATTRIBUTABLE
    shapes (a model with no use case, a use case with no model) that the reader must drop.
    """
    rec: dict = {"kind": "judge_verdict", "event_id": event_id, "verdict": verdict}
    if use_case is not None:
        rec["use_case"] = use_case
    if query_class is not None:
        rec["query_class"] = query_class
    if provider is not None:
        rec["provider"] = provider
    if model is not None:
        rec["model"] = model
    rec.update(extra)
    return rec


def _write_ledger(home: Path, run_id: str, records: list[dict], *, tail: str = "") -> Path:
    """Write ``<home>/workflows/runs/<run_id>/events.jsonl``.

    ``tail`` is appended verbatim with NO trailing newline, which is how a process killed
    mid-append leaves the file.
    """
    run_dir = home / "workflows" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "events.jsonl"
    body = "".join(json.dumps(r) + "\n" for r in records) + tail
    path.write_text(body, encoding="utf-8")
    return path


def _all_paths(root: Path) -> set[Path]:
    return set(root.rglob("*"))


class TestNoSignal:
    def test_no_ledger_at_all_reads_as_no_signal(self, tmp_path):
        assert feedback.feedback_for(_UC, _QC, _REF, home=tmp_path) == (0.0, 0)
        assert feedback.feedback_index(home=tmp_path) == {}

    def test_empty_runs_root_reads_as_no_signal(self, tmp_path):
        (tmp_path / "workflows" / "runs").mkdir(parents=True)
        assert feedback.feedback_for(_UC, _QC, _REF, home=tmp_path) == (0.0, 0)

    def test_no_feedback_falls_back_to_success_rate_through_score(self, tmp_path):
        """The renormalisation claim, asserted where it LIVES — ``stats._score``.

        MRT-5's clause is "renormalizing onto success_rate + ``feedback_n:0`` when absent". This
        module's job is to produce the ``(0.0, 0)`` that makes ``_score`` do exactly that, so the
        assertion goes through ``_score`` rather than re-checking arithmetic here: a local copy of
        the fallback would be the second renormalisation the fold has one answer for.
        """
        fb, n = feedback.feedback_for(_UC, _QC, _REF, home=tmp_path)
        # Asserted through `_score` FIRST, deliberately: a mutation that made this module hand back
        # a fabricated observation instead of `feedback_n: 0` must fail HERE, on the collapse, not
        # on a tuple comparison that never reached the arithmetic under test.
        assert _score(0.8, fb, n) == 0.8  # pure success_rate, NOT 0.60 * 0.8
        # Not vacuous: with a real observation the same call weights the feedback instead.
        assert _score(0.8, 1.0, 3) == round(0.6 * 0.8 + 0.4 * 1.0, 4) != 0.8
        assert (fb, n) == (0.0, 0)


class TestVerdictsFold:
    def test_hand_computed_cell(self, tmp_path):
        """Three PASS + one REJECT over one cell → (1+1+1+0)/4 = 0.75 over n=4."""
        _write_ledger(
            tmp_path,
            "aaaa1111",
            [
                _verdict(event_id="aaaa1111-evt-1", verdict="PASS"),
                _verdict(event_id="aaaa1111-evt-2", verdict="PASS"),
                _verdict(event_id="aaaa1111-evt-3", verdict="REJECT"),
                _verdict(event_id="aaaa1111-evt-4", verdict="PASS"),
            ],
        )
        assert feedback.feedback_for(_UC, _QC, _REF, home=tmp_path) == (0.75, 4)

    def test_a_ref_spelled_directly_is_honoured(self, tmp_path):
        """``ref`` on the event wins over provider+model, and ``ref_of``'s spelling is the one
        used — a colon-bearing model id has exactly one correct join."""
        _write_ledger(
            tmp_path,
            "aaaa2222",
            [
                _verdict(
                    event_id="aaaa2222-evt-1",
                    provider=None,
                    model=None,
                    ref=ref_of("ollama-models", "gpt-oss:20b"),
                )
            ],
        )
        assert feedback.feedback_for(_UC, _QC, "ollama-models:gpt-oss:20b", home=tmp_path) == (
            1.0,
            1,
        )

    def test_non_judge_kinds_are_ignored(self, tmp_path):
        """A ``step_completed`` carries provider+model but is not an assessment of anything."""
        _write_ledger(
            tmp_path,
            "aaaa3333",
            [
                {
                    "kind": "step_completed",
                    "event_id": "aaaa3333-evt-1",
                    "use_case": _UC,
                    "query_class": _QC,
                    "provider": _PROVIDER,
                    "model": _MODEL,
                    "verdict": "PASS",
                },
                _verdict(event_id="aaaa3333-evt-2", verdict="REJECT"),
            ],
        )
        assert feedback.feedback_for(_UC, _QC, _REF, home=tmp_path) == (0.0, 1)


class TestBrokenLines:
    def test_corrupt_line_and_truncated_tail_are_skipped(self, tmp_path):
        """A JSONL reader that dies on a partial tail loses every signal in the file, because a
        process killed mid-write is the expected state, not the exceptional one."""
        run_dir = tmp_path / "workflows" / "runs" / "bbbb1111"
        run_dir.mkdir(parents=True)
        good_one = json.dumps(_verdict(event_id="bbbb1111-evt-1", verdict="PASS"))
        good_two = json.dumps(_verdict(event_id="bbbb1111-evt-3", verdict="REJECT"))
        truncated = '{"kind": "judge_verdict", "event_id": "bbbb1111-evt-4", "verdict": "PA'
        (run_dir / "events.jsonl").write_text(
            good_one + "\n" + "{not json at all\n" + good_two + "\n" + truncated,
            encoding="utf-8",
        )
        # The two valid records still count: (1 + 0) / 2.
        assert feedback.feedback_for(_UC, _QC, _REF, home=tmp_path) == (0.5, 2)

    def test_a_json_scalar_line_is_not_a_record(self, tmp_path):
        _write_ledger(
            tmp_path, "bbbb2222", [_verdict(event_id="bbbb2222-evt-1")], tail='"a bare string"\n7\n'
        )
        assert feedback.feedback_for(_UC, _QC, _REF, home=tmp_path) == (1.0, 1)


class TestAttribution:
    def test_a_model_with_no_use_case_is_dropped(self, tmp_path):
        """Dropping is defensible; guessing is not. An event that names a model but no use case
        cannot be attributed to a routing cell, and there is no cell to charge it to."""
        _write_ledger(
            tmp_path,
            "cccc1111",
            [
                _verdict(event_id="cccc1111-evt-1", use_case=None),
                _verdict(event_id="cccc1111-evt-2", query_class=None),
                _verdict(event_id="cccc1111-evt-3", use_case="", query_class=""),
            ],
        )
        assert feedback.feedback_index(home=tmp_path) == {}
        assert feedback.feedback_for(_UC, _QC, _REF, home=tmp_path) == (0.0, 0)

    def test_a_use_case_with_no_model_is_dropped(self, tmp_path):
        _write_ledger(
            tmp_path,
            "cccc2222",
            [
                _verdict(event_id="cccc2222-evt-1", provider=None, model=None),
                _verdict(event_id="cccc2222-evt-2", model=None),  # half a ref is not a ref
                _verdict(event_id="cccc2222-evt-3", provider=None),
            ],
        )
        assert feedback.feedback_index(home=tmp_path) == {}

    def test_an_unattributable_event_does_not_count_toward_n(self, tmp_path):
        """The precise-count assertion: the attributable record beside it still reads n=1, so the
        drop is a drop and not merely an unscored inclusion."""
        _write_ledger(
            tmp_path,
            "cccc3333",
            [
                _verdict(event_id="cccc3333-evt-1", verdict="PASS"),
                _verdict(event_id="cccc3333-evt-2", verdict="REJECT", use_case=None),
                _verdict(event_id="cccc3333-evt-3", verdict="REJECT", query_class=None),
                _verdict(event_id="cccc3333-evt-4", verdict="REJECT", model=None),
            ],
        )
        # n=1, and feedback stays 1.0: three REJECTs were dropped, not averaged in.
        assert feedback.feedback_for(_UC, _QC, _REF, home=tmp_path) == (1.0, 1)


class TestVerdictVocabulary:
    def test_an_unrecognised_verdict_is_dropped_not_scored(self, tmp_path):
        """DECISION: a verdict outside the closed vocabulary is DROPPED.

        The two alternatives both invent a number. Scored neutral (0.5) is a value nobody chose,
        silently steering routing; scored bad (0.0) punishes a model for this module failing to
        keep up with the judge vocabulary. Dropping keeps ``feedback_n`` an honest count of real
        observations and leaves the parser gap visible in the debug census.
        """
        _write_ledger(
            tmp_path,
            "dddd1111",
            [
                _verdict(event_id="dddd1111-evt-1", verdict="PASS"),
                _verdict(event_id="dddd1111-evt-2", verdict="MOSTLY_FINE"),
                _verdict(event_id="dddd1111-evt-3", verdict=""),
            ],
        )
        # Only the PASS counted: neither an unknown label nor a blank moved n or the mean.
        assert feedback.feedback_for(_UC, _QC, _REF, home=tmp_path) == (1.0, 1)

    def test_control_flow_verdicts_are_dropped(self, tmp_path):
        """RETRY/REPLAN/ESCALATE/NEEDS_INPUT are control flow, not assessments of the output.
        RETRY in particular names a recoverable transient — scoring it 0.0 would penalize a model
        for a network blip."""
        _write_ledger(
            tmp_path,
            "dddd2222",
            [
                _verdict(event_id="dddd2222-evt-1", verdict="REJECT"),
                _verdict(event_id="dddd2222-evt-2", verdict="RETRY"),
                _verdict(event_id="dddd2222-evt-3", verdict="REPLAN"),
                _verdict(event_id="dddd2222-evt-4", verdict="ESCALATE"),
                _verdict(event_id="dddd2222-evt-5", verdict="NEEDS_INPUT"),
            ],
        )
        assert feedback.feedback_for(_UC, _QC, _REF, home=tmp_path) == (0.0, 1)

    def test_cannot_judge_is_dropped(self, tmp_path):
        """A refusal that says why is still not an assessment."""
        _write_ledger(
            tmp_path,
            "dddd3333",
            [
                _verdict(event_id="dddd3333-evt-1", verdict="PASS"),
                _verdict(event_id="dddd3333-evt-2", verdict="REJECT", cannot_judge=True),
            ],
        )
        assert feedback.feedback_for(_UC, _QC, _REF, home=tmp_path) == (1.0, 1)

    def test_closed_vocabulary_matches_judge_contract(self):
        """The restated set is a cycle workaround, so it needs a drift ratchet: ``workflows``
        reaches ``routing`` through ``provider_bridge``, so ``feedback`` cannot import
        ``judge_contract`` at module level. A test can."""
        from personalclaw.workflows.judge_contract import Verdict

        assert feedback._KNOWN_VERDICTS == {v.value for v in Verdict}
        assert set(feedback._QUALITY_FEEDBACK) <= feedback._KNOWN_VERDICTS

    def test_runs_root_matches_the_workflows_store_layout(self):
        """The other cycle workaround: ``_RUNS_SUBPATH`` must stay the store's own layout. Pure
        path arithmetic — no filesystem read, so the real home is never touched."""
        from personalclaw.config.loader import config_dir
        from personalclaw.workflows import store as wf_store

        assert wf_store.runs_root() == Path(config_dir()).joinpath(*feedback._RUNS_SUBPATH)


class TestCountingIsPrecise:
    def test_one_event_read_twice_counts_once(self, tmp_path):
        """``event_id`` is deterministic (``<run>-evt-<seq>``), so a re-emitted or duplicated line
        is the SAME observation. Counting it twice inflates ``feedback_n`` toward the downstream
        ``n >= 5`` floor on no new evidence."""
        rec = _verdict(event_id="eeee1111-evt-1", verdict="PASS")
        _write_ledger(tmp_path, "eeee1111", [rec, rec, rec])
        assert feedback.feedback_for(_UC, _QC, _REF, home=tmp_path) == (1.0, 1)

    def test_distinct_events_with_the_same_verdict_both_count(self, tmp_path):
        """The dedup keys on the id, not the payload — two real observations are two."""
        _write_ledger(
            tmp_path,
            "eeee2222",
            [
                _verdict(event_id="eeee2222-evt-1", verdict="PASS"),
                _verdict(event_id="eeee2222-evt-2", verdict="PASS"),
            ],
        )
        assert feedback.feedback_for(_UC, _QC, _REF, home=tmp_path) == (1.0, 2)


class TestIndexAgreesWithPerCell:
    def _many_cell_home(self, tmp_path: Path) -> dict[tuple[str, str, str], tuple[float, int]]:
        """Two runs, five cells, hand-computed expectations."""
        other_ref = ref_of("openai", "gpt-4o-mini")
        _write_ledger(
            tmp_path,
            "ffff1111",
            [
                _verdict(event_id="ffff1111-evt-1", verdict="PASS"),
                _verdict(event_id="ffff1111-evt-2", verdict="REJECT"),
                _verdict(
                    event_id="ffff1111-evt-3",
                    verdict="PASS",
                    provider="openai",
                    model="gpt-4o-mini",
                ),
                _verdict(event_id="ffff1111-evt-4", verdict="PASS", query_class="code"),
                _verdict(event_id="ffff1111-evt-5", verdict="RETRY", query_class="code"),
            ],
        )
        _write_ledger(
            tmp_path,
            "ffff2222",
            [
                _verdict(event_id="ffff2222-evt-1", verdict="PASS"),
                _verdict(event_id="ffff2222-evt-2", verdict="REJECT", use_case="background"),
                _verdict(event_id="ffff2222-evt-3", verdict="PASS", use_case="background"),
                _verdict(event_id="ffff2222-evt-4", verdict="PASS", use_case="loops", model=None),
            ],
        )
        return {
            (_UC, _QC, _REF): (round(2 / 3, 4), 3),  # PASS, REJECT, PASS across two runs
            (_UC, _QC, other_ref): (1.0, 1),
            (_UC, "code", _REF): (1.0, 1),  # the RETRY beside it dropped
            ("background", _QC, _REF): (0.5, 2),
        }

    def test_index_matches_hand_computed_cells(self, tmp_path):
        expected = self._many_cell_home(tmp_path)
        assert feedback.feedback_index(home=tmp_path) == expected

    def test_index_agrees_with_feedback_for_cell_by_cell(self, tmp_path):
        """The assertion that stops the two drifting: same numbers for every cell, and ``(0.0, 0)``
        for a cell the index does not hold."""
        expected = self._many_cell_home(tmp_path)
        index = feedback.feedback_index(home=tmp_path)
        assert index, "vacuity floor: the fixture produced no cells at all"
        for cell, value in index.items():
            assert feedback.feedback_for(*cell, home=tmp_path) == value
        for cell in expected:
            assert feedback.feedback_for(*cell, home=tmp_path) == expected[cell]
        # The `loops` record had no model, so it is not a cell — and reads as no signal.
        assert feedback.feedback_for("loops", _QC, _REF, home=tmp_path) == (0.0, 0)

    def test_the_fixture_is_actually_read(self, tmp_path):
        """VACUITY FLOOR. An unreadable fixture and an empty ledger are indistinguishable, so
        every "no signal" assertion above would pass for the wrong reason without this."""
        self._many_cell_home(tmp_path)
        index = feedback.feedback_index(home=tmp_path)
        assert any(n > 0 for _fb, n in index.values())
        assert sum(n for _fb, n in index.values()) == 7


class TestWritesNothing:
    def test_no_file_is_created_under_the_home(self, tmp_path):
        """This module reads. A feedback extractor that wrote would be a second fold beside
        ``routing_stats.json``, and the fold is the one durable record."""
        before = _all_paths(tmp_path)
        assert feedback.feedback_index(home=tmp_path) == {}
        assert feedback.feedback_for(_UC, _QC, _REF, home=tmp_path) == (0.0, 0)
        assert _all_paths(tmp_path) == before

    def test_no_file_is_created_beside_a_real_ledger(self, tmp_path):
        _write_ledger(
            tmp_path,
            "9999aaaa",
            [
                _verdict(event_id="9999aaaa-evt-1", verdict="PASS"),
                _verdict(event_id="9999aaaa-evt-2", verdict="REJECT", use_case=None),
            ],
        )
        before = _all_paths(tmp_path)
        assert feedback.feedback_index(home=tmp_path) == {_CELL: (1.0, 1)}
        assert feedback.feedback_for(_UC, _QC, _REF, home=tmp_path) == (1.0, 1)
        assert feedback.feedback_for("nope", "nope", "nope:nope", home=tmp_path) == (0.0, 0)
        assert _all_paths(tmp_path) == before
        # No stats fold was written either — this module is not a second writer of it.
        assert not (tmp_path / "routing_stats.json").exists()

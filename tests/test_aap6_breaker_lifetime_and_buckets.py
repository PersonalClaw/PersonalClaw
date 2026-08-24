"""ACP-AGENT-PARITY §2.3 gap 5 (atom ``AAP-6``) — the two reasons clause 2 could not close.

The prior tick left clause 2 (*"a deliberately failing-tool ACP session trips the circuit
and aborts the turn with the standard breaker message"*) met on kiro and codex and blocked
on claude-code, with two findings recorded rather than fixed:

* **``G154`` (HIGH) — the bucket fragmented.** claude-code sends ``description`` on every
  Bash call, and a model enumerating its own retries writes "Run boom command (1 of 4)" …
  "(4 of 4)". Four byte-identical commands therefore produced four streaks of one and no
  rung fired. Deliberately NOT fixed by stripping ``description`` outright, because for a
  tool whose payload IS a description that merges genuinely different calls and the breaker
  starts aborting healthy turns. Fixed here by dropping annotation keys **only when a
  behavioural key survives beside them** — the narrow reading the finding asked for.
* **``G155`` (MEDIUM) — the circuit rung was unreachable by construction.** ``_acp_breaker``
  was a local in ``_run_chat``, i.e. per TURN, while ``CIRCUIT_THRESHOLD = 30`` is defined by
  ``LoopBreaker`` itself as "this **run's** total failures". An unattended loop repeating a
  failing tool for twenty turns reset the counter every turn and never tripped. Recorded as
  an E3 (the lifetime is a design decision, not an implementation detail). Owner ruling: the
  host-side analogue of a native run is the SESSION, so the breaker lives there.

Both fixes are measured on identity and counting rather than on wording, so a later change
to the notice text cannot make these vacuous.
"""

from __future__ import annotations

from personalclaw.guardrails.loop_breaker import (
    ANNOTATION_ARG_KEYS,
    BLOCK_THRESHOLD,
    CIRCUIT_THRESHOLD,
    WARN_THRESHOLD,
    LoopBreaker,
    normalize_call_args,
    params_key,
)

# The exact shape measured on claude-code: identical command, enumerated description.
_BOOM = "bash -c 'echo boom >&2; exit 3'"


def _claude_call(n: int) -> dict:
    return {"command": _BOOM, "description": f"Run boom command ({n} of 4)"}


class TestAnnotationKeysNoLongerFragmentTheBucket:
    def test_four_enumerated_retries_are_one_bucket(self):
        """`G154` verbatim. Before this, four identical commands → four keys → no rung."""
        keys = {params_key("Bash", _claude_call(n)) for n in (1, 2, 3, 4)}
        assert len(keys) == 1, f"still fragmenting: {keys}"

    def test_the_streak_now_reaches_both_rungs(self):
        """The consequence that matters: the rungs fire on repetition. Counted, not read
        off the notice text."""
        b = LoopBreaker()
        streaks = [b.record(params_key("Bash", _claude_call(n)), True) for n in (1, 2, 3, 4, 5)]
        assert streaks == [1, 2, 3, 4, 5], streaks
        assert streaks[WARN_THRESHOLD - 1] >= WARN_THRESHOLD
        assert streaks[BLOCK_THRESHOLD - 1] >= BLOCK_THRESHOLD

    def test_a_different_command_is_still_a_different_bucket(self):
        """Vacuity floor for the merge: only the annotation is ignored, not the args."""
        a = params_key("Bash", {"command": _BOOM, "description": "x"})
        c = params_key("Bash", {"command": "ls -la", "description": "x"})
        assert a != c

    def test_a_description_only_tool_keeps_keying_on_its_description(self):
        """The reason the prior tick refused a blanket strip: for a tool whose payload IS
        a description, the description is the behaviour. Two different descriptions must
        stay two buckets, or the breaker starts aborting healthy turns."""
        one = params_key("TodoWrite", {"description": "add auth"})
        two = params_key("TodoWrite", {"description": "delete auth"})
        assert one != two
        assert normalize_call_args({"description": "add auth"}) == {"description": "add auth"}

    def test_the_acp_json_string_shape_is_handled_too(self):
        """ACP hands arguments over as an opaque JSON string; the fix has to reach through
        it or it only ever works for the native dict shape."""
        import json

        a = params_key("Bash", json.dumps(_claude_call(1)))
        b = params_key("Bash", json.dumps(_claude_call(4)))
        assert a == b

    def test_adapter_nonces_and_annotations_are_both_dropped_together(self):
        """kiro's `__tool_use_purpose` (`G152`) and claude-code's `description` (`G154`) are
        the same defect through different doors — a call carrying BOTH must still be one
        bucket."""
        a = params_key("Bash", {"command": _BOOM, "__tool_use_purpose": "first", "reason": "1/4"})
        b = params_key("Bash", {"command": _BOOM, "__tool_use_purpose": "second", "reason": "4/4"})
        assert a == b

    def test_purpose_is_in_the_annotation_set_but_only_as_a_bare_key(self):
        """`purpose` is an annotation; `__tool_use_purpose` was already covered by the
        dunder rule. Keeping both is deliberate — an adapter that drops the prefix in a
        later version must not silently re-fragment the bucket."""
        assert "purpose" in ANNOTATION_ARG_KEYS
        assert normalize_call_args({"path": "/tmp/a", "purpose": "look"}) == {"path": "/tmp/a"}


class TestTheBreakerLivesForTheSession:
    def test_the_session_owns_one_breaker_instance(self):
        from personalclaw.dashboard.state import _ChatSession

        s = _ChatSession("dashboard:aap6")
        assert isinstance(s._acp_breaker, LoopBreaker)
        assert s._acp_breaker is s._acp_breaker  # a stable instance, not a property

    def test_two_sessions_do_not_share_a_breaker(self):
        """Vacuity floor: a class-level instance would make every session's failures one
        pool, and one wedged loop would abort an unrelated chat."""
        from personalclaw.dashboard.state import _ChatSession

        a, b = _ChatSession("dashboard:a"), _ChatSession("dashboard:b")
        a._acp_breaker.record("k", True)
        assert a._acp_breaker.total_failures == 1
        assert b._acp_breaker.total_failures == 0

    def test_failures_accumulate_across_turns_so_the_circuit_is_reachable(self):
        """`G155` verbatim: an unattended loop failing the same tool for twenty turns.
        Simulated as twenty turns of two failures — a per-turn breaker would have reported
        2 every time and never tripped; the session-scoped one reaches the ceiling."""
        from personalclaw.dashboard.state import _ChatSession

        s = _ChatSession("dashboard:loop")
        key = params_key("Bash", {"command": _BOOM})
        for _turn in range(20):
            breaker = s._acp_breaker  # exactly what _run_chat now reads, once per turn
            breaker.record(key, True)
            breaker.record(key, True)
        assert s._acp_breaker.total_failures == 40
        assert s._acp_breaker.total_failures > CIRCUIT_THRESHOLD
        assert s._acp_breaker.circuit_tripped() is True

    def test_a_fresh_breaker_per_turn_would_not_have_tripped(self):
        """The counter-factual, asserted so the fix's necessity is visible in the suite
        rather than only in the plan's log."""
        for _turn in range(20):
            per_turn = LoopBreaker()
            per_turn.record("Bash:{}", True)
            per_turn.record("Bash:{}", True)
            assert per_turn.total_failures == 2
            assert per_turn.circuit_tripped() is False

    def test_recovery_still_clears_the_key_so_intermittency_is_not_punished(self):
        """Session lifetime must not turn a flaky-but-surviving tool into a blocked one:
        `record()` clears the KEY's streak on success. The total (the circuit's input)
        deliberately still counts the failures that happened."""
        from personalclaw.dashboard.state import _ChatSession

        s = _ChatSession("dashboard:flaky")
        key = params_key("Bash", {"command": "flaky"})
        for _ in range(10):
            s._acp_breaker.record(key, True)
            s._acp_breaker.record(key, True)
            assert s._acp_breaker.record(key, False) == 0, "a success must clear the streak"
        assert s._acp_breaker.count(key) == 0
        assert s._acp_breaker.total_failures == 20

    def test_run_chat_reads_the_session_breaker_not_a_local(self):
        """Pins the wiring itself. The whole defect was a `LoopBreaker()` constructed in
        `_run_chat`, and a future refactor could reintroduce it without any behavioural
        test noticing inside one turn."""
        import pathlib

        import personalclaw

        src = (
            pathlib.Path(personalclaw.__file__).parent / "dashboard" / "chat_runner.py"
        ).read_text()
        assert "_acp_breaker = session._acp_breaker" in src
        assert "_acp_breaker = LoopBreaker()" not in src

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

**The third finding, and the reason this file grew a config class.** With the lifetime fixed,
the rung was reachable in principle and still *undrivable in practice*: ``CIRCUIT_THRESHOLD``
was a bare module constant with no ``os.getenv`` and no config read anywhere in
``loop_breaker.py``, so proving clause 2 on a real instance needed **more than thirty genuine
tool failures in one run** and there was no way to ask for a lower bar. (Positive control for
that zero: two siblings in the same package, ``guardrails/ceiling.py`` and
``guardrails/writes.py``, DO read env — so the absence was specific to this file, not a grep
artifact.) ``guardrails.loop_breaker.circuit_threshold`` is that seam. The default is
unchanged at 30; what is new is that a number below it can be asked for, which is what makes
the abort observable at all.
"""

from __future__ import annotations

import json

from personalclaw.guardrails.loop_breaker import (
    ANNOTATION_ARG_KEYS,
    BLOCK_THRESHOLD,
    CIRCUIT_THRESHOLD,
    WARN_THRESHOLD,
    LoopBreaker,
    configured_circuit_threshold,
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


def _write_home(tmp_path, monkeypatch, cfg: dict):
    """Point the loader at a tmp home holding exactly ``cfg``, and return it.

    Writes a REAL ``config.json`` and goes through ``AppConfig.load()`` rather than
    constructing the dataclass: the defect was a missing *seam*, so a test that handed the
    breaker a pre-built config object would pass with ``load()`` still dropping the key.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return tmp_path


class TestTheCircuitCeilingIsConfigurable:
    """The ceiling is the ONE rung an operator can retune, and the round trip is what makes
    the clause drivable on a shared instance instead of only in a unit test."""

    def test_the_default_is_unchanged_so_no_existing_run_aborts_sooner(self, tmp_path, monkeypatch):
        """This atom adds a seam; it does not re-tune the breaker. An empty config must
        still resolve to the shipped 30."""
        _write_home(tmp_path, monkeypatch, {})
        assert configured_circuit_threshold() == CIRCUIT_THRESHOLD == 30
        assert LoopBreaker().circuit_threshold == 30

    def test_a_lowered_ceiling_trips_the_circuit_at_that_number(self, tmp_path, monkeypatch):
        """The whole point: with the ceiling at 3, four failures abort the run — so the
        clause can be driven on a real instance without manufacturing 31 broken tool calls."""
        _write_home(
            tmp_path, monkeypatch, {"guardrails": {"loop_breaker": {"circuit_threshold": 3}}}
        )
        b = LoopBreaker()
        assert b.circuit_threshold == 3
        for _ in range(3):
            b.record("Bash:{}", True)
        assert b.circuit_tripped() is False, "at the ceiling, not past it — the compare is strict"
        b.record("Bash:{}", True)
        assert b.circuit_tripped() is True

    def test_the_resolution_really_reads_THE_CONFIG_not_just_a_constructor_pin(
        self, tmp_path, monkeypatch
    ):
        """Vacuity floor. A default-constructed breaker is what production builds
        (``runtime.py`` and ``state.py`` both call ``LoopBreaker()`` with no arguments), so
        the config has to reach *that* object or the seam exists only for callers who already
        knew the number."""
        _write_home(
            tmp_path, monkeypatch, {"guardrails": {"loop_breaker": {"circuit_threshold": 7}}}
        )
        assert LoopBreaker().circuit_threshold == 7
        assert LoopBreaker().circuit_threshold != CIRCUIT_THRESHOLD

    def test_zero_is_floored_to_one_so_a_first_failure_never_aborts_a_run(
        self, tmp_path, monkeypatch
    ):
        """``circuit_tripped`` compares ``total_failures > ceiling``, so a ceiling of 0 would
        abort a run on its FIRST failed tool call and make ordinary retry impossible. The
        floor is in ``load()`` and mirrored by ``_EDITABLE_CONFIG``'s ``min: 1``."""
        _write_home(
            tmp_path, monkeypatch, {"guardrails": {"loop_breaker": {"circuit_threshold": 0}}}
        )
        b = LoopBreaker()
        assert b.circuit_threshold == 1
        b.record("Bash:{}", True)
        assert b.circuit_tripped() is False

    def test_a_junk_value_falls_back_to_the_shipped_ceiling(self, tmp_path, monkeypatch):
        """A typo must not take the breaker out of service, and must not raise out of
        ``load()`` either — the rung is an ABORT, so its failure mode has to be the default."""
        _write_home(
            tmp_path, monkeypatch, {"guardrails": {"loop_breaker": {"circuit_threshold": "lots"}}}
        )
        assert LoopBreaker().circuit_threshold == CIRCUIT_THRESHOLD

    def test_an_unreadable_config_leaves_the_shipped_ceiling_standing(self, monkeypatch):
        """Same polarity one layer up: if the config read itself blows up, the breaker keeps
        the shipped ceiling rather than losing its circuit rung."""
        import personalclaw.config.loader as loader

        def _boom():
            raise RuntimeError("no config today")

        monkeypatch.setattr(loader.AppConfig, "load", staticmethod(_boom))
        assert configured_circuit_threshold() == CIRCUIT_THRESHOLD

    def test_an_explicit_pin_beats_the_config_and_survives_reset(self, tmp_path, monkeypatch):
        """The injection point, for a caller that owns the number (a harness driving the
        rung). It must survive ``reset()``, which re-reads the config for everyone else."""
        _write_home(
            tmp_path, monkeypatch, {"guardrails": {"loop_breaker": {"circuit_threshold": 9}}}
        )
        b = LoopBreaker(circuit_threshold=2)
        assert b.circuit_threshold == 2
        b.reset()
        assert b.circuit_threshold == 2

    def test_reset_rearms_the_config_read_so_an_edit_binds_on_the_next_run(
        self, tmp_path, monkeypatch
    ):
        """``runtime.py`` resets per turn, so a ceiling edited from Settings must bind on the
        next run rather than on the next gateway restart."""
        _write_home(
            tmp_path, monkeypatch, {"guardrails": {"loop_breaker": {"circuit_threshold": 20}}}
        )
        b = LoopBreaker()
        assert b.circuit_threshold == 20
        (tmp_path / "config.json").write_text(
            json.dumps({"guardrails": {"loop_breaker": {"circuit_threshold": 4}}}), encoding="utf-8"
        )
        assert b.circuit_threshold == 20, "cached within the run — one read per run, not per call"
        b.reset()
        assert b.circuit_threshold == 4

    def test_a_clean_run_answers_without_reading_the_config_at_all(self, monkeypatch):
        """``circuit_tripped`` is called on EVERY tool result in the native runtime, so the
        happy path must not cost an uncached ``AppConfig.load()`` per tool call. Zero failures
        can never trip a ceiling floored at 1, so the read is skipped."""
        import personalclaw.guardrails.loop_breaker as lb

        calls: list[int] = []
        monkeypatch.setattr(lb, "configured_circuit_threshold", lambda: (calls.append(1), 30)[1])
        b = lb.LoopBreaker()
        for _ in range(50):
            assert b.circuit_tripped() is False
        assert calls == [], "a clean run read the config"
        b.record("Bash:{}", True)
        b.circuit_tripped()
        b.circuit_tripped()
        assert len(calls) == 1, f"resolved once per run, not per call: {len(calls)}"

    def test_the_field_round_trips_through_load_and_to_dict(self, tmp_path, monkeypatch):
        """Contract points 2 and 3. ``to_dict`` goes through ``asdict(self.guardrails)``, so a
        section that never reached the dataclass is the only way this can fail."""
        from personalclaw.config.loader import AppConfig

        _write_home(
            tmp_path, monkeypatch, {"guardrails": {"loop_breaker": {"circuit_threshold": 12}}}
        )
        cfg = AppConfig.load()
        assert cfg.guardrails.loop_breaker.circuit_threshold == 12
        assert cfg.to_dict()["guardrails"]["loop_breaker"] == {"circuit_threshold": 12}

    def test_the_write_path_is_allowlisted_with_the_same_floor_load_enforces(self):
        """Contract point 4. Without this the Settings control 400s while every backend test
        stays green — the exact gap ``test_config_section_modules``' docstring names."""
        from personalclaw.config.edit_spec import security_control
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        spec = _EDITABLE_CONFIG["guardrails.loop_breaker.circuit_threshold"]
        # The validation shape, exactly; `security` is the field's place on the security list
        # (a higher ceiling loosens it — tests/test_security_posture_rail.py).
        assert {k: v for k, v in spec.items() if k != "security"} == {
            "type": "int",
            "min": 1,
            "max": 1000,
        }
        assert security_control(spec) is not None

    def test_the_settings_control_exists_and_patches_that_path(self):
        """Contract point 5. The ceiling is user-facing: its abort lands in the user's own
        chat as an error message, and the panel already owned a section called "Circuit
        breaker" for a DIFFERENT breaker — so omitting this control would leave a Settings
        page that shows the provider knobs and hides the one that stops a run."""
        import pathlib

        panel = (
            pathlib.Path(__file__).resolve().parent.parent
            / "web/src/pages/settings/GuardrailsPanel.tsx"
        ).read_text(encoding="utf-8")
        assert "loop_breaker.circuit_threshold" in panel
        assert "Tool-loop breaker" in panel
        # And the two breakers must not both be called just "Circuit breaker" — one section
        # holding that title beside the other's knobs is how a user reads the provider
        # threshold as if it governed tool retries.
        assert "Provider circuit breaker" in panel

    def test_the_module_constant_is_now_only_a_default(self):
        """The bare-constant defect, pinned: no production module may *evaluate*
        ``CIRCUIT_THRESHOLD``, because a comparison against it would bypass the config field
        and make the seam decoration.

        Walked as an AST, not grepped: the prose in this module and in ``loop_breaker``'s own
        docstrings names the constant repeatedly, and a line-based sweep reports every one of
        those sentences. The only legitimate evaluations are inside ``loop_breaker`` itself —
        its own declaration and the fallback return — so the census is a count there and a
        zero everywhere else.
        """
        import ast
        import pathlib

        import personalclaw

        src_root = pathlib.Path(personalclaw.__file__).parent
        owner = src_root / "guardrails" / "loop_breaker.py"
        hits: dict[str, int] = {}
        for p in src_root.rglob("*.py"):
            tree = ast.parse(p.read_text(encoding="utf-8"))
            n = sum(
                1
                for node in ast.walk(tree)
                if (isinstance(node, ast.Name) and node.id == "CIRCUIT_THRESHOLD")
                or (isinstance(node, ast.Attribute) and node.attr == "CIRCUIT_THRESHOLD")
            )
            if n:
                hits[str(p.relative_to(src_root))] = n
        assert set(hits) == {
            "guardrails/loop_breaker.py"
        }, f"a consumer still evaluates the constant instead of the ceiling: {hits}"
        # Its declaration and the one fallback return in `configured_circuit_threshold`.
        assert hits["guardrails/loop_breaker.py"] == 2, hits
        assert "return CIRCUIT_THRESHOLD" in owner.read_text(encoding="utf-8")

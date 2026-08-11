"""The two-layer context-compaction ladder for LLM-backed workflow nodes (WV-12).

What is actually load-bearing here, and why each is a test rather than a review comment:

* **The proactive layer fires on MEASUREMENT, not on a guess.** A test that only checked
  "a huge prompt gets compacted" would pass against a hardcoded char cap. These pin the
  threshold to `model_context_window` by driving the SAME prompt against a small-window
  model and a large-window one and asserting only the small one compacts.
* **Layer 2 is length-specific.** A ladder that retried every exception would spend a
  second model call on a 429 or a bad credential — things compaction provably cannot fix.
* **Degrade-to-drop-with-placeholder is the rung that keeps a node alive.** "The
  summarizer failed" means a supplied `summarize_fn` RAISED — `summarize_fn=None` is a
  working configuration (`compact` digests deterministically), so the raise is the case
  worth proving, and the placeholder must NAME the loss rather than silently substituting
  a weaker artifact that reads downstream as a complete summary.
* **Anti-thrashing is wired, not reimplemented.** The controller owns the per-node history
  and the ladder appends to it in place; a copy handed out per call would leave
  `should_compact` looking at a permanently empty list.
* **Tool-pair integrity, anchoring and the prefix guard are NOT re-tested here.**
  `context_compaction` owns them and `test_context_compaction.py` covers them. What IS
  tested is that this module routes through that seam, because a second implementation of
  those rules is the drift risk — an orphaned tool-result breaks the provider.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.workflows import compaction as C
from personalclaw.workflows import store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.blocks import resolve_spec
from personalclaw.workflows.bundled_defs import bundled_root
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.engine import dispatch_gate, dispatch_infer
from personalclaw.workflows.macros import expand_spec
from personalclaw.workflows.models import InstanceState, Node, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """The end-to-end tests drive a REAL run — controller, journal and state on disk.

    🔴 `PERSONALCLAW_HOME` as well as the `store.config_dir` patch, and the env var is the
    load-bearing half. Patching `workflows.store.config_dir` alone is not isolation: a run
    reaches other modules that bind `config_dir` themselves, and `tasks/native.py:46
    _tasks_dir()` is one of them. Measured on the first draft of this test — with only the
    store patched, a single `audit-sweep` run read the developer's REAL task store (43,277
    files) through `native.py:288`'s `_task_map()`, which stamps a derived comment count
    per task (`native.py:145`), producing ~259,000 file opens and a 35-second run that
    tripped the suite's 120s timeout. The runtime was never the compaction ladder; it was a
    test escaping its home.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


#: A model with a genuinely small window in the shipped table (8192 tokens), so the
#: threshold can be crossed by a prompt small enough to keep a test readable.
SMALL_MODEL = "gpt-4"
#: 1,000,000 tokens in the same table — the same prompt must NOT trip this one.
BIG_MODEL = "claude-opus-4.8"


def _n(d: dict) -> Node:
    return Node.from_dict(d)


def _long_prompt(blocks: int = 40, block_chars: int = 1200) -> str:
    """A prompt shaped like a long-horizon node's: many accumulated paragraph blocks,
    with a distinct opening frame and a distinct trailing instruction."""
    body = "\n\n".join(f"finding {i}: " + ("x" * block_chars) for i in range(blocks))
    return f"FRAME: you are auditing a target.\n\n{body}\n\nINSTRUCTION: answer the question."


class TestBudget:
    def test_the_threshold_comes_from_the_bound_model_window(self) -> None:
        """The whole point of "~80% of the bound model window": two models with different
        windows get different budgets. A hardcoded cap would make these equal."""
        small = C.prompt_char_budget(SMALL_MODEL)
        big = C.prompt_char_budget(BIG_MODEL)
        assert small == int(8192 * C.CHARS_PER_TOKEN * C.COMPACT_AT_FRACTION)
        assert big > small * 100

    def test_an_unresolvable_model_falls_back_to_a_real_budget_not_to_unbounded(self) -> None:
        """A missing table entry must not silently DISABLE the ladder. `model_windows`'
        conservative default applies, so an unknown model still gets compacted eventually."""
        budget = C.prompt_char_budget("no-such-model-anywhere")
        assert budget > 0
        assert budget == int(200_000 * C.CHARS_PER_TOKEN * C.COMPACT_AT_FRACTION)


class TestSegmentation:
    def test_a_prompt_round_trips_through_segmentation(self) -> None:
        prompt = "one\n\ntwo\n\nthree"
        assert C.join_segments(C.segment_prompt(prompt)) == prompt

    def test_blank_runs_cannot_pad_the_protected_regions(self) -> None:
        """Empty blocks are dropped. Kept, a run of newlines would fill `protect_head`
        with nothing and push real content into the droppable middle."""
        segs = C.segment_prompt("a\n\n\n\n\n\nb")
        assert [s["content"] for s in segs] == ["a", "b"]

    def test_a_prompt_with_no_middle_is_returned_verbatim(self) -> None:
        """Nothing droppable ⇒ no change and no reported saving. Reporting a saving here
        would let the caller believe the prompt shrank when it did not."""
        out, saved = C.compact_prompt("only one block")
        assert out == "only one block"
        assert saved == 0.0


class TestCompactPrompt:
    def test_it_shrinks_a_long_prompt_and_keeps_the_frame_and_the_instruction(self) -> None:
        """Head and tail protection is what makes this safe for a CONCATENATED prompt: the
        framing the reader needs first and the instruction it must act on are the two
        things that cannot be folded away."""
        prompt = _long_prompt()
        out, saved = C.compact_prompt(prompt)
        assert saved > 0.5
        assert len(out) < len(prompt)
        assert "FRAME: you are auditing a target." in out
        assert "INSTRUCTION: answer the question." in out

    def test_it_routes_through_the_shared_compaction_seam(self) -> None:
        """The prefix guard in the output is the evidence: it is `context_compaction`'s
        string, not one this module owns. Re-deriving the fencing/anchoring/tool-pair rules
        here instead of reusing that seam is the drift this asserts against."""
        out, _saved = C.compact_prompt(_long_prompt())
        assert "[CONTEXT COMPACTION — REFERENCE ONLY" in out
        assert "[END CONTEXT COMPACTION]" in out

    def test_aggressive_frees_more_than_proactive(self) -> None:
        """Layer 2 must actually be a HARDER squeeze — otherwise the retry sends a prompt
        of the same size to the provider that just rejected it for size."""
        prompt = _long_prompt()
        _p, gentle = C.compact_prompt(prompt)
        _a, hard = C.compact_prompt(prompt, aggressive=True)
        assert hard > gentle

    def test_a_supplied_summarizer_is_used(self) -> None:
        called: list[int] = []

        def summarize(middle: list[dict]) -> str:
            called.append(len(middle))
            return "SUMMARY OF THE MIDDLE"

        out, saved = C.compact_prompt(_long_prompt(), summarize_fn=summarize)
        assert called and called[0] > 0
        assert "SUMMARY OF THE MIDDLE" in out
        assert saved > 0.5

    def test_a_compaction_that_would_grow_the_prompt_is_discarded(self) -> None:
        """A summarizer returning something longer than what it replaced is not an
        improvement, and shipping it as one would push the prompt further over budget."""
        prompt = _long_prompt(blocks=8, block_chars=10)

        def bloat(middle: list[dict]) -> str:
            return "B" * 100_000

        out, saved = C.compact_prompt(prompt, summarize_fn=bloat)
        assert out == prompt
        assert saved == 0.0


class TestDegradeToPlaceholder:
    """The rung that decides whether a long-horizon node dies or survives."""

    def test_a_raising_summarizer_degrades_to_a_placeholder_instead_of_failing(self) -> None:
        def broken(middle: list[dict]) -> str:
            raise RuntimeError("summarizer provider is down")

        out, saved = C.compact_prompt(_long_prompt(), summarize_fn=broken)
        # It did not raise, it still shrank, and both ends survived.
        assert saved > 0.5
        assert "FRAME: you are auditing a target." in out
        assert "INSTRUCTION: answer the question." in out

    def test_the_placeholder_names_the_loss_rather_than_hiding_it(self) -> None:
        """A dropped middle must be visibly dropped. The failure mode this guards is a
        downstream reader treating an absence as "there was nothing earlier" — which is
        exactly how a synthesis reports absence as evidence."""

        def broken(middle: list[dict]) -> str:
            raise RuntimeError("nope")

        out, _saved = C.compact_prompt(_long_prompt(), summarize_fn=broken)
        assert "DROPPED (not summarized)" in out
        assert "were REMOVED" in out
        assert "do not infer that it was empty" in out
        # And it does NOT masquerade as the deterministic digest, which would read as a
        # successful summary of content that was actually thrown away.
        assert "## Earlier conversation (compacted)" not in out

    def test_no_summarizer_is_not_a_failure(self) -> None:
        """`summarize_fn=None` is a WORKING configuration — `compact` produces its
        structured deterministic digest — so it must not take the placeholder path."""
        out, saved = C.compact_prompt(_long_prompt(), summarize_fn=None)
        assert saved > 0.5
        assert "DROPPED (not summarized)" not in out
        assert "## Earlier conversation (compacted)" in out


class TestOverflowDetection:
    @pytest.mark.parametrize(
        "message",
        [
            "context_length_exceeded",
            "prompt is too long: 210000 tokens > 200000",
            "This model's maximum context length is 8192 tokens",
            "context window exceeded",
        ],
    )
    def test_it_recognizes_a_length_rejection(self, message: str) -> None:
        assert C.is_context_overflow(RuntimeError(message)) is True

    @pytest.mark.parametrize(
        "message",
        [
            "429 rate limit exceeded",
            "invalid credential",
            "connection reset by peer",
            "the model returned malformed json",
        ],
    )
    def test_it_does_not_claim_failures_compaction_cannot_fix(self, message: str) -> None:
        """This is the discrimination layer 2 depends on. Treating a 429 as an overflow
        would burn a second model call on something a smaller prompt cannot fix."""
        assert C.is_context_overflow(RuntimeError(message)) is False


class TestTheLadder:
    """`complete_with_compaction` — both layers, around one call."""

    def _fn(self, seen: list[str], fail_first: BaseException | None = None):
        state = {"failed": False}

        async def fn(prompt, *, use_case="background", output_type=None, model=""):
            seen.append(prompt)
            if fail_first is not None and not state["failed"]:
                state["failed"] = True
                raise fail_first
            return "ok"

        return fn

    async def test_a_small_prompt_is_sent_untouched(self) -> None:
        """The ladder must be invisible below the threshold — the overwhelming majority of
        node calls, which must not pay for a summarizer or lose a single character."""
        seen: list[str] = []
        out = await C.complete_with_compaction(
            self._fn(seen),
            "a short prompt",
            use_case="background",
            model_resolver=lambda uc: SMALL_MODEL,
        )
        assert out == "ok"
        assert seen == ["a short prompt"]

    async def test_layer_1_compacts_at_the_threshold_of_the_bound_model(self) -> None:
        seen: list[str] = []
        prompt = _long_prompt()
        assert len(prompt) > C.prompt_char_budget(SMALL_MODEL)
        await C.complete_with_compaction(
            self._fn(seen),
            prompt,
            use_case="reasoning",
            model_resolver=lambda uc: SMALL_MODEL,
        )
        assert len(seen) == 1
        assert len(seen[0]) < len(prompt)
        assert "[CONTEXT COMPACTION — REFERENCE ONLY" in seen[0]

    async def test_the_same_prompt_is_untouched_on_a_wide_window_model(self) -> None:
        """🔴 The measurement test. The prompt is IDENTICAL to the one above; only the bound
        model differs. A hardcoded char cap — or a threshold read from the wrong model —
        would compact both, and this is what catches that."""
        seen: list[str] = []
        prompt = _long_prompt()
        await C.complete_with_compaction(
            self._fn(seen),
            prompt,
            use_case="reasoning",
            model_resolver=lambda uc: BIG_MODEL,
        )
        assert seen == [prompt]

    async def test_a_pinned_model_is_what_gets_measured(self) -> None:
        """A cross-family judge pin can have a different window than the worker axis.
        Budgeting against the axis while RUNNING the pin is how the check silently stops
        applying, so the pin must win — here it makes a prompt the axis would have
        compacted go through untouched."""
        seen: list[str] = []
        prompt = _long_prompt()
        await C.complete_with_compaction(
            self._fn(seen),
            prompt,
            use_case="reasoning",
            model=BIG_MODEL,
            model_resolver=lambda uc: SMALL_MODEL,
        )
        assert seen == [prompt]

    async def test_layer_2_recompacts_and_retries_once_on_a_length_rejection(self) -> None:
        seen: list[str] = []
        fn = self._fn(seen, fail_first=RuntimeError("context_length_exceeded"))
        out = await C.complete_with_compaction(
            fn,
            _long_prompt(),
            use_case="reasoning",
            model_resolver=lambda uc: BIG_MODEL,  # layer 1 stays out of the way
        )
        assert out == "ok"
        assert len(seen) == 2, "exactly one retry — not zero, and not a loop"
        assert len(seen[1]) < len(seen[0])

    async def test_layer_2_does_not_retry_a_failure_compaction_cannot_fix(self) -> None:
        seen: list[str] = []
        fn = self._fn(seen, fail_first=RuntimeError("429 rate limit exceeded"))
        with pytest.raises(RuntimeError, match="rate limit"):
            await C.complete_with_compaction(
                fn, _long_prompt(), use_case="reasoning", model_resolver=lambda uc: BIG_MODEL
            )
        assert len(seen) == 1, "a 429 must not cost a second call"

    async def test_an_indivisible_prompt_re_raises_instead_of_retrying_the_identical_text(
        self,
    ) -> None:
        """Nothing left to drop ⇒ a retry would send byte-identical text to the provider
        that just rejected it, and pay a call to learn nothing."""
        seen: list[str] = []
        fn = self._fn(seen, fail_first=RuntimeError("prompt is too long"))
        with pytest.raises(RuntimeError, match="too long"):
            await C.complete_with_compaction(
                fn,
                "one indivisible block",
                use_case="reasoning",
                model_resolver=lambda uc: BIG_MODEL,
            )
        assert len(seen) == 1

    async def test_the_summarizer_failing_still_lets_the_call_succeed(self) -> None:
        """End to end through the ladder: the degrade rung means a summarizer outage costs
        fidelity, never the node."""
        seen: list[str] = []

        def broken(middle: list[dict]) -> str:
            raise RuntimeError("summarizer down")

        out = await C.complete_with_compaction(
            self._fn(seen),
            _long_prompt(),
            use_case="reasoning",
            summarize_fn=broken,
            model_resolver=lambda uc: SMALL_MODEL,
        )
        assert out == "ok"
        assert "DROPPED (not summarized)" in seen[0]


class TestAntiThrashing:
    async def test_the_save_history_is_recorded_for_the_caller(self) -> None:
        seen: list[str] = []
        saves: list[float] = []
        await C.complete_with_compaction(
            self._fn(seen),
            _long_prompt(),
            use_case="reasoning",
            saves=saves,
            model_resolver=lambda uc: SMALL_MODEL,
        )
        assert len(saves) == 1 and saves[0] > 0.0

    def _fn(self, seen: list[str]):
        async def fn(prompt, *, use_case="background", output_type=None, model=""):
            seen.append(prompt)
            return "ok"

        return fn

    async def test_two_poor_compactions_stop_the_third(self) -> None:
        """🔴 `should_compact`'s rule, WIRED rather than reimplemented: two consecutive
        compactions that each freed <10% mean compaction is not helping this node, so it
        stops paying a summarizer. The prompt goes out as-is and layer 2 remains the
        backstop if the provider objects."""
        seen: list[str] = []
        prompt = _long_prompt()
        saves = [0.01, 0.02]  # the history the rule reads
        summarized: list[int] = []

        def summarize(middle: list[dict]) -> str:
            summarized.append(1)
            return "S"

        await C.complete_with_compaction(
            self._fn(seen),
            prompt,
            use_case="reasoning",
            saves=saves,
            summarize_fn=summarize,
            model_resolver=lambda uc: SMALL_MODEL,
        )
        assert seen == [prompt], "an over-budget prompt was sent as-is"
        assert summarized == [], "no summarizer call was paid for"
        assert saves == [0.01, 0.02], "and no new entry was recorded"

    async def test_a_good_history_does_not_block_compaction(self) -> None:
        """The complement: the rule must not be a permanent off switch. One poor save does
        not trip it, and a healthy one keeps compaction available."""
        seen: list[str] = []
        saves = [0.01, 0.60]
        await C.complete_with_compaction(
            self._fn(seen),
            _long_prompt(),
            use_case="reasoning",
            saves=saves,
            model_resolver=lambda uc: SMALL_MODEL,
        )
        assert len(seen[0]) < len(_long_prompt())
        assert len(saves) == 3


class TestTheEngineSeam:
    """The ladder is only worth anything if the LLM-backed dispatchers route through it."""

    async def test_infer_sends_a_compacted_prompt(self, monkeypatch) -> None:
        monkeypatch.setattr(C, "prompt_char_budget", lambda *a, **k: 500)
        seen: list[str] = []

        async def fn(prompt, *, use_case="background", output_type=None):
            seen.append(prompt)
            return "answer"

        node = _n({"kind": "infer", "id": "i", "config": {"prompt": _long_prompt()}})
        r = await dispatch_infer(node, BindingContext(), completion=fn)
        assert r.state == InstanceState.DONE
        assert "[CONTEXT COMPACTION — REFERENCE ONLY" in seen[0]

    async def test_infer_survives_a_length_rejection_it_used_to_die_on(self, monkeypatch) -> None:
        """🔴 The atom's reason for existing: before this, a length rejection was a FAILED
        node. Layer 1 is disabled here (a huge budget) so the assertion is specifically
        about layer 2 rescuing the call."""
        monkeypatch.setattr(C, "prompt_char_budget", lambda *a, **k: 10_000_000)
        calls: list[str] = []

        async def fn(prompt, *, use_case="background", output_type=None):
            calls.append(prompt)
            if len(calls) == 1:
                raise RuntimeError("context_length_exceeded")
            return "answer"

        node = _n({"kind": "infer", "id": "i", "config": {"prompt": _long_prompt()}})
        r = await dispatch_infer(node, BindingContext(), completion=fn)
        assert r.state == InstanceState.DONE
        assert r.output == "answer"
        assert len(calls) == 2

    async def test_a_length_rejection_that_compaction_cannot_save_still_fails_typed(
        self, monkeypatch
    ) -> None:
        """The ladder must not swallow a real failure. When the retry also fails, the node
        fails with the SAME typed failure `_classify_exception` always produced."""
        monkeypatch.setattr(C, "prompt_char_budget", lambda *a, **k: 10_000_000)

        async def fn(prompt, *, use_case="background", output_type=None):
            raise RuntimeError("context_length_exceeded")

        node = _n({"kind": "infer", "id": "i", "config": {"prompt": _long_prompt()}})
        r = await dispatch_infer(node, BindingContext(), completion=fn)
        assert r.state == InstanceState.FAILED
        assert r.failure is not None

    async def test_a_judge_gate_routes_through_the_ladder_too(self, monkeypatch) -> None:
        """A judge on a long-horizon loop reads the accumulated evidence, so its
        instruction is the other prompt that grows toward the window."""
        monkeypatch.setattr(C, "prompt_char_budget", lambda *a, **k: 500)
        seen: list[str] = []

        async def fn(prompt, *, use_case="background", output_type=None):
            seen.append(prompt)
            return "PASS"

        node = _n(
            {"kind": "gate", "id": "g", "config": {"kind": "judge", "prompt": _long_prompt()}}
        )
        r = await dispatch_gate(node, BindingContext(), now=0.0, completion=fn)
        # Whatever the gate decides, the PROMPT it sent must have gone through the ladder.
        assert seen, f"the judge never called the model (state={r.state})"
        assert "[CONTEXT COMPACTION — REFERENCE ONLY" in seen[0]


class TestOnARealLongHorizonTemplate:
    """🔴 Driven end to end on a SHIPPED template, which is part of the atom's done-when.

    `audit-sweep` is the long-horizon shape in the bundled library: an `until_dry` loop
    whose body re-runs three `infer` finders, a per-finding refutation `infer` and a
    completeness critic every cycle, each binding `{{inputs.target}}` into its prompt. That
    is exactly the growth pattern the ladder exists for, and it is why a unit test alone is
    not sufficient evidence — the unit tests call the ladder, this proves the ENGINE calls
    it, on a real spec, with a real controller, journal and state on disk.

    The model call is the only fake. Nothing else here is a stub.
    """

    def _spec(self) -> dict:
        raw = json.loads((bundled_root() / "audit-sweep" / "workflow.json").read_text("utf-8"))
        # Macros expanded THEN blocks resolved — the order the bundled provider uses.
        return resolve_spec(expand_spec(raw))

    def _run(self, spec: dict, target: str) -> WorkflowRun:
        run = store.create(
            WorkflowRun(
                id="",
                workflow_name="audit-sweep",
                inputs={"target": target, "focus": "", "fix": False},
            )
        )
        store.write_spec(run.id, spec)
        return run

    def _fn(self, sent: list[str]):
        async def fn(prompt, *, use_case="background", output_type=None, model=""):
            sent.append(prompt)
            # The finders and the critic all want JSON; one shape satisfies every schema
            # in this template, and an empty findings list is what makes the loop go dry.
            return json.dumps({"findings": [], "new_findings": [], "uncovered": []})

        return fn

    async def test_a_long_horizon_run_compacts_its_nodes_and_still_completes_them(
        self, isolated_home, monkeypatch
    ) -> None:
        spec = self._spec()
        # A target big enough to push each finder's bound prompt over the threshold — the
        # same thing accumulated loop context does to a real long-horizon run, arrived at
        # in one step so the test does not need 40 live cycles to reach it.
        target = "\n\n".join(f"module {i}: " + ("y" * 900) for i in range(30))
        monkeypatch.setattr(C, "prompt_char_budget", lambda *a, **k: 5_000)

        sent: list[str] = []
        run = self._run(spec, target)
        c = RunController(run, spec, services=EngineServices(completion=self._fn(sent)))
        status = await c.run_to_completion(timeout=60)

        # 1. The engine really compacted, on the real template's real prompts.
        compacted = [p for p in sent if "[CONTEXT COMPACTION — REFERENCE ONLY" in p]
        assert compacted, "no node's prompt went through the ladder"
        assert len(sent) >= 12, f"the loop did not run its cycles (only {len(sent)} calls)"

        # 2. Every prompt that went out respected the budget. This is the assertion that
        #    would have caught a ladder that ran but did not actually shrink anything.
        assert max(len(p) for p in sent) <= 6_000, "a prompt went out over budget"
        assert len(target) > 20_000, "the pre-compaction target was not actually large"

        # 3. 🔴 The nodes COMPLETED. A compacted prompt that produced no usable output
        #    would be a worse outcome than the context error this replaces.
        for node_id in ("find_correctness", "find_safety", "find_clarity", "completeness_critic"):
            assert node_id in c._outputs, f"{node_id} produced no output"

        # 4. The per-node save history accumulated ACROSS the loop's iterations — which is
        #    only possible because the controller keys it by node id, not by instance path.
        #    Keyed by path, iteration 2 would have started from an empty list every cycle
        #    and the anti-thrashing rule could never observe repetition at all.
        assert len(c._compaction_saves["find_correctness"]) > 1
        assert all(s > 0.5 for s in c._compaction_saves["find_correctness"])

        # 5. Compaction did not change the run's OUTCOME. Asserted against the same
        #    template driven with a small target below, so the bar is "identical to
        #    uncompacted", not a status hardcoded into this test.
        small_run = self._run(spec, "small")
        baseline_sent: list[str] = []
        monkeypatch.setattr(C, "prompt_char_budget", lambda *a, **k: 10_000_000)
        baseline = RunController(
            small_run, spec, services=EngineServices(completion=self._fn(baseline_sent))
        )
        baseline_status = await baseline.run_to_completion(timeout=60)
        assert not [p for p in baseline_sent if "CONTEXT COMPACTION" in p], "baseline compacted"
        assert status == baseline_status
        assert set(c._outputs) == set(baseline._outputs)

    async def test_the_same_run_survives_a_length_rejection_mid_loop(
        self, isolated_home, monkeypatch
    ) -> None:
        """Layer 2, in the engine, on a real template: one finder's call is rejected for
        length, and the node completes anyway instead of failing the run. Layer 1 is out of
        the way (a huge budget) so this is unambiguously the error-triggered rung."""
        monkeypatch.setattr(C, "prompt_char_budget", lambda *a, **k: 10_000_000)
        spec = self._spec()
        rejected: list[int] = []

        async def fn(prompt, *, use_case="background", output_type=None, model=""):
            if not rejected:
                rejected.append(1)
                raise RuntimeError("context_length_exceeded: prompt is too long")
            return json.dumps({"findings": [], "new_findings": [], "uncovered": []})

        target = "\n\n".join(f"module {i}: " + ("y" * 900) for i in range(30))
        run = self._run(spec, target)
        c = RunController(run, spec, services=EngineServices(completion=fn))
        await c.run_to_completion(timeout=60)

        assert rejected, "the rejection never fired — the test proved nothing"
        # The node whose first call was rejected still produced output.
        assert "find_correctness" in c._outputs or "find_safety" in c._outputs
        assert "completeness_critic" in c._outputs, "the loop never reached its critic"

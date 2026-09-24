"""WF2LEA-13: the procedural-memory loop, driven end to end.

M5d shipped half a loop. `MemoryService.record_procedural` had TWO live writers
(`after_turn_review.record_procedural_outcomes` off the dashboard turn path and
`learning/run_end.py` off a workflow's terminal failures) and
`MemoryService.procedural_priors()` had **zero production callers** — a repo-wide grep
returned the definition plus two tests. Every turn paid to capture how-to-work priors
that nothing ever read. `record_procedural`'s contract also declared four outcomes
where only two were ever written.

This suite pins the closed loop, and it drives the WHOLE chain rather than the reader
alone (a reader that renders nothing in practice is the same defect in a new place):

    record_procedural_outcomes   (live writer, dashboard turn path)
      → promote_by_heat          (live promoter, history consolidation tick)
      → procedural_priors        (the surfaceable set)
      → procedural_block         (the ambient block)
      → build_session_context    (the real prompt assembly)

Plus the two contract decisions: `denied` gained a live writer in the native runtime
(and `synthesize_failures` was already READING for it — the worst inert shape, a live
reader of a value nobody wrote), while `corrected` was removed from the vocabulary
because the seam that detects a correction cannot attribute it to a tool.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from personalclaw.learning import ambient
from personalclaw.memory_record import MemoryKind, MemoryScope
from personalclaw.memory_service import (
    PROCEDURAL_FOOTER,
    PROCEDURAL_HEADER,
    PROCEDURAL_OUTCOMES,
    MemoryService,
)
from personalclaw.vector_memory import VectorMemoryStore

#: Reinforcements needed for `promote_by_heat`'s 1.0 threshold. Not a magic number:
#: heat is `0.7·log1p(visits)/ln10 + 0.5·strength`, so a fresh record needs
#: `log1p(visits) ≥ 1.645` → 5 visits. Written as a constant so a kernel change that
#: moves the gate fails HERE with a readable reason instead of in five assertions.
PROMOTING_VISITS = 5


@pytest.fixture
def svc(tmp_path):
    vs = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    vs.init()
    vs.embed_fn = lambda _t: [1.0, 0.0, 0.0]
    return MemoryService.over_vector_store(vs)


def _capture(svc, pairs, *, turns: int = 1) -> int:
    """Capture through the LIVE writer the dashboard turn path calls."""
    from personalclaw import after_turn_review as atr

    total = 0
    for _ in range(turns):
        total += atr.record_procedural_outcomes(svc, pairs)
    return total


# ── the whole chain ───────────────────────────────────────────────────────────


def test_the_whole_loop_capture_promotion_priors_block(svc):
    """Capture → heat promotion → priors → the ambient block, no hand-written rows.

    The promotion leg is `promote_by_heat`, which `history._maybe_consolidate` runs on
    the maintenance cadence — the real production promoter for a memory RECORD. (The
    curator's `TIER_MIGRATION` proposal path promotes learned-library *entities*; the
    accept installer in `dashboard/handlers/learning.py` has no branch for that kind,
    so accepting one would not move a record's scope.)
    """
    assert _capture(svc, [("fs_read", "success")], turns=PROMOTING_VISITS) == PROMOTING_VISITS
    # Still invisible: one session's observation is not a prior.
    assert svc.procedural_priors() == []
    assert svc.procedural_block() == ""

    assert svc.promote_by_heat() >= 1

    priors = svc.procedural_priors()
    assert [p["text"] for p in priors] == ["fs_read on 'fs_read' → success"]
    block = svc.procedural_block()
    assert block.startswith(PROCEDURAL_HEADER)
    assert block.endswith(PROCEDURAL_FOOTER)
    assert "fs_read" in block


def test_the_block_reaches_the_real_session_context(tmp_path):
    """The whole chain again, through `ContextBuilder.build_session_context`.

    The producer call site is what makes the reader live, so it is driven rather than
    asserted: an ambient block nothing renders is the defect this atom closes.
    """
    from personalclaw.context import ContextBuilder
    from personalclaw.memory import MemoryStore
    from personalclaw.memory_service import service_for
    from personalclaw.skills.loader import SkillsLoader

    store = MemoryStore(workspace=tmp_path / "ws")
    vs = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    vs.init()
    vs.embed_fn = lambda _t: [1.0, 0.0, 0.0]
    store.vector_store = vs
    service = service_for(store)

    _capture(service, [("fs_read", "success")], turns=PROMOTING_VISITS)
    assert service.promote_by_heat() >= 1

    builder = ContextBuilder(
        memory=store,
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )
    ctx = builder.build_session_context()
    assert PROCEDURAL_HEADER in ctx
    assert "fs_read on 'fs_read' → success" in ctx


def test_no_priors_renders_no_block(tmp_path):
    """A fresh install renders nothing — not an empty header promising priors."""
    from personalclaw.context import ContextBuilder
    from personalclaw.memory import MemoryStore
    from personalclaw.skills.loader import SkillsLoader

    store = MemoryStore(workspace=tmp_path / "ws")
    vs = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    vs.init()
    vs.embed_fn = lambda _t: [1.0, 0.0, 0.0]
    store.vector_store = vs
    builder = ContextBuilder(
        memory=store,
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )
    assert PROCEDURAL_HEADER not in builder.build_session_context()


# ── anti-noise: raw failures are synthesis INPUT, never priors ────────────────


def test_a_raw_failure_row_is_never_surfaced_even_when_global(svc):
    """`synthesize_failures` exists so this class cannot become a tool-call log.

    Surfacing raw `→ failed` rows would defeat it twice over: below the cluster
    threshold one failure is not evidence, and above it the reader would print the N
    scattered rows beside the single prior that replaces them.
    """
    _capture(svc, [("web_search", "failed")], turns=PROMOTING_VISITS)
    assert svc.promote_by_heat() >= 1  # the row IS global now
    globals_ = [
        r
        for r in svc.get_records(kinds={MemoryKind.PROCEDURAL.value})
        if r.scope == MemoryScope.GLOBAL
    ]
    assert globals_, "the promotion leg must have run for this test to mean anything"
    assert svc.procedural_priors() == []
    assert svc.procedural_block() == ""


def test_the_synthesized_failure_prior_is_what_surfaces(svc):
    """≥N same-root-cause failures collapse into ONE prior — and THAT is surfaceable."""
    for shape in ("read a file", "list a dir", "stat a path"):
        svc.record_procedural(tool="flaky_tool", task_shape=shape, outcome="failed")
    assert svc.procedural_priors() == []  # scattered rows: nothing to say yet

    assert svc.synthesize_failures(min_cluster=3) == 1

    texts = [p["text"] for p in svc.procedural_priors()]
    assert len(texts) == 1
    assert "flaky_tool" in texts[0] and "unreliable" in texts[0]
    assert "prefer an alternative" in svc.procedural_block()


def test_denied_rows_feed_synthesis_and_only_the_collapsed_prior_surfaces(svc):
    """The `denied` writer's payoff: it flows into a reader that already existed.

    `synthesize_failures` has always clustered on `"→ failed" or "→ denied"`, so
    `denied` was a live READER's input that no writer produced. With the runtime now
    labelling refusals, three denials of one tool become one durable prior — and the
    raw denials never reach the prompt on their own.
    """
    for shape in ("write a file", "delete a path", "chmod a path"):
        svc.record_procedural(tool="fs_write", task_shape=shape, outcome="denied")
    assert svc.procedural_priors() == []

    assert svc.synthesize_failures(min_cluster=3) == 1
    assert any("fs_write" in p["text"] for p in svc.procedural_priors())


def test_the_block_is_capped_so_it_cannot_become_a_tool_call_log(svc):
    """The producer caps the block; the allocator never sees an unbounded list."""
    for i in range(30):
        key = svc.record_procedural(tool=f"tool{i}", task_shape="shape", outcome="success")
        svc._vs.db.execute(
            "UPDATE semantic_memory SET scope='global', recall_count=? WHERE key=?", (i + 1, key)
        )
    svc._vs.db.commit()
    assert len(svc.procedural_priors()) == 12  # the priors default
    bullets = [ln for ln in svc.procedural_block().split("\n") if ln.startswith("- ")]
    assert len(bullets) == 5  # the BLOCK default — deliberately smaller


def test_an_environment_failure_claim_never_becomes_durable_guidance(svc):
    """The write-side guardrail, enforced on the read side too.

    `record_procedural` accepts a `detail` that lands in the record text, so a world
    condition ("connection refused") is a reachable prior text — and a promoted one
    would be durable guidance telling the agent a working tool does not work.
    """
    key = svc.record_procedural(
        tool="fetch_url",
        task_shape="fetch a page",
        outcome="success",
        detail="connection refused on the first attempt",
    )
    svc._vs.db.execute(
        "UPDATE semantic_memory SET scope='global', recall_count=9 WHERE key=?", (key,)
    )
    svc._vs.db.commit()
    assert svc.get_record(key).scope == MemoryScope.GLOBAL
    assert svc.procedural_priors() == []


# ── the closed outcome vocabulary ─────────────────────────────────────────────


def test_the_outcome_vocabulary_is_closed(svc):
    """An unknown outcome RAISES — a row no surfacing rule maps is a row nothing reads."""
    with pytest.raises(ValueError):
        svc.record_procedural(tool="x", task_shape="y", outcome="corrected")
    with pytest.raises(ValueError):
        svc.record_procedural(tool="x", task_shape="y", outcome="")
    assert PROCEDURAL_OUTCOMES == frozenset({"success", "failed", "denied"})
    assert "corrected" not in PROCEDURAL_OUTCOMES


def test_every_declared_outcome_has_a_surfacing_decision(svc):
    """Exhaustive over `PROCEDURAL_OUTCOMES` — no default branch decides for a member.

    A fourth outcome added without a surfacing rule fails here rather than inheriting
    "not surfaced" silently, which is how `corrected` survived as a documented value
    for a whole program.
    """
    expected = {"success": True, "failed": False, "denied": False}
    assert set(expected) == set(PROCEDURAL_OUTCOMES), "a new outcome needs a decision here"
    for outcome, surfaceable in expected.items():
        key = svc.record_procedural(tool=f"t_{outcome}", task_shape="shape", outcome=outcome)
        svc._vs.db.execute(
            "UPDATE semantic_memory SET scope='global', recall_count=9 WHERE key=?", (key,)
        )
        svc._vs.db.commit()
        keys = {p["key"] for p in svc.procedural_priors()}
        assert (key in keys) is surfaceable, f"{outcome!r} surfaced: {key in keys}"


def test_the_unknown_outcome_is_dropped_at_the_capture_seam(svc, caplog):
    """The drain is a boundary: a bad label is logged and skipped, never stored."""
    from personalclaw import after_turn_review as atr

    with caplog.at_level("WARNING", logger="personalclaw.after_turn_review"):
        assert atr.record_procedural_outcomes(svc, [("x", "corrected")]) == 0
    assert any("unknown outcome" in r.getMessage() for r in caplog.records)
    assert svc.get_records(kinds={MemoryKind.PROCEDURAL.value}) == []


# ── `denied` has a live writer: the native runtime ────────────────────────────


def test_classify_denial_is_recognisable_for_every_declared_deny_kind():
    """One author of the wording, one recogniser — over the CLOSED kind set.

    Enumerated from the module's own `DENY_KIND_*` constants, so adding a kind (or
    rewording a branch out of `_DENIAL_FRAGMENTS`) reds here instead of silently
    relabelling that denial as a tool failure.
    """
    from personalclaw import security

    kinds = [getattr(security, n) for n in dir(security) if n.startswith("DENY_KIND_")]
    assert len(kinds) >= 5
    for kind in kinds:
        _recoverable, observation = security.classify_denial(kind, "because", "some_tool")
        assert security.is_denial_observation(observation), kind
    # A genuine tool failure is NOT a denial.
    assert not security.is_denial_observation("Error: file not found or access denied: /x")
    assert not security.is_denial_observation("")


@pytest.mark.asyncio
async def test_the_runtime_labels_denied_failed_and_success_distinctly():
    """Driven through the real `NativeAgentRuntime` tool loop, not a stub.

    A denial arrives at the model as `Error: …` exactly like a failure, and labelling
    it `failed` is what taught failure-synthesis to publish "this tool is unreliable —
    prefer an alternative" about a tool that works fine and is merely not allowed.
    """
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.llm.events import (
        EVENT_COMPLETE,
        EVENT_TEXT_CHUNK,
        EVENT_TOOL_CALL,
        AgentEvent,
    )
    from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

    class _Model:
        supports_tools = True
        _model = "scripted"

        def __init__(self):
            self.calls = 0

        async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
            self.calls += 1
            if self.calls == 1:
                yield AgentEvent(
                    kind=EVENT_TOOL_CALL, tool_call_id="c1", title="echo", tool_input="{}"
                )
                yield AgentEvent(kind=EVENT_COMPLETE)
            else:
                yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok")
                yield AgentEvent(kind=EVENT_COMPLETE)

    class _Tool(ToolProvider):
        def __init__(self, ok=True):
            self._ok = ok

        @property
        def name(self):
            return "mock"

        @property
        def display_name(self):
            return "Mock"

        async def list_tools(self):
            return [
                ToolDefinition(
                    name="echo",
                    description="d",
                    parameters={"type": "object"},
                    requires_approval=False,
                )
            ]

        async def invoke(self, tool_name, arguments):
            if self._ok:
                return ToolResult(success=True, output="OUT")
            return ToolResult(success=False, output="", error="the tool itself blew up")

    async def _outcomes(*, tool, **kw):
        rt = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
            model_provider=_Model(),
            tool_providers=[tool],
            **kw,
        )
        await rt.start()
        async for _ev in rt.stream("go"):
            pass
        return rt.drain_tool_outcomes()

    assert await _outcomes(tool=_Tool()) == [("echo", "success")]
    assert await _outcomes(tool=_Tool(ok=False)) == [("echo", "failed")]
    # The deny-list is one of five denial paths; all five return an observation from
    # `security.classify_denial`, which is why the runtime keys on that one function.
    assert await _outcomes(tool=_Tool(), extra_deny_patterns=["echo"]) == [("echo", "denied")]


def test_the_drained_outcome_reaches_procedural_memory_as_denied(svc):
    """The writer→store leg: a drained `denied` pair is stored as a denial."""
    assert _capture(svc, [("fs_write", "denied")]) == 1
    texts = [r.text for r in svc.get_records(kinds={MemoryKind.PROCEDURAL.value})]
    assert texts == ["fs_write on 'fs_write' → denied"]


def test_corrected_has_no_writer_anywhere_in_src():
    """`corrected` is gone from the contract, not merely unused.

    It was dropped rather than wired because the seam that DETECTS a correction
    cannot attribute it to a tool: `after_turn_review`'s `correction` flag is this
    turn's user message reacting to the PREVIOUS turn's work, and nothing carries the
    previous turn's tool set forward — so any writer here would blame whichever tools
    happened to run this turn. A wrong prior is worse than a missing one.
    """
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"
    offenders = []
    for path in src.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if 'outcome="corrected"' in line or "outcome='corrected'" in line:
                offenders.append(f"{path.name}: {line.strip()}")
    assert offenders == []


# ── the ambient budget: it COMPETES, it does not append ───────────────────────


def test_procedural_maps_onto_the_existing_lesson_kind():
    """No sixth kind, so no sixth slot — the block joins the pool that exists."""
    assert ambient.SLOT_KINDS["procedural"] == "lesson"
    assert ambient.SLOT_KINDS["procedural"] == ambient.SLOT_KINDS["lessons"]
    # The allocator's declared kinds, unchanged by this block.
    assert set(ambient.SLOT_KINDS.values()) == {"lesson", "skill", "template", "memory"}


def test_the_block_competes_inside_the_one_budget():
    """Adding priors can never push the render past the configured budget.

    The measurement that matters: at a budget too small to hold both, the render with
    priors uses no more tokens than the budget allows — the block competed for room
    rather than being appended beside the allocation.
    """
    lessons = "[Learned corrections — ALWAYS follow these]\n" + "\n".join(
        f"- always prefer approach number {i} when refactoring a module" for i in range(12)
    )
    priors = f"{PROCEDURAL_HEADER}\n" + "\n".join(
        f"- tool{i} on 'shape {i}' → success" for i in range(5)
    )
    for budget in (20, 40, 60, 90, 120, 200, 400, 1000, 4000):
        with_priors = ambient.render(lessons=lessons, procedural=priors, budget_tokens=budget)
        assert with_priors.used_tokens <= budget, budget
        assert with_priors.budget_tokens == budget
    # At a generous budget the priors are IN the one allocation, not a second block.
    generous = ambient.render(lessons=lessons, procedural=priors, budget_tokens=4000)
    assert PROCEDURAL_HEADER in generous.text
    assert any(key == "procedural" for _kind, key, _tier in generous.included)


def test_a_stored_lesson_is_never_crowded_out_by_a_prior():
    """The rail that shares the lesson slot must not be satisfied BY the newcomer.

    `render`'s "nothing may crowd out a lesson" retry checks the allocation for a
    surviving lesson. Once the procedural block entered as kind `lesson`, a check on
    kind alone would read a surviving prior block as "a lesson survived" and never
    retry — the rail would read green while doing nothing.
    """
    lessons = "[Learned corrections — ALWAYS follow these]\n- never force-push to main"
    priors = f"{PROCEDURAL_HEADER}\n" + "\n".join(
        f"- tool{i} on 'a fairly long task shape {i}' → success" for i in range(5)
    )
    for budget in (40, 60, 90, 150, 400):
        alloc = ambient.render(lessons=lessons, procedural=priors, budget_tokens=budget)
        if not alloc.included:
            continue
        kept_lesson = ambient._kept_a_lesson(alloc)
        kept_prior = any(key == "procedural" for _kind, key, _tier in alloc.included)
        assert (
            kept_lesson or not kept_prior
        ), f"at budget {budget} a prior survived while the user's lesson did not: {alloc.text!r}"


def test_only_a_prior_surviving_does_not_earn_the_lesson_header():
    """The case that makes the key-based check load-bearing rather than tidy.

    Measured: one 100-token lesson plus a short prior block at a 60-token budget —
    the lesson is skipped as oversized and only the priors survive. Under a check on
    KIND alone that allocation reads as "a lesson survived", so `frame` would print
    "[Learned corrections — ALWAYS follow these]" above a block containing no
    corrections at all: a header asserting rules the model then cannot find, which is
    exactly what that rail exists to prevent.
    """
    long_lesson = "- " + ("always prefer the careful approach when refactoring a module " * 12)
    lessons = "[Learned corrections — ALWAYS follow these]\n" + long_lesson
    priors = f"{PROCEDURAL_HEADER}\n- fs_read on 'read a file' → success\n{PROCEDURAL_FOOTER}"
    alloc = ambient.render(lessons=lessons, procedural=priors, budget_tokens=60)
    assert [key for _kind, key, _tier in alloc.included] == ["procedural"], "premise"
    assert ambient._kept_a_lesson(alloc) is False
    text = ambient.frame(alloc, lessons_block=lessons)
    assert ambient.lesson_header(lessons) not in text
    assert PROCEDURAL_HEADER in text


def test_the_lesson_header_still_frames_the_lessons_when_a_prior_ranks_above_them():
    """`frame` inserts the lesson header before the lessons chunk, not after everything.

    The allocator renders a whole slot as ONE chunk, so a prior block ranking above
    the second lesson makes the chunk OPEN with the priors' header. Keying the
    insertion point on the chunk's first line sent the header to the append-at-the-end
    fallback — an authority statement printed after the rules it governs.
    """
    lessons = "[Learned corrections — ALWAYS follow these]\n- never force-push to main"
    priors = f"{PROCEDURAL_HEADER}\n- fs_read on 'read a file' → success\n{PROCEDURAL_FOOTER}"
    alloc = ambient.render(lessons=lessons, procedural=priors, budget_tokens=4000)
    text = ambient.frame(alloc, lessons_block=lessons)
    header = ambient.lesson_header(lessons)
    assert header in text
    assert text.index(header) < text.index("- never force-push to main")


def test_the_priors_block_is_explicitly_closed():
    """A footer, like `[End of skills]` — the block can land mid-chunk."""
    lessons = "[Learned corrections — ALWAYS follow these]\n- never force-push to main"
    priors = f"{PROCEDURAL_HEADER}\n- fs_read on 'read a file' → success\n{PROCEDURAL_FOOTER}"
    text = ambient.render(lessons=lessons, procedural=priors, budget_tokens=4000).text
    assert PROCEDURAL_FOOTER in text


def test_the_measurement_sweep_sees_the_same_pool_as_the_render():
    """`sources_for` is what the ablation sweep measures; a block missing there would
    make the sweep report on a different, drifting assembly."""
    sources = ambient.sources_for(procedural=f"{PROCEDURAL_HEADER}\n- t on 's' → success")
    assert "procedural" in sources
    assert [c.key for c in sources["procedural"]] == ["procedural"]


def test_context_render_ambient_passes_the_block_through():
    """The call site, structurally: `build_session_context` must produce the block and
    hand it to `_render_ambient` — a producer that computes a block and drops it is the
    exact shape this atom exists to close."""
    from personalclaw import context as ctx_mod

    tree = ast.parse(pathlib.Path(ctx_mod.__file__).read_text(encoding="utf-8"))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "build_session_context"
    )
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    assert any(
        isinstance(c.func, ast.Attribute) and c.func.attr == "procedural_block" for c in calls
    ), "build_session_context must call MemoryService.procedural_block()"
    render = next(
        c for c in calls if isinstance(c.func, ast.Name) and c.func.id == "_render_ambient"
    )
    assert "procedural" in [kw.arg for kw in render.keywords]


# ── surfacing-rank doctrine (WF2LEA-9) ───────────────────────────────────────


def test_heat_may_rank_priors_but_strength_alone_cannot_win(svc):
    """`decay.py`'s doctrine, asserted through the BLOCK this atom added.

    Heat is admissible as rank because it weights usage ABOVE recency: recency may
    break a tie, never create one. So a decayed prior that has been used outranks a
    pristine one that has not — and the kernel's prune/review verdict is not consulted
    at all.
    """
    from datetime import datetime, timedelta, timezone

    used = svc.record_procedural(tool="used_tool", task_shape="shape", outcome="success")
    fresh = svc.record_procedural(tool="fresh_tool", task_shape="shape", outcome="success")
    # `used` is 400 days idle but heavily used; `fresh` is brand new and used once.
    # `updated_at` is the semantic table's recency stamp (it has no last_accessed_at).
    stale = (datetime.now(tz=timezone.utc) - timedelta(days=400)).isoformat()
    svc._vs.db.execute(
        "UPDATE semantic_memory SET scope='global', recall_count=9, updated_at=? WHERE key=?",
        (stale, used),
    )
    svc._vs.db.execute(
        "UPDATE semantic_memory SET scope='global', recall_count=0 WHERE key=?", (fresh,)
    )
    svc._vs.db.commit()

    order = [p["key"] for p in svc.procedural_priors()]
    assert order.index(used) < order.index(fresh)
    lines = [ln for ln in svc.procedural_block().split("\n") if ln.startswith("- ")]
    assert "used_tool" in lines[0]


def test_the_reader_never_imports_the_eviction_verdict():
    """A source-level rail: the ranking path may use `strength`, never `DecayVerdict`."""
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"
    text = (src / "memory_service.py").read_text(encoding="utf-8")
    assert "DecayVerdict" not in text

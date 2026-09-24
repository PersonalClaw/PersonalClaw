"""Skill bodies allocate on the one prompt budget (CONTEXT-ECONOMY CE2-9).

The clause this file exists for, verbatim: *"A test drives a deliberately oversized skill
against a small budget and asserts the conversation survives with the skill reduced — not
that the allocator was called."* So the anchor test below drives the REAL
``ContextBuilder.build_message`` path with a 42,000-token skill and asserts properties of
the assembled prompt: the user's request is in it, the skill's steps are not, the skill's
declared summary is, and the total is small. Nothing here asserts that ``allocate`` was
invoked — a mechanism can be invoked and ignored.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.context import ContextBuilder
from personalclaw.learning.surfacing import Candidate, Tier, allocate, count_tokens
from personalclaw.memory import MemoryStore
from personalclaw.skills.allocation import (
    CONTEXT_TIERS,
    SkillDecision,
    SkillLoadState,
    SkillRequest,
    allocate_skills,
    resolve_tier,
)
from personalclaw.skills.loader import SkillsLoader

# A body big enough that no realistic budget could hold it: ~42,000 tokens.
_HUGE_BODY = "\n".join(
    f"Step {i}. Do the {i}th thing very carefully and at length, describing every nuance."
    for i in range(1, 2001)
)
_HUGE_FIRST_LINE = "Step 1. Do the 1th thing very carefully"
_HUGE_LAST_LINE = "Step 2000. Do the 2000th thing"


def _write_skill(base: Path, name: str, frontmatter: str, body: str) -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")
    return skill_dir


@pytest.fixture(autouse=True)
def _usage_store_stays_in_tmp(tmp_path, monkeypatch):
    """The turn-time use counter writes beside the skills dir — keep it in tmp.

    `SkillUsageStore()` resolves its own path from `skills_dir()`, so an unpatched run
    would record uses into whatever home the process resolves. The suite's real-home rail
    already redirects that, but a test that exercises the use counter should not depend on
    another fixture for its isolation.
    """
    counter_home = tmp_path / "usage-home"
    counter_home.mkdir()
    monkeypatch.setattr("personalclaw.skills.usage.skills_dir", lambda: counter_home)


def _builder(tmp_path: Path) -> ContextBuilder:
    return ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )


def _loader(tmp_path: Path) -> SkillsLoader:
    return SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False)


def _request(tmp_path: Path, name: str, *, score: float = 0.9, forced: bool = False):
    content = (tmp_path / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    return SkillRequest(name=name, content=content, score=score, forced=forced)


def _by_name(decisions: list[SkillDecision]) -> dict[str, SkillDecision]:
    return {d.name: d for d in decisions}


# ── The clause's own test ──


class TestTheConversationSurvivesAnOversizedSkill:
    def test_an_oversized_skill_loads_reduced_and_the_conversation_survives(self, tmp_path):
        """The anchor. A 42k-token skill must not be able to take the turn.

        Asserted on the assembled PROMPT, not on the allocator's call log:
          * the user's request is still in it (the conversation survived),
          * the skill's steps are not (the body did not take the window),
          * the skill's declared summary IS (it loaded, reduced, not dropped),
          * and the whole prompt is smaller than the skill body alone was.
        """
        skills = tmp_path / "skills"
        _write_skill(
            skills,
            "monster",
            "name: monster\ndescription: How to tame a monster in three moves.\n"
            "resources:\n  - path: reference/moves.md\n    description: the move list",
            "# Monster\n" + _HUGE_BODY,
        )
        (skills / "monster" / "reference").mkdir()
        (skills / "monster" / "reference" / "moves.md").write_text("moves", encoding="utf-8")

        notices: list[str] = []
        decisions: list[SkillDecision] = []
        msg, _ = _builder(tmp_path).build_message(
            "please handle the monster",
            is_new_session=True,
            agent="loop-worker",
            force_skill_ids=["monster"],
            notices_out=notices,
            skill_decisions_out=decisions,
        )

        body_tokens = count_tokens(_HUGE_BODY)
        assert body_tokens > 40_000, "fixture must be genuinely oversized"

        # The conversation survived.
        assert "please handle the monster" in msg
        # The body did not reach the prompt — not its tail, and not its head either.
        assert _HUGE_LAST_LINE not in msg
        assert _HUGE_FIRST_LINE not in msg
        # …and the skill still loaded, in its declared form.
        assert "How to tame a monster in three moves." in msg
        assert "reference/moves.md" in msg
        # The whole prompt now costs a fraction of what this ONE skill used to.
        assert count_tokens(msg) < body_tokens // 10

        decision = _by_name(decisions)["monster"]
        assert decision.state is SkillLoadState.REDUCED
        assert decision.body_tokens > 40_000
        assert decision.loaded_tokens < 200
        assert notices and "monster" in notices[0]

    def test_a_reduced_skill_carries_its_declared_summary_not_a_slice_of_its_body(self, tmp_path):
        """REDUCED means the DECLARED summary. A byte-boundary cut is a different thing.

        This is the assertion that separates "shorter" from "reduced": a truncating
        implementation would leave the body's opening lines in the prompt, and every
        `Step N.` line here is a prefix a naive cut would keep.
        """
        skills = tmp_path / "skills"
        _write_skill(
            skills,
            "monster",
            "name: monster\ndescription: Tame it in three moves.",
            "# Monster\n" + _HUGE_BODY,
        )
        alloc = allocate_skills(_loader(tmp_path), [_request(tmp_path, "monster")])
        block = dict(alloc.blocks)["monster"]

        assert "Tame it in three moves." in block
        # No part of the body survives — not one of the 2,000 steps.
        assert "Step " not in block
        # And the block is a COMPLETE declared form: it closes, and it says what to call.
        assert block.startswith("[Skill: monster — REDUCED")
        assert block.rstrip().endswith("[End of skill]")
        assert "skill_invoke{monster}" in block


# ── The three states, closed ──


class TestTheDecisionIsObservablePerTurn:
    def _three_skills(self, tmp_path):
        skills = tmp_path / "skills"
        _write_skill(skills, "small", "name: small\ndescription: a small skill", "# Small\nrule.")
        _write_skill(
            skills,
            "big-with-summary",
            "name: big-with-summary\ndescription: a big skill that says what it is",
            "# Big\n" + _HUGE_BODY,
        )
        # No `description`, no `resources:` → nothing declared to reduce TO.
        _write_skill(skills, "big-bare", "name: big-bare", "# Bare\n" + _HUGE_BODY)
        return allocate_skills(
            _loader(tmp_path),
            [
                _request(tmp_path, "small"),
                _request(tmp_path, "big-with-summary"),
                _request(tmp_path, "big-bare"),
            ],
        )

    def test_one_turn_can_produce_all_three_states_and_they_are_distinct(self, tmp_path):
        """Three skills, three DIFFERENT verdicts, in one turn.

        Collapsing REDUCED into REFUSED (or admitted into reduced) makes this red: the
        three states would no longer be three, and "why did my skill not take effect"
        would stop having a per-skill answer.
        """
        alloc = self._three_skills(tmp_path)
        got = {name: d.state for name, d in _by_name(alloc.decisions).items()}
        assert got == {
            "small": SkillLoadState.ADMITTED,
            "big-with-summary": SkillLoadState.REDUCED,
            "big-bare": SkillLoadState.REFUSED,
        }
        assert len({s for s in got.values()}) == 3

    def test_the_state_set_is_closed_at_three(self, tmp_path):
        assert [s.value for s in SkillLoadState] == ["admitted", "reduced", "refused"]
        alloc = self._three_skills(tmp_path)
        assert all(d.state in set(SkillLoadState) for d in alloc.decisions)

    def test_the_per_turn_report_counts_every_state_including_the_empty_ones(self, tmp_path):
        alloc = self._three_skills(tmp_path)
        assert alloc.counts == {"admitted": 1, "reduced": 1, "refused": 1}
        assert alloc.summary.startswith("1 admitted, 1 reduced, 1 refused — ")
        # A turn with no skill at all reports the triple at zero rather than nothing.
        empty = allocate_skills(_loader(tmp_path), [])
        assert empty.counts == {"admitted": 0, "reduced": 0, "refused": 0}

    def test_a_skill_with_no_declared_summary_is_refused_and_says_why(self, tmp_path):
        """No `description` and no `resources:` → there is nothing to reduce to.

        The alternative — synthesizing a summary from the body's first N chars — is the
        byte-boundary cut the clause rules out, so this refuses and names the reason. What
        reaches the prompt is a POINTER, never the skill's content.
        """
        alloc = self._three_skills(tmp_path)
        decision = _by_name(alloc.decisions)["big-bare"]
        assert decision.state is SkillLoadState.REFUSED
        assert "no summary to reduce to" in decision.reason
        block = dict(alloc.blocks)["big-bare"]
        assert "MATCHED but NOT LOADED" in block
        assert "Step " not in block

    def test_the_decisions_ride_out_in_the_assembled_metadata(self, tmp_path):
        """`build_message`'s structured answer reaches the seam, not only the log."""
        from personalclaw.context_engine import DefaultContextEngine

        skills = tmp_path / "skills"
        _write_skill(skills, "monster", "name: monster\ndescription: tame it", "# M\n" + _HUGE_BODY)
        assembled = DefaultContextEngine().assemble(
            _builder(tmp_path),
            "tame the monster",
            is_new_session=True,
            agent="loop-worker",
            force_skill_ids=["monster"],
            active_recall=False,
        )
        rows = assembled.metadata["skill_decisions"]
        assert [r["name"] for r in rows] == ["monster"]
        assert rows[0]["state"] == "reduced"
        assert rows[0]["cap_tokens"] == CONTEXT_TIERS["standard"]
        # And the same fact is on the notice channel CE2-8 already broadcasts.
        assert any("monster" in n for n in assembled.notices)


# ── Visibility ──


class TestTheReductionIsVisible:
    def test_the_notice_names_the_skill_and_the_reason(self, tmp_path):
        """A silent reduction is indistinguishable from a wrong answer.

        Making the reduction silent (dropping the notice, or emitting a generic
        "context trimmed") makes this red.
        """
        skills = tmp_path / "skills"
        _write_skill(
            skills,
            "monster",
            "name: monster\ndescription: tame it",
            "# M\n" + _HUGE_BODY,
        )
        notices: list[str] = []
        _builder(tmp_path).build_message(
            "tame it",
            is_new_session=True,
            agent="loop-worker",
            force_skill_ids=["monster"],
            notices_out=notices,
        )
        assert len(notices) == 1
        notice = notices[0]
        assert '"monster"' in notice  # WHICH skill
        assert "REDUCED" in notice  # what happened
        assert "context tier declares" in notice  # WHY
        assert "3,000-token cap" in notice  # against which number
        assert "skill_invoke{monster}" in notice  # and what to do about it

    def test_an_admitted_skill_says_nothing(self, tmp_path):
        """A healthy turn stays silent — the same rule CE2-8's notice channel follows."""
        skills = tmp_path / "skills"
        _write_skill(skills, "tiny", "name: tiny\ndescription: d", "# T\nrule.")
        notices: list[str] = []
        _builder(tmp_path).build_message(
            "hello",
            is_new_session=True,
            agent="loop-worker",
            force_skill_ids=["tiny"],
            notices_out=notices,
        )
        assert notices == []


# ── The two caps, one mechanism ──


class TestTheCapsArePerSkillAndInAggregate:
    def test_a_declared_heavy_tier_admits_what_standard_would_reduce(self, tmp_path):
        """The tier is the skill's own DECLARATION, and it changes the outcome."""
        body = "# Mid\n" + "\n".join(f"line {i} of a fairly long skill body" for i in range(1, 501))
        assert CONTEXT_TIERS["standard"] < count_tokens(body) < CONTEXT_TIERS["heavy"]

        skills = tmp_path / "skills"
        _write_skill(skills, "undeclared", "name: undeclared\ndescription: d", body)
        _write_skill(
            skills, "declared", "name: declared\ndescription: d\ncontext_tier: heavy", body
        )
        alloc = allocate_skills(
            _loader(tmp_path),
            [_request(tmp_path, "undeclared"), _request(tmp_path, "declared")],
        )
        states = {n: d.state for n, d in _by_name(alloc.decisions).items()}
        assert states["undeclared"] is SkillLoadState.REDUCED
        assert states["declared"] is SkillLoadState.ADMITTED
        assert _by_name(alloc.decisions)["declared"].cap_tokens == CONTEXT_TIERS["heavy"]

    def test_the_per_skill_cap_binds_even_with_the_whole_budget_free(self, tmp_path):
        """A skill's own ceiling is not "whatever is left". It is what it declared.

        Driven with an aggregate far larger than the body: only the declared cap can
        explain the reduction, so a per-skill cap that was really just the budget shows up
        here as an admission.
        """
        skills = tmp_path / "skills"
        _write_skill(skills, "monster", "name: monster\ndescription: tame it", "# M\n" + _HUGE_BODY)
        alloc = allocate_skills(
            _loader(tmp_path), [_request(tmp_path, "monster")], budget_tokens=500_000
        )
        assert _by_name(alloc.decisions)["monster"].state is SkillLoadState.REDUCED

    def test_the_aggregate_cap_binds_across_skills_that_each_fit_alone(self, tmp_path):
        """Each skill under its own cap, all of them together over the aggregate."""
        body = "# S\n" + "\n".join(f"line {i} of a moderately long body" for i in range(1, 300))
        per = count_tokens(body)
        assert per < CONTEXT_TIERS["standard"]

        skills = tmp_path / "skills"
        names = [f"s{i}" for i in range(4)]
        for name in names:
            _write_skill(skills, name, f"name: {name}\ndescription: summary of {name}", body)
        alloc = allocate_skills(
            _loader(tmp_path),
            [_request(tmp_path, n) for n in names],
            # Room for two bodies plus the reduced forms of the other two, not four bodies.
            budget_tokens=per * 2 + 200,
        )
        counts = alloc.counts
        assert counts["admitted"] == 2, alloc.summary
        assert counts["reduced"] == 2, alloc.summary
        assert alloc.used_tokens <= per * 2 + 200
        # The reduced ones must blame the AGGREGATE, not their own tier — they fit it.
        for decision in alloc.decisions:
            if decision.state is SkillLoadState.REDUCED:
                assert "aggregate budget" in decision.reason

    def test_skill_bodies_are_not_rationed_by_the_diversification_quota(self, tmp_path):
        """More than three small skills all load. A per-source quota of 3 would drop one.

        `MAX_PER_SOURCE` runs inside `fuse`, BEFORE the allocator can report a near-miss,
        so a quota here would be a silent drop with budget to spare — exactly the failure
        this atom removes.
        """
        skills = tmp_path / "skills"
        names = [f"tiny{i}" for i in range(6)]
        for name in names:
            _write_skill(skills, name, f"name: {name}\ndescription: d{name}", f"# {name}\nrule.")
        alloc = allocate_skills(_loader(tmp_path), [_request(tmp_path, n) for n in names])
        assert alloc.counts == {"admitted": 6, "reduced": 0, "refused": 0}

    def test_an_unknown_or_absent_tier_fails_open_to_standard(self, tmp_path):
        assert resolve_tier("") == "standard"
        assert resolve_tier("  HEAVY ") == "heavy"
        assert resolve_tier("enormous", name="x") == "standard"

    def test_every_bundled_skill_fits_the_cap_its_declared_tier_grants(self):
        """A ratchet on the shipped library: no bundled skill loads reduced by default.

        A skill that outgrows its declared tier must either shrink or declare a bigger
        tier; discovering it as a silent reduction in a user's turn is the wrong place.
        """
        from personalclaw.skills.allocation import full_block
        from personalclaw.skills.loader import parse_frontmatter

        bundled = Path(__file__).resolve().parents[1] / "src/personalclaw/skills/bundled"
        over: list[str] = []
        for path in sorted(bundled.rglob("SKILL.md")):
            raw = path.read_text(encoding="utf-8")
            tier = resolve_tier(parse_frontmatter(raw).get("context_tier", ""))
            body = SkillsLoader.strip_frontmatter(raw).strip()
            tokens = count_tokens(full_block(path.parent.name, body))
            if tokens > CONTEXT_TIERS[tier]:
                over.append(f"{path.parent.name}: {tokens} tokens > {tier} cap")
        assert not over, over
        assert len(list(bundled.rglob("SKILL.md"))) >= 15, "vacuity floor: skills were found"


# ── Continued cost ──


class TestContinuedCostIsReEvaluated:
    def test_a_skill_admitted_on_one_turn_is_re_fitted_on_the_next(self, tmp_path, monkeypatch):
        """Nothing caches an admission. The allocation is recomputed every turn.

        The skill file does not change; only the room does. An implementation that
        remembered "monster was admitted" would keep injecting the full body.
        """
        body = "# S\n" + "\n".join(f"line {i} of a moderately long body" for i in range(1, 300))
        skills = tmp_path / "skills"
        _write_skill(skills, "midsize", "name: midsize\ndescription: a summary", body)
        builder = _builder(tmp_path)

        first: list[SkillDecision] = []
        msg1, _ = builder.build_message(
            "turn one",
            is_new_session=True,
            agent="loop-worker",
            force_skill_ids=["midsize"],
            skill_decisions_out=first,
        )
        assert _by_name(first)["midsize"].state is SkillLoadState.ADMITTED
        assert "line 299 of a moderately long body" in msg1

        monkeypatch.setattr("personalclaw.skills.allocation.AGGREGATE_CAP_TOKENS", 200)
        second: list[SkillDecision] = []
        msg2, _ = builder.build_message(
            "turn two",
            is_new_session=False,
            agent="loop-worker",
            force_skill_ids=["midsize"],
            skill_decisions_out=second,
        )
        assert _by_name(second)["midsize"].state is SkillLoadState.REDUCED
        assert "line 299 of a moderately long body" not in msg2
        assert "a summary" in msg2

    def test_a_reduced_skill_counts_as_used_and_a_refused_one_does_not(self, tmp_path):
        """The use counter follows what reached the prompt, not what matched.

        Crediting a use to a skill the agent never saw would train the surfacing ranker on
        the allocator's refusals.
        """
        skills = tmp_path / "skills"
        _write_skill(skills, "small", "name: small\ndescription: d", "# S\nrule.")
        _write_skill(
            skills, "big-with-summary", "name: big-with-summary\ndescription: d", _HUGE_BODY
        )
        _write_skill(skills, "big-bare", "name: big-bare", _HUGE_BODY)
        alloc = allocate_skills(
            _loader(tmp_path),
            [
                _request(tmp_path, "small"),
                _request(tmp_path, "big-with-summary"),
                _request(tmp_path, "big-bare"),
            ],
        )
        assert sorted(alloc.loaded) == ["big-with-summary", "small"]


# ── The allocator itself ──


class TestTheAllocatorHonorsTheDeclaredCap:
    def test_a_declared_per_candidate_cap_degrades_the_tier(self):
        """`Candidate.max_tokens` is enforced inside `allocate`, not by a pre-filter."""
        cand = Candidate(
            kind="lesson",
            key="k",
            score=1.0,
            l0="short",
            l1="a medium rendering",
            l2="x " * 400,
            max_tokens=50,
        )
        alloc = allocate({"s": [cand]}, budget_tokens=100_000, include_preamble=False)
        assert cand.tier is not Tier.L2
        assert "a medium rendering" in alloc.text
        assert alloc.degraded == ["k"]

    def test_an_uncapped_candidate_is_unaffected(self):
        cand = Candidate(kind="lesson", key="k", score=1.0, l0="short", l1="mid", l2="full body")
        alloc = allocate({"s": [cand]}, budget_tokens=100_000, include_preamble=False)
        assert "full body" in alloc.text
        assert alloc.degraded == []

    def test_a_tier_that_renders_empty_is_not_a_fit(self):
        """An empty rendering costs 0 tokens and would "fit" — and load nothing.

        Without the guard the slot gains a blank block that reads like a loaded item while
        carrying no content, which is a silent drop wearing an admission's clothes.
        """
        cand = Candidate(kind="lesson", key="k", score=1.0, l0="", l1="", l2="x " * 400)
        alloc = allocate({"s": [cand]}, budget_tokens=10, include_preamble=False)
        assert alloc.text == ""
        assert alloc.skipped_oversized == ["k"]
        assert alloc.included == []


# ── Compatibility with the shape the prompt had before ──


class TestAnAdmittedSkillIsUnchanged:
    def test_the_admitted_block_is_byte_identical_to_the_old_concatenation(self, tmp_path):
        skills = tmp_path / "skills"
        _write_skill(skills, "tiny", "name: tiny\ndescription: d", "# T\nUse token buckets.")
        msg, _ = _builder(tmp_path).build_message(
            "next cycle",
            is_new_session=True,
            agent="loop-worker",
            force_skill_ids=["tiny"],
        )
        assert "[Skill: tiny]\n# T\nUse token buckets.\n[End of skill]\n\n" in msg

    def test_a_forced_skill_outranks_a_surfaced_one_at_the_same_overlap(self, tmp_path):
        """Priority, not append order, decides who gets the room.

        Two identical bodies, one forced and one surfaced, and only room for one: the
        confirmed skill wins. Before CE2-9 the winner was whichever `parts.add` ran first.

        MEASURED while writing this: passing them in this order originally gave the room to
        `guessed`, because `fuse` stamps `source_rank` from list position and salience
        decays 0.85 per rank — 0.405 for the rank-0 guess against 0.383 for the rank-1
        confirmation. `allocate_skills` now orders by declared score first, so this test is
        the rail on that ordering rather than a restatement of the caller's list.
        """
        body = "# S\n" + "\n".join(f"line {i} of a moderately long body" for i in range(1, 300))
        per = count_tokens(body)
        skills = tmp_path / "skills"
        _write_skill(skills, "chosen", "name: chosen\ndescription: s", body)
        _write_skill(skills, "guessed", "name: guessed\ndescription: s", body)
        # Deliberately passed surfaced-FIRST, so append order would pick the wrong one.
        alloc = allocate_skills(
            _loader(tmp_path),
            [
                _request(tmp_path, "guessed"),
                _request(tmp_path, "chosen", score=1.0, forced=True),
            ],
            # One body, plus room for the loser's reduced form.
            budget_tokens=per + 120,
        )
        states = {n: d.state for n, d in _by_name(alloc.decisions).items()}
        assert states["chosen"] is SkillLoadState.ADMITTED
        assert states["guessed"] is SkillLoadState.REDUCED

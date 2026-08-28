"""Lesson confidence (WF2LEA-15) — injection is gated on evidence, not on existence.

Every assertion about the gate is driven through the REAL capture path
(``VectorMemoryStore.write_lesson``) and read at the REAL injection seam
(``ambient.frame``). Nothing here writes a confidence value.

That constraint is the point of the test, not a stylistic preference. A test that
set ``confidence = 0.9`` on a row would prove the comparison in
``get_lessons_context`` works while leaving the code that *derives* the number
completely unexercised — the mechanism-not-its-use defect. Since the derivation is
the whole atom, a green suite over a hand-written field would be worth nothing:
the deriving code could be deleted and every assertion below would still pass.
So corroboration is produced the only way production produces it — by observing
the same rule again.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from personalclaw.learning import ambient
from personalclaw.learning import lesson_confidence as lc
from personalclaw.learning.hygiene import MIN_EVIDENCE_DEFAULT
from personalclaw.vector_memory import VectorMemoryStore

#: An agent-inferred source — the one that must corroborate before it is trusted.
#: `after_turn_review` is a real caller (`after_turn_review.py`), not a fixture name.
AGENT = "after_turn_review"

RULE = "prefer ruff over flake8 when linting this repository"


@pytest.fixture
def vs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> VectorMemoryStore:
    """A store whose memory.db AND evidence db both live under ``tmp_path``.

    ``PERSONALCLAW_HOME`` is redirected as well: the threshold reader loads
    ``AppConfig``, and a test whose gate depended on the developer's own
    config.json would pass or fail by accident.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    lc.reset_store()
    store = VectorMemoryStore(db_path=tmp_path / "memory.db")
    store.init()
    yield store
    lc.reset_store()


def _live_rules(store: VectorMemoryStore) -> list[str]:
    return [json.loads(row["value_json"]) for row in store.get_lessons()]


def _prompt(store: VectorMemoryStore) -> str:
    """What actually reaches the model — the injection seam, not an internal flag.

    ``get_lessons_context`` → ``ambient.render`` → ``ambient.frame`` is the exact
    chain ``context._render_ambient`` runs, so a lesson that is absent here is
    absent from the turn.
    """
    lessons = store.get_lessons_context()
    alloc = ambient.render(lessons=lessons, budget_tokens=4000)
    return ambient.frame(alloc, lessons_block=lessons)


def _standing(store: VectorMemoryStore, rule: str) -> lc.LessonVerdict:
    rows = [r for r in store.get_lessons() if rule in json.loads(r["value_json"])]
    assert rows, f"no stored lesson matches {rule!r}"
    return store.lesson_standings(rows)[str(rows[0]["key"])]


# ── the default is stated with a reason, not picked ──


def test_the_default_gate_is_the_evidence_floor_rather_than_a_chosen_number():
    """0.5 is `corroboration(min_evidence)`, and the identity is what pins it.

    Without this assertion the default is a number someone liked. With it, moving
    either the curve or the evidence floor without moving the other is a red test —
    which is what "stated with a reason" has to mean in code.
    """
    assert lc.DEFAULT_MIN_CONFIDENCE == lc.corroboration(MIN_EVIDENCE_DEFAULT)
    assert lc.DEFAULT_MIN_CONFIDENCE == pytest.approx(0.5)
    assert lc.corroboration(1) == 0.0, "one observation must carry no corroboration"
    assert lc.corroboration(2) < lc.DEFAULT_MIN_CONFIDENCE, "two is a coincidence"


def test_the_shipped_config_default_matches_the_derived_floor():
    from personalclaw.config.learning import LearningConfig

    assert LearningConfig().min_lesson_confidence == pytest.approx(lc.DEFAULT_MIN_CONFIDENCE)


# ── THE BAR: one observation does not reach a prompt; enough corroboration does ──


def test_a_single_unrepeated_observation_does_not_reach_a_prompt(vs):
    """One sighting of an inferred rule is an anecdote. It is stored, not injected."""
    assert vs.write_lesson(RULE, "tool", source=AGENT) is True, "premise: the row was written"
    assert _live_rules(vs) == [RULE], "premise: the lesson is RETAINED, not discarded"
    assert RULE not in _prompt(vs)


def test_the_same_lesson_reaches_a_prompt_after_enough_corroboration(vs):
    """The same rule, observed again through the same capture call, crosses the gate.

    Nothing between the two assertions touches a confidence field: the only
    difference is that `write_lesson` was called three more times with the same
    text, which is exactly what a recurring correction looks like in production.
    """
    vs.write_lesson(RULE, "tool", source=AGENT)
    assert RULE not in _prompt(vs), "premise: one observation is below the gate"
    for _ in range(3):
        # Returns False — the dedup pass suppresses the row. The OBSERVATION is
        # what survives, which is the signal this used to throw away.
        assert vs.write_lesson(RULE, "tool", source=AGENT) is False
    assert RULE in _prompt(vs)


def test_the_prompt_seam_can_inject_at_all(vs):
    """The vacuity guard for the two tests above.

    "Not in the prompt" is only evidence if this harness is capable of putting a
    lesson in one. A lesson the user taught directly is injected on its first
    observation — an instruction is not a hypothesis awaiting evidence — so this
    is the same `_prompt` chain producing the opposite answer.
    """
    assert vs.write_lesson("always sign off commits with a DCO trailer", "process") is True
    assert "always sign off commits with a DCO trailer" in _prompt(vs)


# ── retained-but-not-injected is a declared state ──


def test_a_below_gate_lesson_is_retained_and_keeps_accumulating(vs):
    vs.write_lesson(RULE, "tool", source=AGENT)
    first = _standing(vs, RULE)
    assert first.standing is lc.LessonStanding.RETAINED
    assert first.evidence.observations == 1
    assert "retained" in first.reason and "gate" in first.reason

    vs.write_lesson(RULE, "tool", source=AGENT)
    second = _standing(vs, RULE)
    assert second.standing is lc.LessonStanding.RETAINED, "still below the gate"
    assert second.evidence.observations == 2, "a retained lesson still gathers evidence"
    assert second.confidence > first.confidence, "the evidence moved the number"


def test_a_retained_lesson_is_never_deleted_by_the_gate(vs):
    vs.write_lesson(RULE, "tool", source=AGENT)
    _prompt(vs)  # the gate runs
    assert _live_rules(vs) == [RULE], "the gate must not evict what it declines to inject"


# ── the precedence rule: refutation is subtracted, never scored beside ──


def _midband_embedder():
    """A tiny embedder that lands two related rules in the 0.5–0.85 judge band."""
    vocab = ["deploy", "friday", "weekend", "morning", "release", "ship", "code", "review"]

    def emb(text: str) -> list[float]:
        lowered = text.lower()
        vec = [1.0 if word in lowered else 0.0 for word in vocab]
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    return emb


def test_a_contradiction_costs_the_lesson_its_corroboration(vs):
    """Driven through the judge, which is the only thing that declares a contradiction.

    The refuted lesson is superseded (pre-existing behaviour) AND loses its
    corroboration, so if the supersession were ever undone it would come back
    below the gate rather than beside the rule that refuted it.
    """
    vs.embed_fn = _midband_embedder()
    vs.contradiction_judge = lambda new, old: True
    for _ in range(4):
        vs.write_lesson("deploy on friday is fine", "tool", source=AGENT)
    store = lc.get_store(vs._db_path.parent)
    rows = [r for r in store.evidence_map(_all_keys(vs)).items()]
    assert rows, "premise: the first rule accumulated evidence"
    old_key, before = rows[0]
    assert before.contradictions == 0 and before.observations == 4

    vs.write_lesson("deploy on weekend is risky", "tool", source=AGENT)
    after = store.evidence_for(old_key)
    assert after.contradictions == 1, "the judge's verdict was recorded as evidence"
    assert after.surviving_observations == before.surviving_observations - 1
    verdict = lc.classify(after, threshold=lc.DEFAULT_MIN_CONFIDENCE)
    assert verdict.confidence < lc.classify(before).confidence


def test_a_refuted_lessons_corroboration_does_not_transfer_to_its_refuter(vs):
    """The refuter starts from its own evidence, not the evidence it defeated."""
    vs.embed_fn = _midband_embedder()
    vs.contradiction_judge = lambda new, old: True
    for _ in range(4):
        vs.write_lesson("deploy on friday is fine", "tool", source=AGENT)
    assert "deploy on friday is fine" in _prompt(vs), "premise: the old rule was injected"

    vs.write_lesson("deploy on weekend is risky", "tool", source=AGENT)
    assert "deploy on weekend is risky" not in _prompt(vs), "one observation of its own"
    assert "deploy on friday is fine" not in _prompt(vs), "refuted, so no longer injected"


def test_a_reversal_voids_the_observations_that_preceded_it(vs):
    """An explicit forget is the truest "a correction reversed it" signal there is.

    The lesson key is deterministic, so writing the same rule again resurrects the
    same row. Without the reversal it would return at the confidence it held when
    the user threw it away — the "why did it start doing that again" failure.
    """
    for _ in range(4):
        vs.write_lesson(RULE, "tool", source=AGENT)
    assert RULE in _prompt(vs), "premise: four observations cross the gate"

    assert vs.delete_lesson(RULE) is True
    vs.write_lesson(RULE, "tool", source=AGENT)  # re-observed once, post-reversal
    assert _live_rules(vs) == [RULE], "premise: the row is back"
    verdict = _standing(vs, RULE)
    assert verdict.evidence.reversals == 1
    assert verdict.evidence.voided == 4
    assert verdict.evidence.surviving_observations == 1
    assert verdict.standing is lc.LessonStanding.RETAINED
    assert RULE not in _prompt(vs)


def test_a_supersession_carries_evidence_forward_to_the_survivor(vs):
    """Restating a rule more precisely must not read as an evidence reset."""
    for _ in range(4):
        vs.write_lesson("use ruff", "tool", source=AGENT)
    assert "use ruff" in _prompt(vs), "premise"
    vs.write_lesson("use ruff for linting", "tool", source=AGENT)  # superset → supersedes
    verdict = _standing(vs, "use ruff for linting")
    assert verdict.evidence.observations == 5
    assert verdict.standing is lc.LessonStanding.INJECTED


# ── the derivation: pure, and every declared input matters ──


def test_every_declared_evidence_input_moves_the_confidence():
    base = lc.LessonEvidence(observations=5)
    assert lc.derive(base) == pytest.approx(lc.corroboration(5))
    # observed more often → higher
    assert lc.derive(lc.LessonEvidence(observations=9)) > lc.derive(base)
    # observed less recently → lower
    aged = lc.derive(base, active_days_idle=30.0)
    assert aged < lc.derive(base)
    # contradicted → lower
    assert lc.derive(lc.LessonEvidence(observations=5, contradictions=2)) < lc.derive(base)
    # reversed → the prior observations no longer count at all
    assert lc.derive(lc.LessonEvidence(observations=5, reversals=1, voided=5)) == 0.0


def test_contradiction_cancels_observations_one_for_one():
    """The precedence rule's step 2, spelled as the identity it claims."""
    three_minus_two = lc.LessonEvidence(observations=3, contradictions=2)
    one = lc.LessonEvidence(observations=1)
    assert lc.derive(three_minus_two) == lc.derive(one)
    assert lc.derive(three_minus_two) == 0.0, "and therefore below any non-zero gate"


def test_recency_rides_the_shared_decay_kernel_not_a_private_curve():
    from personalclaw.learning import decay

    evidence = lc.LessonEvidence(observations=5)
    expected = lc.corroboration(5) * decay.strength(
        kind="lesson", active_days_since_use=40.0, importance=0.0
    )
    assert lc.derive(evidence, active_days_idle=40.0) == pytest.approx(expected)


def test_recency_can_age_a_corroborated_lesson_below_the_gate():
    evidence = lc.LessonEvidence(observations=4)
    assert lc.classify(evidence).standing is lc.LessonStanding.INJECTED
    stale = lc.classify(evidence, active_days_idle=60.0)
    assert stale.standing is lc.LessonStanding.RETAINED
    assert "retained" in stale.reason


def test_a_human_authored_lesson_ages_slower_but_is_not_exempt():
    taught = lc.LessonEvidence(observations=1, human_authored=True)
    inferred = lc.LessonEvidence(observations=1)
    assert lc.derive(taught) == 1.0
    assert lc.derive(taught, active_days_idle=100.0) < 1.0, "not exempt from decay"
    assert lc.derive(taught, active_days_idle=100.0) > lc.derive(inferred, active_days_idle=100.0)


# ── one derivation site: the studio and the gate cannot disagree ──


def test_the_reported_standing_matches_what_the_prompt_contains(vs):
    vs.write_lesson("always run make lint before pushing", "process")  # human → injected
    vs.write_lesson(RULE, "tool", source=AGENT)  # inferred once → retained
    prompt = _prompt(vs)
    rows = vs.get_lessons()
    standings = vs.lesson_standings(rows)
    assert len(standings) == 2
    for row in rows:
        rule = json.loads(row["value_json"])
        verdict = standings[str(row["key"])]
        assert (rule in prompt) is verdict.injected, f"{rule!r} disagrees with {verdict.standing}"


def test_the_gate_fails_open_rather_than_stripping_every_lesson(vs, monkeypatch):
    """An evidence store that cannot be read must not empty the user's prompt."""
    vs.write_lesson(RULE, "tool", source=AGENT)

    def boom(_self):
        raise RuntimeError("evidence db unavailable")

    monkeypatch.setattr(VectorMemoryStore, "_lesson_evidence_store", boom)
    verdicts = vs.lesson_standings(vs.get_lessons())
    assert all(v.injected for v in verdicts.values())
    assert RULE in _prompt(vs), "fail-open: an unreadable gate injects rather than drops"


# ── the threshold is honoured, and it comes from config ──


def test_raising_the_threshold_holds_back_a_lesson_that_was_injected(vs, tmp_path):
    from personalclaw.config.loader import AppConfig, config_path

    for _ in range(4):
        vs.write_lesson(RULE, "tool", source=AGENT)
    assert RULE in _prompt(vs), "premise: injected at the default gate"

    config_path().write_text(json.dumps({"learning": {"min_lesson_confidence": 0.95}}))
    assert AppConfig.load().learning.min_lesson_confidence == pytest.approx(0.95)
    assert lc.configured_threshold() == pytest.approx(0.95)
    assert RULE not in _prompt(vs), "the configured floor is what the gate compares"


def test_the_threshold_round_trips_and_is_patchable(tmp_path, monkeypatch):
    """The write path: dataclass → load() → to_dict() → the PATCH allowlist."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.config.loader import AppConfig, config_path
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    spec = _EDITABLE_CONFIG["learning.min_lesson_confidence"]
    assert spec == {"type": "float", "min": 0.0, "max": 1.0}

    config_path().write_text(json.dumps({"learning": {"min_lesson_confidence": 0.72}}))
    loaded = AppConfig.load()
    assert loaded.learning.min_lesson_confidence == pytest.approx(0.72)
    assert loaded.to_dict()["learning"]["min_lesson_confidence"] == pytest.approx(0.72)


def test_a_zero_threshold_survives_the_loader(tmp_path, monkeypatch):
    """0.0 means "inject anything that exists" — a real choice, not a missing value.

    Its integer siblings in this block use `int(x) or default`, which would rewrite
    a deliberate 0 into the shipped default and make the knob unable to express its
    own bottom end.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.config.loader import AppConfig, config_path

    config_path().write_text(json.dumps({"learning": {"min_lesson_confidence": 0.0}}))
    assert AppConfig.load().learning.min_lesson_confidence == 0.0


def test_a_nonsense_threshold_clamps_instead_of_removing_the_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    lc.reset_store()
    from personalclaw.config.loader import config_path

    config_path().write_text(json.dumps({"learning": {"min_lesson_confidence": -3.0}}))
    assert lc.configured_threshold() == 0.0
    config_path().write_text(json.dumps({"learning": {"min_lesson_confidence": 40.0}}))
    assert lc.configured_threshold() == 1.0


@pytest.mark.asyncio
async def test_the_lessons_endpoint_reports_the_standing_and_its_evidence(vs, tmp_path):
    """The user-facing leg. A studio that cannot show the gate cannot explain it.

    Driven through the real handler, so the wire shape the Memory studio renders is
    the one asserted here rather than a hand-built dict.
    """
    from unittest.mock import MagicMock

    from personalclaw.dashboard.handlers.schedule import api_lessons
    from personalclaw.dashboard.state import DashboardState

    vs.write_lesson("always run make lint before pushing", "process")  # human → injected
    vs.write_lesson(RULE, "tool", source=AGENT)  # inferred once → retained

    mem = MagicMock()
    mem.vector_store = vs
    builder = MagicMock()
    builder.memory = mem
    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0, context_builder=builder)
    request = MagicMock()
    request.app = {"state": state}
    request.headers = {"X-Session-Key": "dashboard:ui"}
    request.query = {}

    body = json.loads((await api_lessons(request)).body)
    by_rule = {row["rule"]: row for row in body["lessons"]}
    assert by_rule["always run make lint before pushing"]["standing"] == "injected"
    held = by_rule[RULE]
    assert held["standing"] == "retained"
    assert held["confidence"] == 0.0
    assert held["observations"] == 1
    assert "gate" in held["confidence_reason"], "the reason has to say why"


def _all_keys(store: VectorMemoryStore) -> list[str]:
    return [
        str(r["key"])
        for r in store.db.execute("SELECT key FROM semantic_memory WHERE key LIKE 'lesson.%'")
    ]

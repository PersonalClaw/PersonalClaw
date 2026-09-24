"""The structured `rigor: deep` interrogation protocol (UP-R5, S45).

The existing :mod:`personalclaw.grill` pipeline stays exactly as it is — it is the vendor-neutral
`assess → recall → decompose → save` machinery, and this module is the planner's protocol ON TOP of
it, not a replacement. What the protocol adds is everything that makes deep grilling fast rather
than tedious:

* **Every question ships with the planner's recommended answer.** A question with no default is a
  question the user must think about from nothing; a question with one they can accept is a
  confirmation. This is the single largest difference between a grill a user finishes and one they
  abandon halfway.
* **Facts are LOOKED UP, not asked.** A question whose answer is already discoverable — in the
  codebase, in the knowledge store, in memory — is a question that makes the system look like it
  has not been paying attention. The split is mechanical here: a question is routed to a lookup
  channel or it is asked, never both.
* **The channels are never conflated.** Memory recall and knowledge search are two subsystems with
  two different lifecycles, and a merged "context fetch" would make it impossible to say which one
  answered. They are separate callables with separate result provenance, per the plan's boundary
  note.
* **Prohibitions are frozen.** A Stop/never-do answer that could be re-litigated by a later stage
  is not a boundary, it is a suggestion. Once captured it is injected verbatim into every stage's
  worker context.

The protocol is PURE: no LLM call, no I/O, no clock. Rounds are built, answers are folded in, and
the Step-0 schema is derived — the caller owns asking and looking up. That is what makes the
asymmetries below testable at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from personalclaw.workflows.human_input import Ask, AskField, AskKind
from personalclaw.workflows.intent import Intent, Rigor

#: A batched round is capped so a single submit is answerable in one sitting. Measured against the
#: plan's own number: ≤8 typed question objects.
MAX_BATCH = 8

#: Options per choice question. Two is a coin flip presented as a decision; more than five is a
#: menu nobody reads, and the escape hatch below makes the cap safe.
MIN_CHOICES = 2
MAX_CHOICES = 5

#: Every choice question carries this. A closed option set is a claim that the planner enumerated
#: the possibilities, and it is wrong often enough that removing the escape hatch would silently
#: force a wrong answer rather than surface a missing one.
OTHER = "Other (describe)"


class Channel(str, Enum):
    """Where an answer comes from.

    `ASK` is the expensive one — it costs the user's attention — so the whole point of the
    facts-vs-decisions split is to route as little as possible here.
    """

    ASK = "ask"
    CODEBASE = "codebase"  # the brownfield context pass
    KNOWLEDGE = "knowledge"  # the knowledge store: HybridRetriever / knowledge_search
    MEMORY = "memory"  # harness-known facts: MemoryService recall


#: Phrases that mark a question as answerable by LOOKING. Ordered most-specific-first because the
#: first match wins and `what did I decide` must not be captured by the generic codebase words.
_LOOKUP_SIGNALS: tuple[tuple[Channel, tuple[str, ...]], ...] = (
    (
        Channel.MEMORY,
        (
            "did i decide",
            "did we decide",
            "did i already",
            "did we already",
            "have i said",
            "my preference",
            "do i usually",
            "did i previously",
            "prior decision",
            "settled earlier",
        ),
    ),
    (
        Channel.KNOWLEDGE,
        (
            "in my notes",
            "do i have a note",
            "what do i know about",
            "did i save",
            "in the knowledge",
            "documented anywhere",
            "recorded about",
        ),
    ),
    (
        Channel.CODEBASE,
        # Measured: a hand-listed set had "which file" and "what module" but not "which module",
        # so a natural phrasing fell through to ASK. An arbitrary asymmetry in a router is worse
        # than a narrow one — it works often enough that nobody notices the half that does not.
        # The interrogative × noun cross-product removes the asymmetry by construction.
        tuple(
            f"{interrogative} {noun}"
            for interrogative in ("which", "what")
            for noun in (
                "file",
                "files",
                "module",
                "modules",
                "function",
                "class",
                "package",
                "test",
                "tests",
                "endpoint",
            )
        )
        + (
            "where is",
            "where does",
            "what does the code",
            "current implementation",
            "existing signature",
            "already implemented",
            "how is it currently",
            "how does it currently",
        ),
    ),
)

#: A question the protocol must ask in every round. Its answers become the frozen prohibitions
#: block, and it is unconditional because a boundary nobody was asked for is a boundary nobody set.
BOUNDARY_QUESTION = "Is there anything this must NOT do?"


@dataclass
class Question:
    """One typed question, carrying its own recommendation and its own channel.

    `recommended` is the protocol's whole speed story, and `depends_on` is what decides pacing:
    independent questions batch, dependent ones cannot.
    """

    key: str
    text: str
    kind: str = "text"  # text | choice | slider | boundary
    choices: list[str] = field(default_factory=list)
    recommended: Any = None
    #: Keys this question's phrasing depends on. A question whose text is only meaningful after
    #: another is answered cannot go in the same batch.
    depends_on: list[str] = field(default_factory=list)
    load_bearing: bool = True
    channel: Channel = Channel.ASK
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "text": self.text,
            "kind": self.kind,
            "choices": list(self.choices),
            "recommended": self.recommended,
            "depends_on": list(self.depends_on),
            "load_bearing": self.load_bearing,
            "channel": self.channel.value,
            "why": self.why,
        }


@dataclass
class Round:
    """One interrogation round: either a batch or a single dependent question.

    `batched` is derived, never declared — a caller that could claim it would let a dependent
    question ride along in a batch whose phrasing it invalidates.
    """

    questions: list[Question] = field(default_factory=list)
    batched: bool = False
    reason: str = ""

    def to_ask(self) -> Ask:
        """Render as the engine's ONE typed ask payload.

        A batch becomes a `form` (one submit); a single question becomes `choice` or `text`. Using
        the engine's own payload rather than a parallel shape is what lets the QuestionSlider widget
        render a grill round with no planner-specific renderer.
        """
        if self.batched:
            return Ask(
                kind=AskKind.FORM,
                prompt="A few things before I plan this:",
                fields=[_as_field(q) for q in self.questions],
            )
        question = self.questions[0]
        if question.choices:
            return Ask(
                kind=AskKind.CHOICE,
                prompt=question.text,
                choices=_with_other(question.choices),
            )
        return Ask(kind=AskKind.TEXT, prompt=question.text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "questions": [q.to_dict() for q in self.questions],
            "batched": self.batched,
            "reason": self.reason,
        }


def _as_field(question: Question) -> AskField:
    """One question as a typed form field, with its recommendation as the DEFAULT.

    The recommendation has to arrive as `default`, not as prose in the label: a recommendation the
    user must retype is not one they can accept.
    """
    field_type = "choice" if question.choices else "string"
    if question.kind == "slider":
        field_type = "number"
    return AskField(
        name=question.key,
        type=field_type,
        label=question.text,
        required=question.load_bearing,
        default=question.recommended,
        choices=_with_other(question.choices) if question.choices else [],
    )


def _with_other(choices: list[str]) -> list[str]:
    """Append the escape hatch unless it is already there. Never replaces an option."""
    out = [c for c in choices if c]
    if OTHER not in out:
        out.append(OTHER)
    return out[: MAX_CHOICES + 1]


def route_question(text: str) -> Channel:
    """Which channel answers this question.

    Discoverable facts are looked up; only genuine decisions are asked. A question routed to a
    lookup channel and ALSO asked would be the failure this split exists to prevent — the user
    answering something the system could have read.
    """
    lowered = (text or "").lower()
    for channel, signals in _LOOKUP_SIGNALS:
        if any(signal in lowered for signal in signals):
            return channel
    return Channel.ASK


def split_facts_and_decisions(
    questions: list[Question],
) -> tuple[list[Question], dict[Channel, list[Question]]]:
    """Partition into `(to_ask, {channel: to_look_up})`.

    Returned as a per-channel mapping rather than one "context" list because the caller has to
    dispatch each to a DIFFERENT subsystem, and a merged list would force it to guess. Questions
    whose channel was set explicitly are honored — the router only classifies the unset ones.
    """
    to_ask: list[Question] = []
    lookups: dict[Channel, list[Question]] = {}
    for question in questions:
        channel = (
            question.channel
            if question.channel is not Channel.ASK
            else route_question(question.text)
        )
        question.channel = channel
        if channel is Channel.ASK:
            to_ask.append(question)
        else:
            lookups.setdefault(channel, []).append(question)
    return to_ask, lookups


def pace(questions: list[Question]) -> list[Round]:
    """Group questions into rounds.

    Three or more INDEPENDENT load-bearing decisions earn one batched round — a user who can see
    all of them answers faster than one led through a sequence. Dependent questions fall back to
    one-per-turn, because a question whose phrasing depends on an unanswered one is a question
    asked wrong.
    """
    if not questions:
        return []
    independent = [q for q in questions if not q.depends_on]
    dependent = [q for q in questions if q.depends_on]
    rounds: list[Round] = []
    load_bearing = [q for q in independent if q.load_bearing]
    if len(load_bearing) >= 3:
        for start in range(0, len(independent), MAX_BATCH):
            chunk = independent[start : start + MAX_BATCH]
            rounds.append(
                Round(
                    questions=chunk,
                    batched=True,
                    reason=f"{len(load_bearing)} independent load-bearing decisions",
                )
            )
    else:
        rounds.extend(
            Round(questions=[q], batched=False, reason="too few to batch") for q in independent
        )
    # Dependent questions are ordered after everything they name, one per round.
    for question in _in_dependency_order(dependent):
        rounds.append(
            Round(
                questions=[question],
                batched=False,
                reason=f"depends on {', '.join(question.depends_on)}",
            )
        )
    return rounds


def _in_dependency_order(questions: list[Question]) -> list[Question]:
    """Stable topological-ish order: a question comes after any listed dependency present here.

    Not a full toposort — a cycle in planner-authored questions is a bug, and silently reordering
    around one would hide it. Anything unresolved keeps its original position.
    """
    remaining = list(questions)
    placed: list[Question] = []
    placed_keys: set[str] = set()
    progress = True
    while remaining and progress:
        progress = False
        for question in list(remaining):
            pending = [
                dep
                for dep in question.depends_on
                if dep not in placed_keys and any(q.key == dep for q in remaining)
            ]
            if not pending:
                placed.append(question)
                placed_keys.add(question.key)
                remaining.remove(question)
                progress = True
    return placed + remaining


def boundary_question() -> Question:
    """The Stop/never-do question. Present in every round set, unconditionally."""
    return Question(
        key="prohibitions",
        text=BOUNDARY_QUESTION,
        kind="boundary",
        recommended="",
        load_bearing=False,
        why="answers freeze into a prohibitions block every stage sees",
    )


def build_rounds(questions: list[Question]) -> list[Round]:
    """The full round plan: facts filtered out, boundary appended, then paced.

    The boundary question is appended AFTER the split so a lookup router can never route it away —
    "what must this not do" is not discoverable by definition.
    """
    to_ask, _lookups = split_facts_and_decisions(questions)
    to_ask = [q for q in to_ask if q.key != "prohibitions"]
    return pace(to_ask + [boundary_question()])


@dataclass
class Probe:
    """One adversarial scenario probe.

    A probe is not a question about preferences: it is a concrete scenario the user's stated
    constraints do not cover, which is how a stated-vs-revealed contradiction surfaces BEFORE the
    spec is emitted rather than in the run's output.
    """

    scenario: str
    tests: str  # which stated constraint it stresses
    contradicts: str = ""  # filled when the answer contradicts what was stated

    def to_dict(self) -> dict[str, Any]:
        return {"scenario": self.scenario, "tests": self.tests, "contradicts": self.contradicts}


#: Constraint families and the scenario that stresses each. Deterministic, because a probe
#: generated by a model is one that cannot be tested — and the value is in the STRESS, not the
#: prose. The caller may add model-written probes on top.
_PROBE_SHAPES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("fast", "quick", "asap", "today", "urgent", "deadline"),
        "The first result is thin but arrives in a tenth of the time. Ship it or wait?",
        "speed",
    ),
    (
        ("thorough", "exhaustive", "comprehensive", "complete", "every"),
        "Coverage stalls at ~80% and the rest costs as much again. Stop or continue?",
        "completeness",
    ),
    (
        ("cheap", "budget", "minimal", "small", "low cost"),
        "The cheap path needs three retries and ends up costing more. Which do you want?",
        "cost",
    ),
    (
        ("accurate", "correct", "verified", "reliable", "precise"),
        "One claim cannot be verified from available sources. Drop it or flag it and keep it?",
        "accuracy",
    ),
    (
        ("automatic", "unattended", "hands off", "without asking", "autonomous"),
        "A step needs a judgement nobody anticipated. Guess and continue, or stop and wait?",
        "autonomy",
    ),
)


def stress_probes(goal: str, *, limit: int = 3) -> list[Probe]:
    """Adversarial probes derived from the goal's OWN stated constraints.

    Probes are generated from what the user said, never from a fixed list: a probe about cost
    posed to someone who never mentioned cost is a question about nothing, and it is the fastest
    way to make a grill feel like a form. Capped at three — the phase exists to surface a
    contradiction, not to re-interrogate.
    """
    lowered = (goal or "").lower()
    probes: list[Probe] = []
    for words, scenario, tests in _PROBE_SHAPES:
        if any(word in lowered for word in words):
            probes.append(Probe(scenario=scenario, tests=tests))
        if len(probes) >= limit:
            break
    return probes


def contradiction(probe: Probe, answer: str) -> str:
    """Whether a probe answer contradicts the constraint it stressed.

    Deliberately narrow: it fires only when the answer picks the side the stated constraint ruled
    out. A looser check would report a contradiction on every nuanced answer, and a contradiction
    report nobody believes is worse than none.
    """
    lowered = (answer or "").lower()
    if not lowered:
        return ""
    opposite = {
        "speed": ("wait", "take the time", "do it properly", "hold"),
        "completeness": ("stop", "good enough", "ship what we have", "80"),
        "cost": ("expensive", "spend", "the better one", "whatever it costs"),
        "accuracy": ("keep it", "flag it and keep", "include it"),
        "autonomy": ("stop and wait", "ask me", "wait for me", "check with me"),
    }.get(probe.tests, ())
    for phrase in opposite:
        if phrase in lowered:
            return f"stated {probe.tests}, but chose “{phrase}” when it was tested"
    return ""


@dataclass
class StepZero:
    """The Step-0 output schema: what is confirmed, what is assumed, what is still open.

    The three lists exist as three lists specifically so an assumption can never be presented as a
    requirement. `open_questions` are BLOCKERS — a plan emitted over an open question is a plan
    built on a guess nobody labelled.
    """

    confirmed: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    prohibitions: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """Whether spec emission may proceed. An open question blocks it."""
        return not self.open_questions

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmed": list(self.confirmed),
            "assumptions": list(self.assumptions),
            "open_questions": list(self.open_questions),
            "prohibitions": list(self.prohibitions),
            "ready": self.ready,
        }


#: Answers that mean "you decide". They become ASSUMPTIONS, never confirmed requirements — a
#: deferral recorded as a decision is exactly the guess-as-requirement failure Step-0 exists to
#: prevent.
_DEFERRALS = (
    "you decide",
    "your call",
    "whatever you think",
    "up to you",
    "no preference",
    "dont care",
    "don't care",
    "either",
    "default",
    "n/a",
    "idk",
    "not sure",
    "unsure",
)


def fold_answers(
    questions: list[Question],
    answers: dict[str, Any],
    *,
    looked_up: dict[str, str] | None = None,
) -> StepZero:
    """Fold answers and lookups into the Step-0 schema.

    Three routings, and the distinction between them is the point:

    * an answered question → **confirmed** (the user said it);
    * an unanswered one, or one deferred back to the planner → **assumption** if it has a
      recommendation, **open question** if it does not;
    * a looked-up fact → **confirmed**, tagged with the channel that found it, because a fact from
      a subsystem is not the user's word and a review must be able to tell them apart.
    """
    step = StepZero()
    for question in questions:
        if question.key == "prohibitions":
            step.prohibitions.extend(_split_prohibitions(answers.get("prohibitions")))
            continue
        raw = answers.get(question.key)
        given = str(raw).strip() if raw is not None else ""
        if given and not _is_deferral(given):
            step.confirmed.append(f"{question.text} → {given}")
        elif question.recommended not in (None, ""):
            step.assumptions.append(f"{question.text} → {question.recommended} (assumed)")
        elif question.load_bearing:
            step.open_questions.append(question.text)
    for key, value in (looked_up or {}).items():
        if value:
            step.confirmed.append(f"{key} → {value} (looked up)")
    return step


def settled_decisions(step: StepZero) -> list[str]:
    """The decisions a grill pass actually SETTLED — the input to ``grill.SaveFn``.

    ``grill.py`` declares ``SaveFn = Callable[[str], None]`` and takes ``save=None``, and every
    caller passed None, so a grill pass could settle a question and then forget the answer — the
    next pass re-asked what the user had already decided, which is precisely the "memory-checked
    decomposition" upgrade the pipeline exists to deliver.

    What counts as settled is a real distinction, not a formality: only ``confirmed`` items are
    returned. An ASSUMPTION is the planner's guess and persisting it as a lesson would harden a
    guess into a standing instruction; an OPEN QUESTION is by definition unsettled. Prohibitions ARE
    settled — the user stating a boundary is the most durable decision a grill produces — so they
    are included, tagged so a reviewer can see they are constraints rather than choices.
    """
    out = [str(c).strip() for c in step.confirmed if str(c).strip()]
    out += [f"Prohibition: {p}".strip() for p in step.prohibitions if str(p).strip()]
    return out


def _is_deferral(answer: str) -> bool:
    lowered = answer.lower().strip(" .!?")
    return any(lowered == phrase or lowered.startswith(phrase) for phrase in _DEFERRALS)


def _split_prohibitions(raw: Any) -> list[str]:
    """One free-text boundary answer into discrete prohibitions.

    Split on newlines and semicolons only. Splitting on commas would shred a single prohibition
    that happens to contain a list ("don't touch prod, staging, or CI"), turning one boundary into
    three partial ones.
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        items = [str(item) for item in raw]
    else:
        items = re.split(r"[\n;]+", str(raw))
    return [item.strip(" -•\t") for item in items if item.strip(" -•\t")]


def prohibitions_block(prohibitions: list[str]) -> str:
    """The frozen block injected verbatim into every stage's worker context.

    Frozen means frozen: no summarizing, no re-ranking, no truncation. A prohibition a later stage
    could re-litigate is not a boundary, and a boundary that fits in a summary is one that can be
    dropped by a summarizer.
    """
    if not prohibitions:
        return ""
    lines = "\n".join(f"- {p}" for p in prohibitions)
    return "HARD PROHIBITIONS — these were set by the user and are not negotiable:\n" + lines


def inject_prohibitions(spec: dict[str, Any], prohibitions: list[str]) -> dict[str, Any]:
    """Attach the frozen block to every model-bearing node in the spec.

    Every stage, not the root: a stage's worker sees its own config, and a prohibition parked at
    the root is one the worker never reads. Zero-token nodes are skipped — they run no model that
    could violate a prohibition, and an unread block there is noise in the spec diff.
    """
    block = prohibitions_block(prohibitions)
    if not block:
        return spec
    out = dict(spec)
    root = out.get("root")
    if isinstance(root, dict):
        out["root"] = _inject(root, block)
    return out


def _inject(node: dict[str, Any], block: str) -> dict[str, Any]:
    out = dict(node)
    if out.get("kind") in ("stage", "gate"):
        config = dict(out.get("config") or {})
        if config.get("prompt") or out.get("kind") == "stage":
            config["prohibitions"] = block
            out["config"] = config
    for key in ("children", "branches"):
        if isinstance(out.get(key), list):
            out[key] = [_inject(c, block) if isinstance(c, dict) else c for c in out[key]]
    for key in ("body", "then", "otherwise"):
        if isinstance(out.get(key), dict):
            out[key] = _inject(out[key], block)
    return out


def deep_triggered(intent: Intent, risk_hits: list[Any] | None = None) -> tuple[bool, str]:
    """Whether the grill runs, and WHY.

    Two triggers, both from mechanisms that already exist: the classifier's own `DEEP` routing, and
    any risk-registry hit. The reason is returned rather than logged because a user who was
    interrogated deserves to know what earned it — an unexplained grill reads as the system being
    slow, not careful.
    """
    if risk_hits:
        names = sorted({getattr(h, "signal", str(h)) for h in risk_hits})
        return True, f"risk signal: {', '.join(names)}"
    if intent.rigor is Rigor.DEEP:
        return True, intent.reason or "classified deep"
    return False, ""

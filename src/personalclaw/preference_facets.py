"""Typed, decaying preference-facet model (learn-preference-facets).

A principled user-profile layer distinct from contextual memory: preferences are
**typed facets** with a **stability score that decays by class half-life**, so a
one-off stylistic nudge fades unless reinforced while an identity fact persists.

Six facet classes (with decay half-lives):
- ``style``    — how the user likes responses (terse, code-first…)   30d
- ``identity`` — who they are (name, role, stack)                    90d
- ``tooling``  — preferred tools/workflows                           30d
- ``goal``     — standing objectives                                 30d
- ``channel``  — per-surface prefs                                    7d
- ``veto``     — hard "never do X"  → THIS IS A LESSON, not a facet:
  vetoes route to ``write_lesson`` so the agent's "always/never" rules live in
  ONE place (the lesson store + contradiction judge), not a parallel model.

Stability = ``base × cue × decay(age, half_life)``. Cue families weight the
evidence (Explicit 1.0 → Recurrence 0.6). State machine (Active / Provisional /
Candidate / Dropped) is derived from the live stability + the user overrides
(Pinned = floor 1.0, Forgotten = 0). The Active facets render into an always-on
ambient PROFILE block (the stable-defaults half; on-demand recall stays separate).

No new LLM calls: facet candidates come from cheap heuristics + the EXISTING
consolidation/after-turn summarizer. Persisted as ``pref.facet.<class>.<slug>``
semantic keys, reusing semantic memory (+ supersession + recall_count).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

FACET_CLASSES = ("style", "identity", "tooling", "goal", "channel", "veto")

# Class decay half-lives (days) — how fast stability halves without reinforcement.
_HALF_LIFE_DAYS: dict[str, float] = {
    "identity": 90.0,
    "style": 30.0,
    "tooling": 30.0,
    "goal": 30.0,
    "channel": 7.0,
}

# Cue families → base evidence weight.
_CUE_WEIGHT: dict[str, float] = {
    "explicit": 1.0,
    "edit": 0.8,
    "correction": 0.9,
    "recurrence": 0.6,
    "inferred": 0.5,
}

# State thresholds over the (decayed) stability.
_ACTIVE_AT = 0.6
_PROVISIONAL_AT = 0.35
_DROP_BELOW = 0.15
# Ambient-block budget so no class dominates / the block stays small.
_MAX_RENDERED = 25


@dataclass
class Facet:
    """One typed preference. ``stability`` is the score AT ``updated_at``;
    :func:`decayed_stability` applies the class half-life at read time."""

    cls: str
    text: str
    stability: float
    updated_at: str
    cue: str = "inferred"
    pinned: bool = False
    forgotten: bool = False

    def to_payload(self) -> dict:
        return {
            "cls": self.cls,
            "text": self.text,
            "stability": self.stability,
            "updated_at": self.updated_at,
            "cue": self.cue,
            "pinned": self.pinned,
            "forgotten": self.forgotten,
        }

    @classmethod
    def from_payload(cls, d: dict) -> "Facet":
        return cls(
            cls=d.get("cls", "style"),
            text=d.get("text", ""),
            stability=float(d.get("stability", 0.5)),
            updated_at=d.get("updated_at", ""),
            cue=d.get("cue", "inferred"),
            pinned=bool(d.get("pinned")),
            forgotten=bool(d.get("forgotten")),
        )


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def base_stability(cue: str) -> float:
    """Initial stability for a freshly-observed facet, from its cue family."""
    return _CUE_WEIGHT.get(cue, 0.5)


def decay(value: float, age_days: float, half_life_days: float) -> float:
    """Exponential half-life decay: ``value × 0.5**(age/half_life)`` — the ONE decay
    machinery PClaw's read-time-decay stores share (preference facets here + P11's
    engagement signals). Non-positive half-life ⇒ no decay (guards div-by-zero)."""
    if half_life_days <= 0:
        return value
    return value * (0.5 ** (max(0.0, age_days) / half_life_days))


def decayed_stability(facet: Facet, *, now: datetime | None = None) -> float:
    """Stability with class half-life decay applied. Pinned = 1.0, Forgotten = 0."""
    if facet.forgotten:
        return 0.0
    if facet.pinned:
        return 1.0
    if facet.cls == "veto":  # vetoes don't decay (they're lessons)
        return facet.stability
    half = _HALF_LIFE_DAYS.get(facet.cls, 30.0)
    upd = _parse(facet.updated_at)
    if upd is None:
        return facet.stability
    age_days = ((now or _now()).timestamp() - upd.timestamp()) / 86400.0
    return decay(facet.stability, age_days, half)


def facet_state(facet: Facet, *, now: datetime | None = None) -> str:
    """Active / Provisional / Candidate / Dropped from decayed stability + overrides."""
    if facet.forgotten:
        return "Dropped"
    if facet.pinned:
        return "Active"
    s = decayed_stability(facet, now=now)
    if s >= _ACTIVE_AT:
        return "Active"
    if s >= _PROVISIONAL_AT:
        return "Provisional"
    if s >= _DROP_BELOW:
        return "Candidate"
    return "Dropped"


def reinforce(facet: Facet, cue: str, *, now: datetime | None = None) -> Facet:
    """Reinforce a facet with new evidence — raises stability toward 1.0.

    New stability = current decayed value pulled toward the cue's base weight
    (so repeated explicit statements climb; a weak recurrence nudges gently).
    """
    cur = decayed_stability(facet, now=now)
    weight = base_stability(cue)
    facet.stability = min(1.0, cur + weight * (1.0 - cur))
    facet.cue = cue
    facet.updated_at = (now or _now()).isoformat()
    return facet


# ── Heuristic candidate producers (no LLM) ──

# ── The style rule (stated once, here) ──
#
# A style word is not a preference on its own. "a shorter version of those notes", "make
# it more concise", "keep it short", "just the code for the parser" each ask for something
# about ONE piece of work, and the detector's output is an explicit-cue facet: Active at
# once, and rendered into the USER PROFILE block of every later prompt as a "stable learned
# preference". Matching the bare word "shorter" filed a request about one set of notes as
# a standing rule for every answer after it. So a style hint is learned only when its
# sentence frames it as standing, in one of three ways:
#
#   1. a standing-scope marker in the same sentence — "from now on", "always", "going
#      forward", "in general", "whenever you reply" (:data:`_STANDING_SCOPE_RE`);
#   2. the assistant's replies IN GENERAL as its object — a plural reply noun ("keep your
#      answers short", "I prefer shorter answers", "make your replies more concise"); or
#   3. a sentence that IS the manner instruction, addressed to the assistant and naming no
#      work ("Be more terse.", "Get to the point.", "No fluff, please.").
#
# Which frames a hint may use depends on what it says (the groups of
# :data:`_STYLE_HINT_RE`): a ``replies`` hint names the replies, so it stands by itself; a
# ``manner`` hint needs frame 1 or 3; a ``work`` hint ("keep it short", "shorter", "just
# the code") reads as a request about the work at hand, so only frame 1 makes it standing.
# A sentence that scopes itself to now ("this time", "for now") is never standing, and
# every hint in the message is tried, so a one-off before a real preference does not hide
# it. Precision over recall, for the veto rule's reason: a missed preference is cheap (the
# after-turn summarizer produces the richer set), while a false one steers every future turn.
#
# Sentences split on ``.!?`` and newlines, never on commas: "Summarize this, be brief" is one
# instruction about one summary, and "From now on, be brief" keeps its marker.

_MANNER_ADJ = (
    r"(?:terse|concise|brief|short|shorter|succinct|verbose|detailed|formal|casual|direct)"
)
_KEEP_ADJ = r"(?:short|shorter|concise|brief|terse|to the point|detailed|formal|casual)"
_REPLY_NOUNS = r"(?:responses|answers|replies|messages|explanations)"

_STYLE_HINT_RE = re.compile(
    r"\b(?:"
    r"(?P<replies>keep (?:your |the )?(?:responses|answers|replies) " + _KEEP_ADJ + r"|"
    r"make (?:your |the )?" + _REPLY_NOUNS + r" (?:more |less )?" + _MANNER_ADJ + r"|"
    r"(?:more |less )?" + _MANNER_ADJ + r" " + _REPLY_NOUNS + r")"
    r"|(?P<manner>be (?:more |less )?"
    r"(?:terse|concise|brief|succinct|verbose|detailed|formal|casual|direct)|"
    r"(?:no|less|more|without) (?:preamble|explanations?|comments|filler|fluff)|"
    r"get to the point|stop explaining)"
    r"|(?P<work>keep (?:your |the )?(?:it|them|this|that|things?|response|answer|reply) "
    + _KEEP_ADJ
    + r"|just (?:the )?(?:code|answer|facts)|to the point|shorter|more concise)"
    r")\b",
    re.IGNORECASE,
)

#: Frame 1 of the style rule: the sentence says the hint holds beyond this request.
_STANDING_SCOPE_RE = re.compile(
    r"\b(?:from now on|going forward|from here on(?: out)?|(?:in|for) (?:the )?future|"
    r"always|by default|in general|generally|as a rule|every time|each time|whenever|"
    r"all the time|at all times|henceforth|"
    r"in (?:all|every) (?:of )?(?:your )?(?:replies|reply|answers?|responses?|messages?)|"
    r"when(?:ever)? you (?:reply|answer|respond|write|explain))\b",
    re.IGNORECASE,
)

#: A sentence that scopes itself to now is never a standing preference.
_ONE_OFF_SCOPE_RE = re.compile(
    r"\b(?:this time|for now|just this once|this once|for this one|right now)\b",
    re.IGNORECASE,
)

#: Words that may surround a manner instruction without naming any work (frame 3): the
#: politeness and softening around "be more direct", and nothing that could be a task.
_MANNER_FILLER = frozenset("""
    please pls kindly thanks thank you ok okay just try to can could would will and also
    hey hi oh so a bit little lot much way
    """.split())

_SENTENCE_END_RE = re.compile(r"[.!?\n]")


def _sentence_around(msg: str, start: int, end: int) -> str:
    """The sentence of *msg* that holds ``msg[start:end]`` (split on ``.!?`` and newlines)."""
    left = max((m.end() for m in _SENTENCE_END_RE.finditer(msg, 0, start)), default=0)
    right = _SENTENCE_END_RE.search(msg, end)
    return msg[left : right.start() if right else len(msg)]


def _only_the_instruction(sentence: str, hint: str) -> bool:
    """Frame 3: *sentence* is *hint* plus politeness, and names no work."""
    rest = sentence.lower().replace(hint.lower(), " ", 1)
    return all(tok in _MANNER_FILLER for tok in _CLAUSE_TOKEN_RE.findall(rest))


def style_hint(user_message: str) -> str | None:
    """The distilled style hint of a STANDING preference in *user_message*, or None.

    Implements the style rule stated above; every hint in the message is tried in order.
    """
    msg = user_message or ""
    for m in _STYLE_HINT_RE.finditer(msg):
        hint = m.group(0)
        sentence = _sentence_around(msg, m.start(), m.end())
        if _ONE_OFF_SCOPE_RE.search(sentence):
            continue
        standing = bool(_STANDING_SCOPE_RE.search(sentence))
        if m.group("replies") or standing:
            return hint.strip().lower()[:120]
        if m.group("manner") and _only_the_instruction(sentence, hint):
            return hint.strip().lower()[:120]
    return None


# ── The veto rule (stated once, here) ──
#
# A negative trigger word is NOT a prohibition on its own. "never" is also a degree
# adverb ("reply in exactly one sentence from now on, never more") and it heads fixed
# idioms ("never mind", "better late than never"). Matching the trigger plus
# anything-until-punctuation therefore learned the FRAGMENT "never more" as a durable
# veto. So a veto is recognized only when the trigger is followed by a
# **prohibited-action clause**, which requires all three of:
#
#   1. a head token that can open a verb phrase — i.e. NOT a closed-class function
#      word, degree adverb, comparative, pronoun, preposition, conjunction, copula or
#      modal auxiliary (:data:`_NON_ACTION_HEADS`);
#   2. at least one further token (the action's object/complement) after any
#      emphatic doubling ("never, ever …") is stripped, so a bare
#      intensifier or a trailing "…, never" can never qualify; and
#   3. a clause that is not one of the enumerated non-prohibitive idioms
#      (:data:`_VETO_IDIOM_HEADS` / :data:`_VETO_IDIOM_CLAUSES`).
#
# Precision over recall, deliberately: this detector's output is the one that reaches
# the DURABLE lesson store (a veto routes to ``write_lesson``, where the always/never
# rules and the contradiction judge live), while a missed veto is cheap — the existing
# after-turn summarizer still produces the richer set. No LLM call is added.
#
# Deliberately REJECTED by this rule: "never more", "never more than one sentence"
# (a quantity nudge, not a prohibition), "never again", "never mind (the tests)",
# "better late than never", "now or never", "never say never", "would never have
# guessed", "never to be repeated", and any trigger with no complement at all.
_VETO_TRIGGER_RE = re.compile(
    r"\b(?:never|do ?n'?t ever|do not ever|always avoid)\b",
    re.IGNORECASE,
)

#: Tokens that cannot head a prohibited action. Closed-class words plus the degree
#: adverbs/comparatives that make ``never`` read as an intensifier. ``do``/``get``/
#: ``let`` are absent on purpose — they are main verbs in real vetoes ("never do that
#: again"); ``have``/``had`` ARE excluded because "would never have guessed" is far
#: more common than "never have X".
_NON_ACTION_HEADS = frozenset("""
    more less again ever too so very quite enough than as then only just also
    the a an any some this that these those it its me my mine you your yours
    he him his she her hers they them their we us our ours i
    in on at for with without from by of about into onto over under near
    before after since during until while because if unless though although
    and but or nor
    is are was were be been being am s
    will would shall should can could may might must have has had having to
    """.split())

#: Emphatic doubling of the trigger ("never, ever do that again"). Stripped from the
#: front of a clause before the head is judged, so an emphatic veto is not mistaken for
#: an adverbial one. ``ever`` is in :data:`_NON_ACTION_HEADS` too — it cannot head an
#: action — but leading it must not condemn the clause behind it.
_EMPHATIC_CLAUSE_HEADS = frozenset({"ever"})

#: A clause head that is a verb but whose phrase is not a prohibition.
_VETO_IDIOM_HEADS = frozenset({"mind"})  # "never mind …" is a discourse marker

#: Whole normalized clauses that are proverbs rather than prohibitions.
_VETO_IDIOM_CLAUSES = frozenset({"say never"})

_CLAUSE_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-_/]*")


def veto_clause(user_message: str) -> str | None:
    """The prohibited-action clause of a veto in ``user_message``, or None.

    Implements the veto rule stated above: trigger + verb-phrase head + at least one
    complement token, minus the enumerated idioms. Every trigger occurrence is tried,
    so a non-prohibitive first "never" does not mask a genuine veto later in the
    message. The returned text is the DISTILLED clause (trigger + its clause, stopping
    at sentence punctuation), never the whole raw message.
    """
    msg = user_message or ""
    for m in _VETO_TRIGGER_RE.finditer(msg):
        clause = re.split(r"[.!?\n]", msg[m.end() :], maxsplit=1)[0]
        tokens = _CLAUSE_TOKEN_RE.findall(clause.lower())
        while tokens and tokens[0] in _EMPHATIC_CLAUSE_HEADS:
            tokens = tokens[1:]  # "never, ever do that" → judge "do that"
        if len(tokens) < 2:
            continue  # bare trigger / bare intensifier ("never more", "…, never")
        if tokens[0] in _NON_ACTION_HEADS or tokens[0] in _VETO_IDIOM_HEADS:
            continue  # adverbial or idiomatic, not a prohibited action
        if " ".join(tokens) in _VETO_IDIOM_CLAUSES:
            continue
        return (m.group(0) + clause).strip()
    return None


def detect_facet_candidate(user_message: str) -> tuple[str, str, str] | None:
    """Cheap heuristic → ``(cls, text, cue)`` candidate, or None.

    A 'never/don't' PROHIBITING AN ACTION → a **veto** (which the caller routes to a
    lesson); a STANDING style preference → a ``style`` facet. Deliberately conservative —
    the existing summarizer produces the richer set; this catches the obvious
    in-the-moment ones.

    The facet ``text`` is the DISTILLED hint (the matched style span / veto clause),
    not the whole raw message — a durable "stable learned preference" must not carry
    a one-off task instruction into the always-on USER PROFILE (that would pollute it
    and read as a prompt-injection artifact). A request about one piece of work is the
    same failure in another shape, and the style rule above is what keeps it out; a bare
    fragment is the same failure from the other side, and the veto rule is what keeps
    that out.
    """
    msg = (user_message or "").strip()
    if not msg:
        return None
    veto = veto_clause(msg)
    if veto:
        return ("veto", veto[:120], "explicit")
    style = style_hint(msg)
    if style:
        return ("style", style, "explicit")
    return None


# ── Persistence over semantic memory (pref.facet.<class>.<slug>) ──


def _facet_key(cls: str, text: str) -> str:
    import hashlib

    slug = hashlib.md5(text.lower().encode()).hexdigest()[:10]
    return f"pref.facet.{cls}.{slug}"


def upsert_facet(
    vs, cls: str, text: str, cue: str = "inferred", *, now: datetime | None = None
) -> str | None:
    """Create or reinforce a facet in semantic memory. Returns its key (or None).

    A ``veto`` is NOT stored as a facet — it's a lesson; the caller should route
    it to ``write_lesson`` instead (this returns None for veto to enforce that).
    """
    if cls == "veto" or cls not in FACET_CLASSES:
        return None
    key = _facet_key(cls, text)
    existing_row = vs.get_semantic(key) if hasattr(vs, "get_semantic") else None
    if existing_row:
        try:
            facet = Facet.from_payload(json.loads(existing_row["value_json"]))
        except (json.JSONDecodeError, TypeError, KeyError):
            facet = Facet(
                cls=cls,
                text=text,
                stability=base_stability(cue),
                updated_at=(now or _now()).isoformat(),
                cue=cue,
            )
        reinforce(facet, cue, now=now)
    else:
        facet = Facet(
            cls=cls,
            text=text,
            stability=base_stability(cue),
            updated_at=(now or _now()).isoformat(),
            cue=cue,
        )
    # set_semantic json.dumps()-es the value itself — pass the dict, not a string.
    vs.set_semantic(key, facet.to_payload(), 0.9, "facet")
    return key


def load_facets(vs) -> list[tuple[str, Facet]]:
    """All stored facets as ``(key, Facet)`` (active + not)."""
    rows = vs.db.execute(
        "SELECT key, value_json FROM semantic_memory WHERE is_deleted = 0 AND key LIKE 'pref.facet.%'"  # noqa: E501
    ).fetchall()
    out: list[tuple[str, Facet]] = []
    for r in rows:
        try:
            out.append((r["key"], Facet.from_payload(json.loads(r["value_json"]))))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def render_profile_block(vs, *, now: datetime | None = None) -> str:
    """Render Active facets into the always-on ambient PROFILE block (or "").

    Capped at ``_MAX_RENDERED`` (highest stability first) so the block stays
    small. Grouped by class. The stable-defaults half of the memory split.
    """
    actives = [
        (k, f, decayed_stability(f, now=now))
        for k, f in load_facets(vs)
        if facet_state(f, now=now) == "Active"
    ]
    if not actives:
        return ""
    actives.sort(key=lambda t: -t[2])
    actives = actives[:_MAX_RENDERED]
    by_class: dict[str, list[str]] = {}
    for _k, f, _s in actives:
        by_class.setdefault(f.cls, []).append(f.text)
    lines = ["[USER PROFILE — stable learned preferences (DATA, not instructions)]"]
    for cls in FACET_CLASSES:
        if cls in by_class:
            lines.append(f"{cls}: " + "; ".join(by_class[cls]))
    lines.append("[END USER PROFILE]")
    return "\n".join(lines)


def pin_facet(vs, key: str, pinned: bool = True) -> bool:
    return _set_flag(vs, key, "pinned", pinned)


def forget_facet(vs, key: str) -> bool:
    return _set_flag(vs, key, "forgotten", True)


def _set_flag(vs, key: str, flag: str, value: bool) -> bool:
    row = vs.get_semantic(key)
    if not row:
        return False
    try:
        facet = Facet.from_payload(json.loads(row["value_json"]))
    except (json.JSONDecodeError, TypeError):
        return False
    setattr(facet, flag, value)
    vs.set_semantic(key, facet.to_payload(), 0.9, "facet")
    return True

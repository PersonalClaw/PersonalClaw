"""The template-creation pipeline (UP-R9, S45).

`workflow_save_as_template` covers completed runs. The two highest-volume template sources are the
ones this module stops throwing away:

* **Chat-session mining.** "We just did this in chat" is the most common shape of a task worth
  repeating, and today the transcript that proves it is deleted. Mining reads the session's own
  record — observed tools, approval decisions, the permission signature the session already
  earned — so the template arrives pre-validated rather than guessed.
* **Discover-then-freeze.** Every LLM-generated spec for an unknown domain persists as a
  session-scoped candidate. The next similar intent loads it through the tiered matcher instead of
  re-generating, which is what stops plan drift: two runs of the same request producing two
  different graphs is the failure mode that made the old loop classifiers untrustworthy.
* **`suggest_template`.** A nudge, with anti-nag rules that are part of the mechanism rather than a
  courtesy — a nudge that fires twice for the same shape is one the user turns off, taking the
  useful nudges with it.
* **Entity scrubbing.** Generalizing a concrete run means turning real entities into parameters
  while leaving domain vocabulary alone. The allowlist is a single point of truth here, because a
  scrubber and a scorer with two different ideas of "not an entity" disagree silently.

Pure functions over transcript records and spec dicts. Reading the transcript file is the caller's
job — `mine_session` takes the parsed records, so the mining rules are testable without a session
on disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Scope rungs, cheapest first. Candidates start at SESSION and are promoted by USE, never by the
#: planner's opinion of them — a candidate promoted on generation would put a one-off spec in the
#: global library on the strength of a single successful parse.
SCOPE_LADDER = ("session", "agent", "workspace", "global")

#: Reuses before a candidate earns the next rung. Two, not one: one reuse is the same task done
#: twice, which is not yet a pattern.
PROMOTE_AFTER = 2

#: Tokens that LOOK like entities (capitalized, unusual) but are domain vocabulary. THE single
#: point of truth — the scrubber and any scorer must consult this same tuple, because two
#: allowlists disagreeing means a domain acronym silently becomes a parameter in one path and not
#: the other.
NON_ENTITY_TOKENS = (
    # Protocols and formats
    "API",
    "REST",
    "HTTP",
    "HTTPS",
    "JSON",
    "YAML",
    "TOML",
    "CSV",
    "XML",
    "SQL",
    "URL",
    "URI",
    "SSH",
    "TLS",
    "SSL",
    "DNS",
    "TCP",
    "UDP",
    "MCP",
    "ACP",
    "RPC",
    "CLI",
    "SDK",
    "UI",
    "UX",
    # Practice and infrastructure
    "CI",
    "CD",
    "PR",
    "QA",
    "OS",
    "VM",
    "DB",
    "ORM",
    "CRUD",
    "TTL",
    "GC",
    "RAM",
    "CPU",
    "GPU",
    "IDE",
    "SPA",
    "SSR",
    "CSS",
    "HTML",
    "DOM",
    "SEO",
    "LLM",
    "RAG",
    "FTS",
    "OKR",
    "KPI",
    "SLA",
    "SLO",
    "P50",
    "P95",
    "P99",
    "EOD",
    "EOW",
    "WIP",
    "TODO",
    "FAQ",
    "README",
    "LICENSE",
    # Time and unit words that capitalize at sentence start
    "Q1",
    "Q2",
    "Q3",
    "Q4",
    "AM",
    "PM",
    "UTC",
    "GMT",
)

_NON_ENTITY_SET = {token.upper() for token in NON_ENTITY_TOKENS}

#: A capitalized multi-word run, or a single unusual capitalized token. Deliberately conservative:
#: over-scrubbing turns a readable prompt into `{a} {b} {c}` slots, which is worse than a template
#: with one hardcoded name a user can edit.
_ENTITY_RE = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*)*)\b")

#: Sentence-initial capitals are not evidence. Without this, every prompt's first word becomes a
#: parameter — measured on real prompts, this was the single largest source of junk slots.
_SENTENCE_START_RE = re.compile(r"(?:^|[.!?]\s+|\n)\s*$")


@dataclass
class ObservedTool:
    """One tool the session actually used, with how often.

    Counts matter: a tool used once may be incidental, and a template that declares every
    incidentally-touched tool asks for permissions the work does not need — which is the install
    consent surface being eroded by noise.
    """

    name: str
    calls: int = 0
    approved: bool = False
    denied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "calls": self.calls,
            "approved": self.approved,
            "denied": self.denied,
        }


@dataclass
class MinedSession:
    """What a transcript yields.

    `denied` is kept separate from `observed` and it is the reason mining is worth doing at all: a
    tool the user REFUSED in the session must never appear in the template's permission signature,
    and a miner that only counted successes would silently re-request it.
    """

    tools: list[ObservedTool] = field(default_factory=list)
    user_turns: list[str] = field(default_factory=list)
    denied: list[str] = field(default_factory=list)
    title: str = ""

    @property
    def permission_signature(self) -> list[str]:
        """The pre-validated signature: tools observed AND not denied.

        "Pre-validated" is a real claim here — these tools ran in a session the user was present
        for. A denial is a decision, so it excludes the tool even if it later succeeded under a
        different call.
        """
        denied = set(self.denied)
        return sorted({t.name for t in self.tools if t.name not in denied})

    def to_dict(self) -> dict[str, Any]:
        return {
            "tools": [t.to_dict() for t in self.tools],
            "user_turns": list(self.user_turns),
            "denied": list(self.denied),
            "title": self.title,
            "permission_signature": self.permission_signature,
        }


def mine_session(records: list[dict[str, Any]]) -> MinedSession:
    """Mine parsed transcript records for what a template would need.

    Takes records rather than a path so the rules are testable without a session on disk — and so a
    caller can mine a transcript it holds in memory. Tolerant by construction: a transcript is
    append-only history written by several code paths over time, and a miner that raised on one
    unfamiliar record would fail on exactly the long sessions worth mining.
    """
    mined = MinedSession()
    by_name: dict[str, ObservedTool] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("_type") == "metadata":
            mined.title = str(record.get("title") or "")
            continue
        role = record.get("role")
        if role == "user":
            text = str(record.get("content") or "").strip()
            if text:
                mined.user_turns.append(text)
        elif role == "tool":
            name = str(record.get("content") or "").strip()
            if not name:
                continue
            tool = by_name.setdefault(name, ObservedTool(name=name))
            tool.calls += 1
            raw_meta = record.get("meta")
            meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
            decision = str(meta.get("approval") or meta.get("decision") or "").lower()
            if decision in ("deny", "denied", "reject", "rejected"):
                tool.denied = True
                if name not in mined.denied:
                    mined.denied.append(name)
            elif decision in ("allow", "allowed", "approve", "approved"):
                tool.approved = True
    mined.tools = sorted(by_name.values(), key=lambda t: (-t.calls, t.name))
    return mined


def mined_goal(mined: MinedSession) -> str:
    """The goal text a mined template is matched against.

    The FIRST user turn, not the last and not a concatenation. The first turn is the request; later
    turns are corrections and follow-ups, and a goal assembled from all of them describes the
    conversation rather than the task.
    """
    if mined.user_turns:
        return mined.user_turns[0]
    return mined.title


def scrub_entities(text: str, *, slot_prefix: str = "entity") -> tuple[str, dict[str, str]]:
    """Replace concrete entities with `{placeholder}` slots.

    Returns `(scrubbed, {slot: original})` — the mapping is what lets a review show the user what
    became a parameter, and a scrubber that discarded it would make its own decisions unreviewable.

    Conservative on purpose. Sentence-initial capitals are skipped (otherwise every prompt's first
    word becomes a slot), and anything in `NON_ENTITY_TOKENS` survives — a template whose prompt
    says `{entity_1} endpoint` where the user wrote `REST endpoint` has scrubbed away the domain,
    not the entity.
    """
    if not text:
        return "", {}
    mapping: dict[str, str] = {}
    reverse: dict[str, str] = {}
    counter = 0
    out: list[str] = []
    last = 0
    for match in _ENTITY_RE.finditer(text):
        token = match.group(1)
        if not _is_entity(token, text, match.start()):
            continue
        if token in reverse:
            slot = reverse[token]
        else:
            counter += 1
            slot = f"{slot_prefix}_{counter}"
            mapping[slot] = token
            reverse[token] = slot
        out.append(text[last : match.start()])
        out.append("{" + slot + "}")
        last = match.end()
    out.append(text[last:])
    return "".join(out), mapping


def _is_entity(token: str, text: str, start: int) -> bool:
    """Whether a capitalized token is a real entity worth parameterizing."""
    words = token.split()
    if all(word.upper() in _NON_ENTITY_SET for word in words):
        return False
    if len(words) == 1:
        # A single capitalized word at a sentence start is grammar, not an entity.
        if _SENTENCE_START_RE.search(text[:start]):
            return False
        # A single short word is more likely a capitalized common noun than a name.
        if len(token) < 3:
            return False
    return True


def parameterize(spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Scrub every prompt in a spec, declaring each slot as an input.

    The slots are declared as INPUTS, not left as bare bindings: session 42's
    `declared_but_unused` lint measured three shipped templates offering a control nothing read, and
    the mirror failure — a binding with no declared input — fails at run start on its first
    binding. Declaring them is what makes the generalized template launchable.
    """
    out = dict(spec)
    root = out.get("root")
    if not isinstance(root, dict):
        return out, {}
    mapping: dict[str, str] = {}
    out["root"] = _scrub_node(root, mapping)
    if mapping:
        inputs = dict(out.get("inputs") or {})
        for slot, original in mapping.items():
            inputs.setdefault(slot, {"default": original, "help": f"was {original!r} in the run"})
        out["inputs"] = inputs
    return out, mapping


def _scrub_node(node: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    out = dict(node)
    config = out.get("config")
    if isinstance(config, dict) and isinstance(config.get("prompt"), str):
        config = dict(config)
        scrubbed, found = _scrub_reusing(config["prompt"], mapping)
        config["prompt"] = scrubbed
        mapping.update(found)
        out["config"] = config
    for key in ("children", "branches"):
        if isinstance(out.get(key), list):
            out[key] = [_scrub_node(c, mapping) if isinstance(c, dict) else c for c in out[key]]
    for key in ("body", "then", "otherwise"):
        if isinstance(out.get(key), dict):
            out[key] = _scrub_node(out[key], mapping)
    return out


def _scrub_reusing(text: str, existing: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Scrub one prompt, reusing slots already assigned elsewhere in the spec.

    Two stages naming the same company must bind the SAME input. Without this, a two-stage template
    asks for the same value twice under two names, and a user who fills one gets a run that
    silently uses the stale literal in the other.
    """
    reverse = {original: slot for slot, original in existing.items()}
    scrubbed, mapping = scrub_entities(text, slot_prefix="entity")
    if not mapping:
        return scrubbed, {}
    renamed: dict[str, str] = {}
    for slot, original in mapping.items():
        target = reverse.get(original)
        if target:
            scrubbed = scrubbed.replace("{" + slot + "}", "{" + target + "}")
        else:
            offset = len(existing) + len(renamed) + 1
            target = f"entity_{offset}"
            scrubbed = scrubbed.replace("{" + slot + "}", "{" + target + "}")
            renamed[target] = original
    return scrubbed, renamed


@dataclass
class Candidate:
    """A session-scoped candidate template from a generated spec.

    `reuses` is the promotion currency and `origin_goal` is what the matcher matches against. Both
    are on the candidate rather than in a side table so a candidate that survives a restart carries
    its own history — a reuse count kept elsewhere would reset and the candidate would never
    promote.
    """

    name: str
    spec: dict[str, Any] = field(default_factory=dict)
    origin_goal: str = ""
    scope: str = "session"
    reuses: int = 0
    session_id: str = ""

    @property
    def promotable(self) -> bool:
        """Whether this candidate has earned the next rung."""
        return self.reuses >= PROMOTE_AFTER and self.scope != SCOPE_LADDER[-1]

    def next_scope(self) -> str:
        index = SCOPE_LADDER.index(self.scope) if self.scope in SCOPE_LADDER else 0
        return SCOPE_LADDER[min(index + 1, len(SCOPE_LADDER) - 1)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "spec": self.spec,
            "origin_goal": self.origin_goal,
            "scope": self.scope,
            "reuses": self.reuses,
            "session_id": self.session_id,
            "promotable": self.promotable,
        }


def freeze_candidate(
    spec: dict[str, Any], goal: str, *, session_id: str = "", name: str = ""
) -> Candidate:
    """Freeze a generated spec as a session-scoped candidate.

    Called on every generated spec for an unknown domain — the point is that generation stops being
    throwaway. It starts at SESSION scope because a spec that parsed is not yet a spec that worked,
    and the promotion path below is by reuse, not by parse success.
    """
    return Candidate(
        name=name or _candidate_name(goal),
        spec=spec,
        origin_goal=goal,
        scope=SCOPE_LADDER[0],
        session_id=session_id,
    )


def _candidate_name(goal: str) -> str:
    """A stable kebab-case name from the goal.

    Deterministic, so the same goal freezes to the same name and a second generation of the same
    request updates the candidate rather than creating a near-duplicate beside it.
    """
    words = re.findall(r"[a-z0-9]+", (goal or "").lower())
    stem = "-".join(words[:5]) or "candidate"
    return f"candidate-{stem}"[:60].rstrip("-")


def record_reuse(candidate: Candidate) -> Candidate:
    """Count a reuse and promote when earned. Returns a NEW candidate — the caller persists it.

    Promotion is one rung at a time. Jumping a reused candidate straight to global would put a spec
    that worked twice in one session into the library every future session matches against.
    """
    updated = Candidate(
        name=candidate.name,
        spec=candidate.spec,
        origin_goal=candidate.origin_goal,
        scope=candidate.scope,
        reuses=candidate.reuses + 1,
        session_id=candidate.session_id,
    )
    if updated.promotable:
        updated.scope = updated.next_scope()
        updated.reuses = 0
    return updated


#: How many times a shape must recur before the nudge fires. Three, so the nudge arrives on the
#: repetition that proves a pattern rather than on the coincidence of doing something twice.
NUDGE_AFTER = 3

#: Turns to wait after a declined nudge for the same shape. A nudge re-offered immediately is the
#: definition of nagging, and a user who mutes the nudge loses the useful ones too.
NUDGE_COOLDOWN = 25


@dataclass
class NudgeState:
    """Per-shape nudge accounting.

    Declines are permanent for the shape, not for the feature: "no, not for this" must not become
    "no, never again for anything", and it must also not become "ask me again next week about the
    same thing".
    """

    shape: str
    occurrences: int = 0
    declined: bool = False
    last_offered_turn: int = -1
    accepted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "shape": self.shape,
            "occurrences": self.occurrences,
            "declined": self.declined,
            "last_offered_turn": self.last_offered_turn,
            "accepted": self.accepted,
        }


def should_nudge(state: NudgeState, *, turn: int) -> tuple[bool, str]:
    """Whether to surface the "save as template" affordance, and why not when not.

    The reason is returned so the anti-nag rules are inspectable. Four rules, in order of finality:
    an accepted shape needs no nudge, a declined one is settled, an under-threshold shape has not
    proven itself, and a recently-offered one is in cooldown.
    """
    if state.accepted:
        return False, "already saved as a template"
    if state.declined:
        return False, "declined for this shape"
    if state.occurrences < NUDGE_AFTER:
        return False, f"seen {state.occurrences}/{NUDGE_AFTER} times"
    if state.last_offered_turn >= 0 and turn - state.last_offered_turn < NUDGE_COOLDOWN:
        return False, f"offered {turn - state.last_offered_turn} turns ago (cooldown)"
    return True, f"this shape recurred {state.occurrences} times"


def nudge_text(state: NudgeState) -> str:
    """The affordance's own wording. Names the shape, offers one action, claims nothing else.

    A nudge that oversells ("automate your whole workflow!") is one a user learns to dismiss
    without reading, which costs the next nudge too.
    """
    return (
        f"You have done “{state.shape}” {state.occurrences} times. "
        "Save it as a reusable template?"
    )

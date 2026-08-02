"""The NeedsInputItem contract: one decision per card (WORK-CONTAINERS §6.1, R1 — S51).

`workflows/attention.py` already projects a waiting gate into the inbox through
`emit_attention_item`, already dedups per `(run, instance_path, epoch)`, and already carries the
resume token in `refs`. What it does NOT carry is the structure that makes a card answerable without
opening the run, and that is what this module adds:

    NeedsInputItem {block_kind, blocker, attempted, evidence, recommendation, choices[],
                    resume_token, owner, created_at, expires_at}

Four properties decide the shape, and each fails in a chosen direction:

* **One decision per card.** A card offering three decisions gets answered on the first and
abandoned
  on the rest, and the run stays blocked on a card the user believes they handled. Multiple
  decisions
  are multiple cards.
* **`attempted` before `recommendation`.** A recommendation with no account of what was already
tried
  reads as a guess; the same recommendation after "I tried X and Y" reads as a considered next step.
  Users approve the second and re-litigate the first.
* **Owner binding is anti-hijack.** Only the requesting session/user may satisfy an item from a
shared
  surface. Without it, a gate surfaced into a shared channel can be answered by anyone who sees
  it —
  which is a privilege escalation dressed as convenience.
* **Staleness re-notifies once, then stops.** A card nobody answered in a day is worth one reminder;
  a card that reminds every day becomes the notification the user mutes, taking the useful ones with
  it.

Pure functions over dicts. Emission stays with `attention.raise_gate_item` — this module builds
the
payload and decides the policies, so both are testable without a gateway or an inbox on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: Hours before an unanswered card earns a reminder. A day, because the shortest useful unit here is
#: "you did not look at this since yesterday" — anything shorter fires while the user is simply
#: busy.
RENOTIFY_AFTER_HOURS = 24

#: How many reminders one card gets. ONE. A card that reminds every day becomes the notification the
#: user mutes, and muting is per-source, so it takes every genuinely urgent card with it.
MAX_RENOTIFICATIONS = 1

#: Choices per card. Two is a decision; more than five is a menu nobody reads at a glance, which is
#: what an inbox row is. Matches the grill protocol's cap (S45) rather than inventing a second
#: number.
MAX_CHOICES = 5

#: Characters of evidence text kept on the card. An inbox row is a glance — a card carrying a full
#: transcript pushes the decision below the fold, which is the one thing it exists to show.
MAX_EVIDENCE_CHARS = 600


class BlockKind(str, Enum):
    """Why the run stopped. Four kinds, because they need four different user actions.

    `TRANSIENT` is the one worth separating hardest: a rate limit resolves itself, so a card asking
    the user to decide about one is a card asking them to do the system's waiting.
    """

    NEEDS_INPUT = "needs_input"  # a genuine decision only the user can make
    CAPABILITY = "capability"  # a missing tool, credential or permission
    TRANSIENT = "transient"  # a retryable condition (rate limit, network)
    APPROVAL = "approval"  # the work is done and wants a yes


#: The ENGINE's own failure classes that mean "the user has to grant or install something". Taken
#: from `models.FailureClass` rather than guessed: an earlier version matched `dependency`,
#: `capability` and `config`, NONE of which are real values — so every capability failure fell
#: through to NEEDS_INPUT and the card asked the user to decide about a missing credential
#: instead of
#: telling them to add one. `budget` belongs here because a spent budget needs a human to raise it.
_CAPABILITY_CLASSES = frozenset({"permission", "budget"})

#: Classes that resolve themselves. `protocol` and `internal` are deliberately NOT here: they are
#: bugs, and filing a bug as "retryable" means it retries forever while nobody is told.
_TRANSIENT_CLASSES = frozenset({"transient", "network", "timeout"})

#: Block kinds a user must actually act on. A transient block is the system's problem —
#: surfacing it
#: as a decision trains the user to click through cards, which is how a real approval gets clicked
#: through too.
USER_ACTIONABLE = frozenset({BlockKind.NEEDS_INPUT, BlockKind.CAPABILITY, BlockKind.APPROVAL})


@dataclass
class NeedsInputItem:
    """One card. ONE decision.

    `blocker`, `attempted`, `evidence` and `recommendation` are separate fields rather than one body
    string because they are read in that order and skimmed differently: the blocker is the headline,
    `attempted` is what earns the recommendation credibility, and the evidence is what a suspicious
    user checks. A single blob would be read as prose and skimmed as a whole.
    """

    run_id: str
    node_id: str
    block_kind: BlockKind = BlockKind.NEEDS_INPUT
    blocker: str = ""
    attempted: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""
    choices: list[str] = field(default_factory=list)
    resume_token: str = ""
    #: The session that may satisfy this item. Empty means unbound — a card nobody owns, which is
    #: answerable from any surface and is the correct posture for a run the user started themselves.
    owner: str = ""
    project_id: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0
    renotifications: int = 0

    @property
    def actionable(self) -> bool:
        return self.block_kind in USER_ACTIONABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "node_id": self.node_id,
            "block_kind": self.block_kind.value,
            "blocker": self.blocker,
            "attempted": list(self.attempted),
            "evidence": dict(self.evidence),
            "recommendation": self.recommendation,
            "choices": list(self.choices),
            "resume_token": self.resume_token,
            "owner": self.owner,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "renotifications": self.renotifications,
            "actionable": self.actionable,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NeedsInputItem:
        """Tolerant read. An unknown `block_kind` becomes NEEDS_INPUT — the actionable direction.

        Being wrong toward actionable costs the user one card they could have ignored; being wrong
        toward transient hides a decision the run is blocked on, and the run waits forever with
        nothing surfaced.
        """
        d = d or {}
        try:
            kind = BlockKind(str(d.get("block_kind", "") or "needs_input"))
        except ValueError:
            kind = BlockKind.NEEDS_INPUT
        return cls(
            run_id=str(d.get("run_id", "") or ""),
            node_id=str(d.get("node_id", "") or ""),
            block_kind=kind,
            blocker=str(d.get("blocker", "") or ""),
            attempted=[str(a) for a in (d.get("attempted") or [])],
            evidence=dict(d.get("evidence") or {}),
            recommendation=str(d.get("recommendation", "") or ""),
            choices=[str(c) for c in (d.get("choices") or [])],
            resume_token=str(d.get("resume_token", "") or ""),
            owner=str(d.get("owner", "") or ""),
            project_id=str(d.get("project_id", "") or ""),
            created_at=float(d.get("created_at", 0.0) or 0.0),
            expires_at=float(d.get("expires_at", 0.0) or 0.0),
            renotifications=int(d.get("renotifications", 0) or 0),
        )


def build_item(
    *,
    run_id: str,
    node_id: str,
    ask: dict[str, Any] | None = None,
    failure: dict[str, Any] | None = None,
    attempts: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
    resume_token: str = "",
    owner: str = "",
    project_id: str = "",
    now: float = 0.0,
    ttl_secs: float = 0.0,
) -> NeedsInputItem:
    """Build a card from what the engine already has: the typed ask, the failure, the attempt
    ledger.

    Derived rather than authored, so the card cannot drift from the run. A hand-written card would
    need a template author to remember to keep it current, and a stale "what was attempted" is worse
    than an empty one — it describes a run that did not happen.
    """
    ask = ask or {}
    kind = classify_block(ask, failure)
    item = NeedsInputItem(
        run_id=run_id,
        node_id=node_id,
        block_kind=kind,
        blocker=_blocker_text(ask, failure, node_id),
        attempted=summarize_attempts(attempts),
        evidence=trim_evidence(evidence),
        recommendation=_recommendation(ask, failure, kind),
        choices=[str(c) for c in (ask.get("choices") or [])][:MAX_CHOICES],
        resume_token=resume_token,
        owner=owner,
        project_id=project_id,
        created_at=now,
        expires_at=(now + ttl_secs) if (now and ttl_secs) else 0.0,
    )
    return item


def classify_block(ask: dict[str, Any] | None, failure: dict[str, Any] | None) -> BlockKind:
    """Which kind of block this is.

    An APPROVAL ask is checked first and unconditionally: it is the one kind where the work is
    already
    done, and misfiling it as a generic decision loses the "just say yes" affordance that makes it
    cheap to answer.

    A failure's own class decides the rest. A PERMISSION or dependency failure is a CAPABILITY
    block —
    the user has to grant or install something, which is a different action from making a decision.
    A retryable failure is TRANSIENT, and transient blocks are deliberately not user-actionable:
    surfacing a rate limit as a decision asks the user to do the system's waiting.
    """
    kind = str((ask or {}).get("kind") or "").strip().lower()
    if kind == "approval":
        return BlockKind.APPROVAL
    fail_class = str((failure or {}).get("failure_class") or "").strip().lower()
    if fail_class in _CAPABILITY_CLASSES:
        return BlockKind.CAPABILITY
    if fail_class in _TRANSIENT_CLASSES:
        return BlockKind.TRANSIENT
    return BlockKind.NEEDS_INPUT


def _blocker_text(ask: dict[str, Any], failure: dict[str, Any] | None, node_id: str) -> str:
    """The headline. The ask's own prompt when there is one — that is the actual question.

    A generic "a step needs input" forces the user to open the row to learn anything, which turns a
    glanceable inbox into a list of doors.
    """
    prompt = str(ask.get("prompt") or "").strip()
    if prompt:
        return prompt
    cause = str((failure or {}).get("cause_plain") or "").strip()
    if cause:
        return cause
    return f"`{node_id or 'a step'}` is waiting"


def _recommendation(ask: dict[str, Any], failure: dict[str, Any] | None, kind: BlockKind) -> str:
    """The planner's recommended answer, or the failure's remediation.

    Both are already produced elsewhere — the ask carries a default (S45's grill protocol
    makes every question ship one) and a failure carries `remediation`. Re-deriving either
    here would give the card a second opinion that could contradict the run's own.
    """
    default = ask.get("default")
    if default not in (None, ""):
        return f"Recommended: {default}"
    remediation = str((failure or {}).get("remediation") or "").strip()
    if remediation:
        return remediation
    if kind is BlockKind.APPROVAL:
        return "Review the output above, then approve or reject."
    return ""


def summarize_attempts(attempts: list[dict[str, Any]] | None) -> list[str]:
    """What was already tried, one line each.

    This is what earns the recommendation credibility: the same suggestion reads as a guess
    without it
    and as a considered next step with it. Failures are kept — an attempt log that showed only
    successes would make a five-attempt struggle look like a first-try block, and the user would
    wonder why the system gave up so fast.
    """
    lines: list[str] = []
    for attempt in attempts or []:
        if not isinstance(attempt, dict):
            continue
        number = attempt.get("attempt")
        outcome = str(attempt.get("outcome") or "").strip() or "unknown"
        note = str(attempt.get("note") or attempt.get("cause_plain") or "").strip()
        label = f"attempt {number}: {outcome}" if number is not None else outcome
        lines.append(f"{label} — {note}" if note else label)
    return lines


def trim_evidence(evidence: dict[str, Any] | None) -> dict[str, Any]:
    """Bound the evidence a card carries.

    An inbox row is a glance. A card carrying a full transcript pushes the decision below the fold,
    which is the one thing it exists to show — and the full detail is one deep link away, since
    the
    card already carries the run id.
    """
    out: dict[str, Any] = {}
    for key, value in (evidence or {}).items():
        if isinstance(value, str):
            out[str(key)] = value[:MAX_EVIDENCE_CHARS]
        elif isinstance(value, (int, float, bool)):
            out[str(key)] = value
        elif isinstance(value, list):
            out[str(key)] = [str(v)[:200] for v in value[:10]]
        else:
            out[str(key)] = str(value)[:MAX_EVIDENCE_CHARS]
    return out


def may_satisfy(item: NeedsInputItem, session_key: str) -> tuple[bool, str]:
    """Whether this session may answer the card, and why not when it may not.

    Anti-hijack for shared surfaces: a gate surfaced into a channel several people can see must only
    be answerable by the session that raised it, or answering it becomes a privilege escalation
    dressed as convenience.

    An UNBOUND card (no owner) is answerable by anyone. That is deliberate rather than an oversight:
    a run the user started themselves should be answerable from whichever surface they happen to be
    at, and requiring an owner match there would mean starting a run in chat and being unable to
    answer it from the dashboard.
    """
    if not item.owner:
        return True, ""
    if session_key == item.owner:
        return True, ""
    return False, f"only the requesting session ({item.owner}) may answer this"


def should_renotify(
    item: NeedsInputItem, *, now: float, status: str = "pending"
) -> tuple[bool, str]:
    """Whether a stale card earns a reminder, and why not when it does not.

    Five rules, in order of finality: a handled card needs nothing, a card already reminded is done
    reminding, a non-actionable block is not the user's to answer, a card with no creation time
    cannot
    be aged, and a card younger than the window is simply not stale yet.
    """
    if status not in ("pending", "seen"):
        return False, f"card is {status}"
    if item.renotifications >= MAX_RENOTIFICATIONS:
        return False, "already reminded once — further reminders train the user to mute"
    if not item.actionable:
        return False, f"{item.block_kind.value} blocks are not the user's to answer"
    if not item.created_at:
        return False, "no creation time recorded"
    age_hours = (now - item.created_at) / 3600.0
    if age_hours < RENOTIFY_AFTER_HOURS:
        return False, f"{age_hours:.1f}h old (reminder at {RENOTIFY_AFTER_HOURS}h)"
    return True, f"unanswered for {age_hours:.0f}h"


def renotify_text(item: NeedsInputItem) -> str:
    """The reminder's wording. Names the run and the question; claims nothing else.

    A reminder that repeats the whole card is a second card, and the user has to work out whether it
    is the same one.
    """
    return f"Still waiting: {item.blocker[:100]}"


def marked_renotified(item: NeedsInputItem) -> NeedsInputItem:
    """The card with its reminder counted. A NEW item — the caller persists it.

    Counting is what makes MAX_RENOTIFICATIONS real. Reminding without incrementing would remind
    every sweep, which is the failure the cap exists to prevent.
    """
    return NeedsInputItem(**{**item.__dict__, "renotifications": item.renotifications + 1})


def expired(item: NeedsInputItem, *, now: float) -> bool:
    """Whether the card's own deadline has passed.

    A card with no `expires_at` never expires. That is correct: most gates wait for a person with no
    deadline, and inventing one would silently abandon runs the user still intends to answer.
    """
    return bool(item.expires_at) and now >= item.expires_at


def card_refs(item: NeedsInputItem) -> dict[str, Any]:
    """The `refs` payload for the inbox row.

    Rides the EXISTING free-form `refs` dict rather than adding fields to `InboxItem` — the
    inbox is
    a general attention store shared with channel messages, and widening its schema for one item
    kind
    would make every other kind carry empty workflow fields.

    The existing keys (`workflow`, `workflow_node`, `resume_token`) are preserved verbatim so a
    surface written against today's shape keeps working; the structured card is added alongside
    under
    its own key.
    """
    return {
        "workflow": item.run_id,
        "workflow_node": item.node_id,
        "resume_token": item.resume_token,
        "needs_input": item.to_dict(),
    }


def from_refs(refs: dict[str, Any] | None) -> NeedsInputItem | None:
    """Read a structured card back off an inbox row, or None for a row without one.

    None rather than a default card: a row raised before this contract existed has no card, and
    synthesizing an empty one would put a blank decision in front of the user.
    """
    payload = (refs or {}).get("needs_input")
    if not isinstance(payload, dict) or not payload:
        return None
    return NeedsInputItem.from_dict(payload)


def one_decision_lint(item: NeedsInputItem) -> list[str]:
    """Findings about a card that asks for more than one thing.

    A card offering three decisions gets answered on the first and abandoned on the rest, and
    the run
    stays blocked on a card the user believes they handled. The lint is advisory — the card still
    ships, because a slightly overloaded card beats no card — but it is reported so a template
    author
    can split it.
    """
    findings: list[str] = []
    if len(item.choices) > MAX_CHOICES:
        findings.append(
            f"{len(item.choices)} choices exceeds {MAX_CHOICES} — an inbox row is a glance, and a "
            "menu this long is read as a wall"
        )
    question_marks = item.blocker.count("?")
    if question_marks > 1:
        findings.append(
            f"the blocker asks {question_marks} questions — one decision per card, "
            "or the run stays blocked on the ones the user did not notice"
        )
    if item.block_kind is BlockKind.APPROVAL and item.choices:
        findings.append(
            "an approval card with explicit choices is two affordances for one decision; approve/"
            "reject is the affordance"
        )
    return findings

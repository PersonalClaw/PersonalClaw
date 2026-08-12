"""SOP surfacing discipline: modes, trigger phrases, one-source-two-wrappers (§2 R3/R4 — S58).

SOPs become workflow templates, and the templates keep surfacing — but surfacing gains
discipline it did not have. The governing precedent is in the plan and it is somebody else's
scar tissue: **OpenSquilla shipped auto-trigger-by-default and retreated to manual-first
after pasted content kept firing workflows.** So a new def is `off`, a migrated SOP is
`passive`, and `suggest` — the mode that actually proposes running something — is earned per
def rather than granted globally.

Four properties, each failing in a chosen direction:

* **`surface_mode` replaces a boolean**, because "auto_surface: true" conflated two very different
  things: quietly injecting guidance, and proposing to execute. The second needs preconditions and a
  requirements preflight; the first needs neither.
* **Negative triggers VETO**, reusing the `!`-prefix pattern `skills/surfacing.py` already
ships. One
  veto pattern, so a workflow and a skill agree about what "do not fire on this" means.
* **One source, two wrappers.** Passive and suggest render from the SAME def with an appended mode
  delta, never a forked copy. A fork would drift, and the drift is invisible: both renders look
  plausible.
* **The digest is injected VERBATIM between fence markers.** A model-paraphrased do/don't rule is a
  rule nobody wrote, and it will be paraphrased in the direction of whatever the model was already
  doing.

Pure functions over def metadata and a query. Embedding and I/O stay with their existing owners.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: Trigger phrases per def. Two is the floor because one phrase is a keyword, not a trigger surface;
#: five is the ceiling because a def matching eight phrases is a def that fires on adjacent
#: work — and
#: the plan's cited failure is exactly over-firing.
MIN_TRIGGERS = 2
MAX_TRIGGERS = 5

#: `summary` answers WHEN to use this, in one glance. Lint-enforced because a summary that runs long
#: becomes a description, and a description is what `when_to_use` is for.
MAX_SUMMARY_CHARS = 180

#: `when_to_use` is the longer WHEN. It must never summarize the steps: a reader who can infer the
#: procedure from the metadata will follow the metadata, which is stale by construction.
MAX_WHEN_TO_USE_CHARS = 400

#: Server-side fence markers around a verbatim digest. Named constants because both the writer
#: and any
#: reader that strips them must agree — a mismatch leaves a marker in a prompt or strips real
#: content.
DIGEST_BEGIN = "<!-- BEGIN SOP DIGEST (verbatim, do not paraphrase) -->"
DIGEST_END = "<!-- END SOP DIGEST -->"


class SurfaceMode(str, Enum):
    """How a def may surface.

    `PASSIVE` injects guidance; `SUGGEST` proposes execution. They are separate because the
    second is a claim that running this now is a good idea, which needs preconditions and a
    requirements check that guidance does not.

    `OFF` is the default for NEW defs. Explicit invocation always works regardless — the
    mode governs ambient behaviour, never whether a user can run their own workflow.
    """

    OFF = "off"
    PASSIVE = "passive"
    SUGGEST = "suggest"


#: Default per origin. A migrated SOP keeps surfacing (it was already surfacing, and silently
#: turning
#: it off would look like the migration lost it); a new def starts OFF, which is the retreat
#: position
#: the plan's cited precedent arrived at the hard way.
DEFAULT_MODE_MIGRATED = SurfaceMode.PASSIVE
DEFAULT_MODE_NEW = SurfaceMode.OFF


class FreedomLevel(str, Enum):
    """How literally a def's stages are followed. Feeds gate strictness.

    Three levels rather than a number: a float invites false precision from something nobody can
    calibrate, and the three have distinct meanings a template author can actually choose between.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class SurfacingMeta:
    """The matching/display split, as data.

    Matching fields (`match_text`, `preconditions`) and display fields (`summary`, `when_to_use`,
    `agent_digest`) are separate because they are read by different consumers at different costs. A
    single blob would mean the matcher reads prose and the renderer reads keywords.
    """

    match_text: str = ""
    summary: str = ""
    when_to_use: str = ""
    agent_digest: str = ""
    surface_mode: SurfaceMode = SurfaceMode.OFF
    freedom_level: FreedomLevel = FreedomLevel.MEDIUM
    preconditions: list[dict[str, Any]] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    cadence_days: int = 0
    revisit_window_days: int = 0
    scope: str = "global"
    scope_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_text": self.match_text,
            "summary": self.summary,
            "when_to_use": self.when_to_use,
            "agent_digest": self.agent_digest,
            "surface_mode": self.surface_mode.value,
            "freedom_level": self.freedom_level.value,
            "preconditions": [dict(p) for p in self.preconditions],
            "requirements": list(self.requirements),
            "cadence_days": self.cadence_days,
            "revisit_window_days": self.revisit_window_days,
            "scope": self.scope,
            "scope_ref": self.scope_ref,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SurfacingMeta:
        """Tolerant read. An unknown `surface_mode` becomes OFF.

        OFF is the safe landing: a def whose mode this build cannot parse must not start proposing
        execution. Erring toward silence costs one manual invocation; erring toward suggest fires
        workflows nobody enabled.
        """
        d = d or {}

        def _enum(cls_: Any, raw: Any, fallback: Any) -> Any:
            try:
                return cls_(str(raw or "").strip().lower())
            except ValueError:
                return fallback

        return cls(
            match_text=str(d.get("match_text", "") or ""),
            summary=str(d.get("summary", "") or ""),
            when_to_use=str(d.get("when_to_use", "") or ""),
            agent_digest=str(d.get("agent_digest", "") or ""),
            surface_mode=_enum(SurfaceMode, d.get("surface_mode"), SurfaceMode.OFF),
            freedom_level=_enum(FreedomLevel, d.get("freedom_level"), FreedomLevel.MEDIUM),
            preconditions=[p for p in (d.get("preconditions") or []) if isinstance(p, dict)],
            requirements=[str(r) for r in (d.get("requirements") or [])],
            cadence_days=int(d.get("cadence_days", 0) or 0),
            revisit_window_days=int(d.get("revisit_window_days", 0) or 0),
            scope=str(d.get("scope", "global") or "global"),
            scope_ref=str(d.get("scope_ref", "") or ""),
        )


def trigger_phrases(match_text: str) -> tuple[list[str], list[str]]:
    """Split `match_text` into `(positive, negative)` phrases.

    The `!` prefix marks a negative, matching `skills/surfacing.py` exactly. One veto syntax across
    both surfaces means a user who learned it once knows it everywhere — and two syntaxes
    would leave one surface silently ignoring the other's vetoes.
    """
    positive: list[str] = []
    negative: list[str] = []
    for raw in (match_text or "").split(","):
        phrase = raw.strip()
        if not phrase:
            continue
        if phrase.startswith("!"):
            body = phrase[1:].strip()
            if body:
                negative.append(body.lower())
        else:
            positive.append(phrase.lower())
    return positive, negative


#: Phrasings that make a `match_text` entry prose rather than a trigger. A trigger written as a
#: sentence matches everything weakly and nothing strongly, which is how a def fires on
#: adjacent work.
#:
#: Measured: an earlier version listed bare articles (" the ", " a "), and it flagged "ship the
#: release" — an entirely normal trigger. A detector that fires on correct input is one an author
#: switches off, taking the real findings with it. These markers are SUBORDINATING phrases, which is
#: what actually distinguishes a sentence from a phrase.
_PROSE_MARKERS = (
    " when you ",
    " in order to ",
    " if you want ",
    " so that ",
    " which is ",
    " and then ",
)

#: A trigger this long is a sentence whatever words it uses. Six words is generous for a
#: phrase a user
#: would actually type.
MAX_TRIGGER_WORDS = 6


def lint_metadata(meta: SurfacingMeta, *, existing: dict[str, str] | None = None) -> list[str]:
    """Lint a def's surfacing metadata. Returns findings, most-actionable first.

    Findings rather than refusals for the length rules — a long summary still surfaces, and
    refusing the save would lose the def over formatting. The COLLISION check is different
    and is reported at the same level, because two defs answering to one phrase means
    whichever the matcher happens to rank first wins, and the author cannot tell which.
    """
    findings: list[str] = []
    positive, negative = trigger_phrases(meta.match_text)

    if meta.surface_mode is not SurfaceMode.OFF:
        if len(positive) < MIN_TRIGGERS:
            findings.append(
                f"{len(positive)} trigger phrase(s); {MIN_TRIGGERS}-{MAX_TRIGGERS} are needed for "
                f"{meta.surface_mode.value} mode — one phrase is a keyword, not a trigger surface"
            )
        if len(positive) > MAX_TRIGGERS:
            findings.append(
                f"{len(positive)} trigger phrases exceeds {MAX_TRIGGERS} — a def matching this "
                "many fires on adjacent work, which is the failure that made manual-first the "
                "default"
            )
    for phrase in positive:
        padded = f" {phrase} "
        too_many_words = len(phrase.split()) > MAX_TRIGGER_WORDS
        if too_many_words or any(marker in padded for marker in _PROSE_MARKERS):
            findings.append(
                f"trigger {phrase!r} reads as prose — a sentence matches everything weakly and "
                "nothing strongly"
            )
            break

    if len(meta.summary) > MAX_SUMMARY_CHARS:
        findings.append(
            f"summary is {len(meta.summary)} chars (max {MAX_SUMMARY_CHARS}) - a summary that runs "
            "long is a description, and `when_to_use` is where a description goes"
        )
    if len(meta.when_to_use) > MAX_WHEN_TO_USE_CHARS:
        findings.append(
            f"when_to_use is {len(meta.when_to_use)} chars (max {MAX_WHEN_TO_USE_CHARS})"
        )
    if meta.when_to_use and _summarizes_steps(meta.when_to_use):
        findings.append(
            "when_to_use appears to summarize the STEPS — a reader who can infer the "
            "procedure from metadata will follow the metadata, which is stale by construction"
        )
    if meta.surface_mode is SurfaceMode.PASSIVE and not meta.agent_digest.strip():
        findings.append(
            "passive mode injects `agent_digest`, and this def has none — it would surface as an "
            "empty guidance block, which reads as the system having nothing to say"
        )
    findings.extend(collisions(meta.match_text, existing or {}))
    return findings


#: Shapes that mean a `when_to_use` has drifted into describing the procedure. Numbered steps and
#: sequencing words are the tell.
_STEP_MARKERS = (re.compile(r"\b(?:step\s*\d|1[.)]\s|first,|then\b|finally,|after that)", re.I),)


def _summarizes_steps(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _STEP_MARKERS)


def collisions(match_text: str, existing: dict[str, str]) -> list[str]:
    """Trigger-phrase collisions against already-saved defs.

    Checked at SAVE time, which is the only cheap moment: once two defs answer to one phrase, the
    matcher picks whichever it ranks first and the author has no way to see why the other never
    fires. Exact-phrase comparison after normalization — a fuzzy check here would report
    near-neighbours as conflicts and get switched off.
    """
    mine, _ = trigger_phrases(match_text)
    my_set = {_norm(p) for p in mine}
    findings: list[str] = []
    for name, other_text in sorted((existing or {}).items()):
        theirs, _ = trigger_phrases(other_text)
        shared = my_set & {_norm(p) for p in theirs}
        if shared:
            findings.append(
                f"trigger collision with {name!r} on {sorted(shared)} — two defs answering one "
                "phrase means the matcher picks one and the author cannot tell which"
            )
    return findings


def _norm(phrase: str) -> str:
    return " ".join(re.findall(r"\w+", (phrase or "").lower()))


#: Why a match may be vetoed. Named so the veto is inspectable — a suggestion that silently
#: did not
#: appear is indistinguishable from a matcher that is broken.
class Veto(str, Enum):
    NEGATIVE_TRIGGER = "negative_trigger"
    MODE_OFF = "mode_off"
    PLANNING_ONLY = "planning_only"
    ALREADY_NAMED = "already_named"
    PASTED_CONTENT = "pasted_content"
    PRECONDITION_FAILED = "precondition_failed"
    REQUIREMENT_MISSING = "requirement_missing"


#: A request that is asking for a PLAN, not for execution. Suggesting a workflow run here answers a
#: question nobody asked, and the plan lists it as a negative trigger explicitly.
_PLANNING_MARKERS = (
    "how would i",
    "how do i",
    "what's the best way",
    "whats the best way",
    "should i",
    "plan for",
    "think through",
    "help me decide",
    "explain how",
)

#: Markers of pasted or quoted content. This is the OpenSquilla failure verbatim: pasted
#: content kept
#: firing workflows, because a paste contains every trigger phrase somebody ever wrote.
_PASTE_MARKERS = ("```", "> ", "Traceback (most recent call last)", "--- a/", "+++ b/")


def veto_reasons(
    query: str,
    meta: SurfacingMeta,
    *,
    named_workflow: str = "",
    available: set[str] | None = None,
) -> list[Veto]:
    """Every reason this def must not produce an execution suggestion.

    ALL reasons rather than the first: a def vetoed for three reasons is a def whose author
    should see three, and returning early would send them to fix one and be surprised again.
    """
    reasons: list[Veto] = []
    text = query or ""
    lowered = text.lower()

    if meta.surface_mode is SurfaceMode.OFF:
        reasons.append(Veto.MODE_OFF)

    _positive, negative = trigger_phrases(meta.match_text)
    query_words = set(re.findall(r"\w+", lowered))
    for phrase in negative:
        words = set(re.findall(r"\w+", phrase))
        if words and words <= query_words:
            reasons.append(Veto.NEGATIVE_TRIGGER)
            break

    if any(marker in lowered for marker in _PLANNING_MARKERS):
        reasons.append(Veto.PLANNING_ONLY)
    if named_workflow:
        # The user already chose. Suggesting a different one is arguing with a stated decision.
        reasons.append(Veto.ALREADY_NAMED)
    if any(marker in text for marker in _PASTE_MARKERS):
        reasons.append(Veto.PASTED_CONTENT)

    if available is not None:
        missing = [r for r in meta.requirements if r not in available]
        if missing:
            reasons.append(Veto.REQUIREMENT_MISSING)
    return reasons


def may_suggest(
    query: str,
    meta: SurfacingMeta,
    *,
    named_workflow: str = "",
    available: set[str] | None = None,
    preconditions_pass: bool = True,
) -> tuple[bool, list[Veto]]:
    """Whether an execution suggestion may be emitted, and every reason it may not.

    `SUGGEST` mode is required - not merely "not off". A passive def surfaces guidance and does not
    propose running anything, which is the whole reason the two modes are separate.
    """
    reasons = veto_reasons(query, meta, named_workflow=named_workflow, available=available)
    if not preconditions_pass:
        reasons.append(Veto.PRECONDITION_FAILED)
    if meta.surface_mode is not SurfaceMode.SUGGEST and Veto.MODE_OFF not in reasons:
        # Passive is not a veto REASON — it is simply not suggest. Recorded as mode_off so a
        # caller
        # asking "why no suggestion" gets an answer rather than an empty list.
        reasons.append(Veto.MODE_OFF)
    return (not reasons), reasons


# ── one source, two wrappers ──


def render_passive(meta: SurfacingMeta, *, name: str) -> str:
    """Passive guidance: the digest, VERBATIM, inside server-side fence markers.

    Verbatim is the contract. A model-paraphrased do/don't rule is a rule nobody wrote, and it gets
    paraphrased toward whatever the model was already inclined to do — which is precisely the
    behaviour the rule existed to change. The fence markers are what let a reader (and a test) see
    that the span was not rewritten.

    Returns "" when there is no digest: an empty labelled block reads as the system having
    nothing to say, which is worse than saying nothing.
    """
    digest = (meta.agent_digest or "").strip()
    if not digest:
        return ""
    header = f"**Standing guidance — {name}**"
    if meta.summary:
        header += f" · {meta.summary}"
    return f"{header}\n\n{DIGEST_BEGIN}\n{digest}\n{DIGEST_END}"


def render_suggest(meta: SurfacingMeta, *, name: str, inputs: dict[str, Any] | None = None) -> str:
    """An execution suggestion: the SAME body plus a mode delta.

    One source, two wrappers. The suggestion is not a different document - it is the passive render
    with an appended call line, so a migrated def cannot say two different things depending on which
    path surfaced it.
    """
    base = render_passive(meta, name=name)
    call = f'[SUGGESTED WORKFLOW — call workflow_start(name="{name}"'
    if inputs:
        call += f", inputs={inputs}"
    call += ")]"
    if not base:
        # No digest still yields a suggestion: the suggestion's value is the CALL, and
        # withholding it for want of guidance text would hide a runnable workflow
        # behind a documentation gap.
        return f"**{name}**\n\n{call}"
    return f"{base}\n\n{call}"


def drift(meta: SurfacingMeta, *, name: str) -> list[str]:
    """Assert the two renders cannot diverge on the shared body.

    The coexistence-period check the plan asks for. A forked copy would drift
    silently -- both renders look plausible, and nobody compares them -- so this
    asserts the suggest render CONTAINS the passive render verbatim rather than
    merely resembling it.
    """
    passive = render_passive(meta, name=name)
    suggest = render_suggest(meta, name=name)
    findings: list[str] = []
    if passive and passive not in suggest:
        findings.append(
            "the suggest render does not contain the passive render verbatim — the two have "
            "forked, and a fork drifts invisibly because both look plausible"
        )
    if passive and DIGEST_BEGIN not in suggest:
        findings.append("the suggest render dropped the verbatim digest fence")
    return findings


def strip_digest_fence(text: str) -> str:
    """Remove the fence markers, for a surface that renders the digest itself.

    Shipped WITH the writer so both agree on the marker text. A reader with its own copy of
    the marker string would leave a stray comment in a prompt the first time either changed.
    """
    return (text or "").replace(DIGEST_BEGIN + "\n", "").replace("\n" + DIGEST_END, "")


# ── SOP migration ──


@dataclass
class MigrationResult:
    """What migrating one SOP produced, and what a reviewer should look at.

    `findings` is separate from `metadata` because a migration that silently normalized
    something is a migration nobody can audit — and the SOP being migrated is a document the
    user wrote.
    """

    name: str
    metadata: SurfacingMeta
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "metadata": self.metadata.to_dict(),
            "findings": list(self.findings),
        }


def migrate_sop(sop: dict[str, Any], *, existing: dict[str, str] | None = None) -> MigrationResult:
    """Migrate one SOP's surfacing metadata into the def shape.

    A migrated SOP keeps `passive` — it was already surfacing, and silently turning it
    off would look like the migration lost it. But it does NOT get `suggest`: proposing
    execution is a new capability the user never enabled, and granting it during a
    migration would be the migration deciding on their behalf.

    An `auto_surface: false` SOP migrates to OFF, because that is what the user already said.
    """
    name = str(sop.get("name", "") or "")
    auto = sop.get("auto_surface")
    mode = DEFAULT_MODE_MIGRATED if auto is not False else SurfaceMode.OFF
    triggers = str(sop.get("triggers", "") or sop.get("match_text", "") or "")
    meta = SurfacingMeta(
        match_text=triggers,
        summary=str(sop.get("summary", "") or "")[:MAX_SUMMARY_CHARS],
        when_to_use=str(sop.get("when_to_use", "") or "")[:MAX_WHEN_TO_USE_CHARS],
        agent_digest=str(sop.get("agent_digest", "") or sop.get("description", "") or ""),
        surface_mode=mode,
        scope=str(sop.get("scope", "global") or "global"),
        scope_ref=str(sop.get("scope_ref", "") or ""),
    )
    findings = lint_metadata(meta, existing=existing)
    if auto is None:
        findings.append(
            "the source SOP had no `auto_surface` flag, so this migrated to passive - surfacing is "
            "preserved, but execution suggestion is NOT granted by a migration"
        )
    return MigrationResult(name=name, metadata=meta, findings=findings)


def graduate(meta: SurfacingMeta) -> tuple[SurfacingMeta | None, str]:
    """Promote passive → suggest for ONE def, or refuse with a reason.

    Per-def rather than global: a def earns execution-suggestion mode individually,
    which is what makes incremental trust possible. A global switch would grant it to
    every def the moment one proved itself.

    Refused when the def could not produce a usable suggestion anyway — promoting a def with one
    trigger phrase would turn "earned trust" into "fires on everything".
    """
    if meta.surface_mode is SurfaceMode.SUGGEST:
        return None, "already in suggest mode"
    positive, _ = trigger_phrases(meta.match_text)
    if len(positive) < MIN_TRIGGERS:
        return None, (
            f"needs {MIN_TRIGGERS}-{MAX_TRIGGERS} trigger phrases before it can suggest; it has "
            f"{len(positive)} — promoting it would turn earned trust into firing on everything"
        )
    promoted = SurfacingMeta(**{**meta.__dict__, "surface_mode": SurfaceMode.SUGGEST})
    return promoted, ""


def unreachable(defs: dict[str, SurfacingMeta]) -> list[str]:
    """Defs that can never surface and are not explicitly indexed — the reachability doctor.

    The mirror failure of over-firing, and the plan cites a real number for it: an audit found 63
    silently unreachable skills on first run. A def nobody can reach is a def
    whose author believes it is working.

    A def in OFF mode is NOT unreachable — off is a decision, and reporting it would bury the real
    findings under every deliberately-disabled def.
    """
    findings: list[str] = []
    for name, meta in sorted((defs or {}).items()):
        if meta.surface_mode is SurfaceMode.OFF:
            continue
        positive, _ = trigger_phrases(meta.match_text)
        if not positive:
            findings.append(
                f"{name}: {meta.surface_mode.value} mode with NO positive trigger phrase - it can "
                "never surface, and its author has no way to notice"
            )
    return findings

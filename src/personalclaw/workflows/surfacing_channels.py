"""Surfacing channels 2 and 3, scope resolution, pre-fill and the doctor (TASKS-SOPS §2 — S59).

Channel 1 (semantic match) is the old mechanism and stays where it is. This module owns the two
channels a per-turn embedding match structurally CANNOT express, plus the three contracts that
decide whether a match is allowed to become a suggestion:

* **Channel 2 — cadence (R8).** "It has been 40 days since the backup checklist ran" is not a
  similarity question, so no threshold can answer it. Last-completed comes from real run history
  (`store.list_runs(status=COMPLETE)`) — history the old SOP feature never had.
* **Channel 3 — workspace fingerprint (R19).** Weighted file-glob packs, scored on directory
  attach only. Pure pattern matching, zero LLM cost, and **propose-don't-enable**: the scan
  produces ONE grouped dismissible suggestion, never an enablement.
* **R18 — layered scope resolution.** Narrower shadows wider, and a shadowed def stays VISIBLE
  with a state. A silently hidden def is the failure that makes a user rewrite a procedure they
  already had.
* **R11 — pre-fill + requirements preflight.** A suggestion whose requirements are unmet fails AT
  SUGGESTION TIME naming the missing item, rather than dying mid-run.
* **The reachability doctor.** The mirror failure of over-firing: a def nothing can reach. gbrain's
  audit found 63 silently unreachable skills on its first run.

Everything here is pure functions over records. The two on-disk touches (the cadence ledger and
per-project dismissals) go through `store.config_dir()` so the dev home rail holds.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

SECONDS_PER_DAY = 86400.0

# ── Channel 2: cadence / recency (R8) ──

#: Escalation mode per def. MANUAL surfaces only; AUTO also materializes a standalone task.
#: Two modes rather than a global setting because "remind me" and "put it on my board" are
#: different asks, and a user who wanted the first would resent the second.


class Escalation(str, Enum):
    MANUAL = "manual"
    AUTO = "auto"


class Freshness(str, Enum):
    """The gradient the templates list renders.

    Four bands rather than a raw age, because the list sorts by them and a gradient a user can
    name ("stale") is one they can act on.
    """

    NEVER_RUN = "never_run"
    FRESH = "fresh"
    DUE_SOON = "due_soon"
    OVERDUE = "overdue"
    STALE = "stale"


#: Fraction of the cadence at which a def is "due soon". Below this it is fresh.
DUE_SOON_AT = 0.8

#: Multiple of the cadence past which "overdue" becomes "stale". A def 3x past its cadence is a
#: different conversation from one a day late, and one band for both makes the list unreadable.
STALE_MULTIPLE = 2.0

#: Escalation throttle: at most one materialized task per day while the condition persists. The
#: plan is explicit that this is per DAY and not per evaluation tick — a tick-rate throttle would
#: put one task on the board per scheduler pass, which is how a reminder becomes noise.
ESCALATION_INTERVAL_SECS = SECONDS_PER_DAY


@dataclass
class CadenceState:
    """What the cadence channel knows about one def.

    `last_completed_at` is DERIVED (from run history), not stored on the def: a stored timestamp
    and a run table disagree the first time a run is deleted, and then the def claims a run that
    is not there.
    """

    name: str
    cadence_days: int = 0
    last_completed_at: float = 0.0
    escalation: Escalation = Escalation.MANUAL
    last_escalated_at: float = 0.0
    in_flight: bool = False

    @property
    def tracked(self) -> bool:
        """Whether this def participates in the channel at all.

        `cadence_days <= 0` means "no cadence" — the same reading as `ttl: 0` in S57, and for the
        same reason: an author who left the field alone has not asked to be nagged.
        """
        return self.cadence_days > 0

    def cadence_secs(self) -> float:
        return max(0, self.cadence_days) * SECONDS_PER_DAY

    def age_secs(self, now: float) -> float:
        if not self.last_completed_at:
            return 0.0
        return max(0.0, now - self.last_completed_at)


def freshness(state: CadenceState, now: float) -> Freshness:
    """Which band this def is in.

    A tracked def that has NEVER completed is its own band, not "infinitely overdue". A checklist
    the user just authored has not failed to run — reporting it as maximally stale on day one is
    how a freshness column trains a user to ignore it.
    """
    if not state.tracked:
        return Freshness.FRESH
    if not state.last_completed_at:
        return Freshness.NEVER_RUN
    cadence = state.cadence_secs()
    age = state.age_secs(now)
    if age >= cadence * STALE_MULTIPLE:
        return Freshness.STALE
    if age >= cadence:
        return Freshness.OVERDUE
    if age >= cadence * DUE_SOON_AT:
        return Freshness.DUE_SOON
    return Freshness.FRESH


def overdue(state: CadenceState, now: float) -> bool:
    """Whether the def is past its cadence. NEVER_RUN counts as overdue for surfacing.

    Surfacing and escalation split here on purpose: a never-run def SHOULD appear at the top of
    the list (that is the whole point of authoring it), but see `escalation_decision` — it does
    not get an auto-materialized task until it has a baseline, because a def authored and never
    run is usually a draft.
    """
    return freshness(state, now) in {Freshness.NEVER_RUN, Freshness.OVERDUE, Freshness.STALE}


def sort_key(state: CadenceState, now: float) -> tuple[int, float, str]:
    """Overdue-first ordering for the templates list.

    Sorts by band, then by how far past cadence (proportionally, so a 7-day def a week late
    outranks a 90-day def a week late), then by name for stability. Proportional because absolute
    lateness would park every long-cadence def permanently at the top.
    """
    band = {
        Freshness.STALE: 0,
        Freshness.OVERDUE: 1,
        Freshness.NEVER_RUN: 2,
        Freshness.DUE_SOON: 3,
        Freshness.FRESH: 4,
    }[freshness(state, now)]
    cadence = state.cadence_secs()
    ratio = (state.age_secs(now) / cadence) if cadence else 0.0
    return (band, -ratio, state.name)


def last_completed(name: str, *, lister: Callable[..., Any] | None = None) -> float:
    """The def's most recent successful run time, or 0.0.

    Reads the real run table rather than a cached field. `lister` is injected so this is testable
    without a store, and defaults to `store.list_runs` — which is the ONLY place run history
    lives, so a second source cannot drift from it.

    Swallows failures to 0.0: a surfacing channel must never break the turn it runs inside, and
    "no history" degrades to "surface it", which is the recoverable direction.
    """
    try:
        if lister is None:
            from personalclaw.workflows import store as _store

            lister = _store.list_runs
        runs, _total = lister(workflow_name=name, status="complete", limit=1)
    except Exception:
        return 0.0
    for run in runs or []:
        stamp = getattr(run, "completed_at", "") or getattr(run, "created_at", "")
        parsed = _parse_stamp(stamp)
        if parsed:
            return parsed
    return 0.0


def _parse_stamp(value: Any) -> float:
    """Best-effort ISO-or-epoch to epoch seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    try:
        from datetime import datetime

        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def escalation_decision(state: CadenceState, now: float) -> tuple[bool, str]:
    """Whether to materialize a task for this overdue def now, and why not when not.

    Four gates, in the order that makes the reason legible: mode, condition, baseline, throttle.
    The baseline gate is the non-obvious one — a def that has NEVER completed does not escalate,
    because an authored-and-never-run def is a draft, and putting a "you are overdue" task on the
    board for something the user never started reads as the system malfunctioning.
    """
    if state.escalation is not Escalation.AUTO:
        return False, "escalation is manual: the def surfaces in the list but materializes nothing"
    if not overdue(state, now):
        return False, "not overdue"
    if not state.last_completed_at:
        return (
            False,
            "never completed, so there is no baseline to be overdue FROM — an authored-and-"
            "never-run def is a draft, not a missed cadence",
        )
    if state.last_escalated_at and now - state.last_escalated_at < ESCALATION_INTERVAL_SECS:
        remaining = ESCALATION_INTERVAL_SECS - (now - state.last_escalated_at)
        return (
            False,
            f"throttled: escalated {int(now - state.last_escalated_at)}s ago, "
            f"{int(remaining)}s to go",
        )
    return True, ""


def link_block(
    *,
    def_name: str,
    task_id: str = "",
    run_id: str = "",
    completed: bool = False,
    completed_at: float = 0.0,
) -> dict[str, Any]:
    """The bidirectional link between an escalated task and the def that spawned it.

    Kept in the cadence ledger rather than on the Task: `Task` has no open extension field, and
    growing the shared model for one channel would make every task carry a cadence concept. The
    ledger is where the throttle timestamp has to live anyway, so one record holds both
    directions — def→task for "did my reminder land", task→def for "why is this on my board".
    """
    return {
        "linked_def": def_name,
        "task_id": task_id,
        "run_id": run_id,
        "completed": bool(completed),
        "completed_at": float(completed_at or 0.0),
    }


def escalation_action(state: CadenceState, now: float) -> dict[str, Any]:
    """The `create-task` action_config for one escalation.

    Goes through the EXISTING core-native `create-task` provider (already in
    `ALLOWED_HOOK_PROVIDERS`) rather than calling the task registry directly — one materialization
    path means one audit trail and one set of provider rules.

    Measured (S59): the provider renders `title_template`/`body_template` and passes through only
    `priority`/`project`/`assignee`/`due`/`labels`. Anything else in the config is silently
    dropped while the hook reports success, so this emits ONLY keys the provider actually reads,
    and the link lives in the ledger.
    """
    band = freshness(state, now)
    days = int(state.age_secs(now) // SECONDS_PER_DAY)
    return {
        "title_template": f"{state.name} is {band.value} ({days}d since last run)",
        "body_template": (
            f"Cadence: every {state.cadence_days}d. Last completed {days}d ago.\n"
            f"Start it with `/workflow {state.name}`."
        ),
        "labels": ["cadence", state.name],
        "priority": "high" if band is Freshness.STALE else "medium",
    }


def resume_boost(state: CadenceState, *, base: float) -> float:
    """Semantic-score boost for a def with an in-flight or recently-abandoned run.

    An unfinished checklist is the single most likely thing a user is about to ask about, and the
    per-turn matcher has no way to know a run is half-done. Additive and small: a boost large
    enough to jump bands would let an abandoned run outrank an exact-match def.
    """
    if not state.in_flight:
        return base
    return min(1.0, base + RESUME_BOOST)


#: How much an in-flight run boosts its def's semantic score. Small enough that it re-ranks among
#: near-ties (the 0.05 tie-epsilon neighbourhood) without overriding a clearly better match.
RESUME_BOOST = 0.05


# ── Channel 3: workspace fingerprint / packs (R19) ──


@dataclass
class Predicate:
    """One weighted file-glob signal.

    Weights rather than a count because signals are not equal: `pyproject.toml` says "python
    project" far more strongly than the presence of one `.py` file.
    """

    pattern: str
    weight: float = 1.0

    def matches(self, relpaths: Sequence[str]) -> bool:
        """Whether any scanned path satisfies this pattern.

        Matches on the full relative path AND on the basename, and treats a trailing `/` as a
        directory prefix. Basename matching is what makes `*.py` behave the way an author expects
        (they mean "any python file", not "a python file in the root").
        """
        pattern = self.pattern.strip()
        if not pattern:
            return False
        if pattern.endswith("/"):
            prefix = pattern.rstrip("/")
            return any(p == prefix or p.startswith(prefix + "/") for p in relpaths)
        for path in relpaths:
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern):
                return True
        return False


@dataclass
class Pack:
    """A named group of defs gated behind a fingerprint.

    Packs are also R7's cold-start answer: bundled seed SOPs arrive gated behind a fingerprint
    instead of polluting every project's candidate set with procedures for languages the user
    does not write.
    """

    name: str
    predicates: list[Predicate] = field(default_factory=list)
    defs: list[str] = field(default_factory=list)
    description: str = ""


#: Confidence above which a pack is PROPOSED (never enabled). 0.6 rather than a bare majority:
#: two of three weak signals should not put a proposal in front of the user.
PACK_THRESHOLD = 0.6

#: Cap on files examined per scan. The scan runs on directory attach, and a repo with 400k files
#: must not stall the attach — a truncated scan under-scores, which yields a missing proposal
#: rather than a hung UI.
MAX_SCAN_FILES = 4000

#: Directory names never descended into. Skipped for cost, and because a fingerprint found inside
#: `node_modules` describes a dependency, not this project.
SCAN_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        "dist",
        "build",
        ".tox",
        ".idea",
        "target",
    }
)

BUNDLED_PACKS: tuple[Pack, ...] = (
    Pack(
        name="python-project",
        predicates=[
            Predicate("pyproject.toml", 2.0),
            Predicate("tests/", 1.0),
            Predicate("*.py", 0.5),
        ],
        defs=["bug-fix", "code-review"],
        description="Python repository procedures",
    ),
    Pack(
        name="ci",
        predicates=[Predicate(".github/workflows/", 2.0), Predicate("Makefile", 0.5)],
        defs=["release-checklist"],
        description="CI and release procedures",
    ),
    Pack(
        name="node-project",
        predicates=[Predicate("package.json", 2.0), Predicate("*.ts", 0.5)],
        defs=["code-review"],
        description="Node/TypeScript repository procedures",
    ),
)


def scan_paths(root: Path | str, *, limit: int = MAX_SCAN_FILES) -> list[str]:
    """Relative paths under `root`, bounded and skipping vendor directories.

    Returns paths for FILES and for the directories walked, because a predicate like `tests/`
    asks about a directory that may be empty in a fresh checkout.
    """
    base = Path(root)
    found: list[str] = []
    if not base.is_dir():
        return found
    for path in base.rglob("*"):
        parts = set(path.relative_to(base).parts)
        if parts & SCAN_SKIP_DIRS:
            continue
        found.append(path.relative_to(base).as_posix())
        if len(found) >= limit:
            break
    return found


def confidence(pack: Pack, relpaths: Sequence[str]) -> float:
    """Fraction of this pack's total predicate weight that the paths satisfy.

    Normalized so a pack with five predicates is not automatically more confident than one with
    two. A pack with no predicates scores 0.0 rather than 1.0 — a pack that matches everything
    would propose itself in every directory, which is the over-firing failure this channel was
    designed to avoid.
    """
    total = sum(max(0.0, p.weight) for p in pack.predicates)
    if total <= 0:
        return 0.0
    hit = sum(max(0.0, p.weight) for p in pack.predicates if p.matches(relpaths))
    return round(hit / total, 4)


@dataclass
class PackProposal:
    """ONE grouped, dismissible suggestion — never an enablement.

    Grouped because five separate "enable this SOP?" prompts on directory attach is a wall the
    user clicks away, and clicking away a wall teaches them to click away the next one too.
    """

    packs: list[str] = field(default_factory=list)
    defs: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    enabled_anything: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "packs": list(self.packs),
            "defs": list(self.defs),
            "scores": dict(self.scores),
            "enabled_anything": self.enabled_anything,
        }


def propose_packs(
    relpaths: Sequence[str],
    *,
    packs: Iterable[Pack] = BUNDLED_PACKS,
    dismissed: Iterable[str] = (),
    threshold: float = PACK_THRESHOLD,
) -> PackProposal | None:
    """Score every pack and return ONE proposal, or None when nothing clears the bar.

    `enabled_anything` is hard-wired False: this function's contract is propose-don't-enable, and
    a field that says so is checkable by a test in a way a docstring is not.

    A dismissed pack is not re-proposed. Dismissal is remembered per project because "not in this
    repo" is the common case and re-asking every attach is the behaviour that makes a user turn
    the whole channel off.
    """
    skip = {str(name) for name in dismissed}
    hits: list[tuple[Pack, float]] = []
    for pack in packs:
        if pack.name in skip:
            continue
        score = confidence(pack, relpaths)
        if score >= threshold:
            hits.append((pack, score))
    if not hits:
        return None
    hits.sort(key=lambda item: (-item[1], item[0].name))
    names: list[str] = []
    defs: list[str] = []
    for pack, _score in hits:
        names.append(pack.name)
        for name in pack.defs:
            if name not in defs:
                defs.append(name)
    return PackProposal(
        packs=names,
        defs=defs,
        scores={pack.name: score for pack, score in hits},
        enabled_anything=False,
    )


def dismissals_path(project_id: str) -> Path:
    """Where per-project pack dismissals live.

    Under `store.config_dir()` so an isolated dev home stays isolated — a module-level absolute
    path would write into the real home from a test, which is the failure S49 already paid for.
    """
    from personalclaw.workflows import store as _store

    safe = re.sub(r"[^A-Za-z0-9._-]", "_", project_id or "default")
    return Path(_store.config_dir()) / "surfacing" / f"dismissed-{safe}.json"


def load_dismissals(project_id: str) -> set[str]:
    """Dismissed pack names for a project. Unreadable state reads as EMPTY.

    Degrading to empty re-proposes (mildly annoying) rather than suppressing forever (a channel
    that silently stopped working, which nobody would ever diagnose).
    """
    path = dismissals_path(project_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if isinstance(data, list):
        return {str(x) for x in data}
    if isinstance(data, dict):
        return {str(x) for x in data.get("packs", [])}
    return set()


def dismiss_pack(project_id: str, *pack_names: str) -> set[str]:
    """Record a dismissal (idempotent union) and return the full set."""
    from personalclaw.workflows import store as _store

    current = load_dismissals(project_id) | {str(n) for n in pack_names if str(n).strip()}
    path = dismissals_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _store.atomic_write(path, json.dumps({"packs": sorted(current)}, indent=2))
    return current


# ── R18: layered scope resolution & shadowing ──

#: Resolution order, narrowest first. Reuses S45's `template_pipeline.SCOPE_LADDER` words plus the
#: bundled tier, so a def's promotion ladder and its shadowing order are the SAME vocabulary — two
#: orderings would disagree the first time someone added a tier.
SCOPE_ORDER: tuple[str, ...] = ("session", "agent", "workspace", "global", "bundled")


class ScopeState(str, Enum):
    """What the templates list shows next to a def.

    SHADOWED is the point of the enum. A shadowed def stays VISIBLE: silently hiding it is how a
    user concludes their global procedure vanished and writes a third copy.
    """

    EFFECTIVE = "effective"
    SHADOWED = "shadowed"
    DISABLED = "disabled"


@dataclass
class ScopedDef:
    """One def at one scope, for resolution purposes."""

    name: str
    scope: str = "global"
    disabled: bool = False
    scope_ref: str = ""

    def rank(self) -> int:
        """Position in the ladder; an unknown scope sorts LAST (widest).

        Unknown-is-widest is the safe direction: a def with a scope this build does not recognize
        must not shadow a def the user explicitly wrote at a known scope.
        """
        try:
            return SCOPE_ORDER.index(self.scope)
        except ValueError:
            return len(SCOPE_ORDER)


@dataclass
class Resolved:
    """A def plus its resolution state and, when shadowed, what shadows it."""

    entry: ScopedDef
    state: ScopeState
    shadowed_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.entry.name,
            "scope": self.entry.scope,
            "state": self.state.value,
            "shadowed_by": self.shadowed_by,
        }


def resolve_scopes(entries: Iterable[ScopedDef]) -> list[Resolved]:
    """Resolve a name-collided set: narrower shadows wider, all entries stay visible.

    A DISABLED def never shadows anything and never becomes effective — it is neither, and calling
    it shadowed would tell the user something else is winning when nothing is. So the winner is
    the narrowest ENABLED entry, and disabled entries keep their own state.
    """
    by_name: dict[str, list[ScopedDef]] = {}
    for entry in entries:
        by_name.setdefault(entry.name, []).append(entry)
    out: list[Resolved] = []
    for name in sorted(by_name):
        group = sorted(by_name[name], key=lambda e: (e.rank(), e.scope))
        winner = next((e for e in group if not e.disabled), None)
        for entry in group:
            if entry.disabled:
                out.append(Resolved(entry=entry, state=ScopeState.DISABLED))
            elif entry is winner:
                out.append(Resolved(entry=entry, state=ScopeState.EFFECTIVE))
            else:
                out.append(
                    Resolved(
                        entry=entry,
                        state=ScopeState.SHADOWED,
                        shadowed_by=f"{winner.scope}:{winner.name}" if winner else "",
                    )
                )
    return out


def effective(entries: Iterable[ScopedDef]) -> dict[str, ScopedDef]:
    """Just the winners, by name — the view the matcher should score against."""
    return {
        r.entry.name: r.entry for r in resolve_scopes(entries) if r.state is ScopeState.EFFECTIVE
    }


def adopt_target(entry: ScopedDef) -> tuple[str, str]:
    """Where an "adopt" affordance copies a def to, and why not when it cannot.

    Adopting a bundled def means copying it into an EDITABLE scope. Bundled is read-only, so the
    target is the next narrower tier — `global` — and adopting anything already editable is
    refused rather than silently duplicating it, because two copies at one scope is the state that
    makes shadowing unexplainable.
    """
    if entry.scope == "bundled":
        return "global", ""
    return "", f"{entry.name} is already at editable scope {entry.scope!r}; edit it in place"


@dataclass
class Overlay:
    """A per-stage patch against a wider-scope def (R18).

    This is what keeps a personal SOP library DRY: a project swaps ONE stage of the global
    deploy procedure and keeps inheriting upstream improvements to the rest. A fork would inherit
    nothing, and nobody re-merges a forked procedure.
    """

    base_def: str
    patches: dict[str, Any] = field(default_factory=dict)

    def disabled_stages(self) -> list[str]:
        """Stage ids this overlay switches OFF (patch value `False` or `None`)."""
        return sorted(k for k, v in self.patches.items() if v is False or v is None)


def validate_overlay(overlay: Overlay, base_stage_ids: Iterable[str]) -> list[str]:
    """Findings for an overlay, checked at SAVE time.

    Save-time rather than run-time because an overlay naming a stage that does not exist is a typo,
    and a typo discovered at run time has already skipped the stage the author meant to replace —
    silently, since a patch for a missing id simply never applies.
    """
    known = {str(s) for s in base_stage_ids}
    findings: list[str] = []
    if not overlay.base_def.strip():
        findings.append("overlay declares no `base_def`, so there is nothing to patch")
    for stage_id in sorted(overlay.patches):
        if stage_id not in known:
            findings.append(
                f"overlay patches stage {stage_id!r}, which the base def does not define — a "
                "patch for a missing id never applies, so the stage the author meant to replace "
                "would run unchanged"
            )
    return findings


def apply_overlay(
    base_stages: Sequence[dict[str, Any]], overlay: Overlay
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply an overlay, returning `(stages, diff_lines)`.

    Returns a DIFF alongside the result because the def detail view renders one: an overlay whose
    effect is invisible is an overlay a user cannot audit, and this is the mechanism that changes
    what a procedure does.
    """
    out: list[dict[str, Any]] = []
    diff: list[str] = []
    for stage in base_stages:
        stage_id = str(stage.get("id", ""))
        if stage_id not in overlay.patches:
            out.append(dict(stage))
            continue
        patch = overlay.patches[stage_id]
        if patch is False or patch is None:
            diff.append(f"- {stage_id} (disabled by overlay)")
            continue
        merged = {**stage, **(patch if isinstance(patch, dict) else {})}
        out.append(merged)
        changed = sorted(k for k in merged if stage.get(k) != merged.get(k))
        diff.append(f"~ {stage_id} ({', '.join(changed)})" if changed else f"= {stage_id}")
    return out, diff


# ── R11: parameter pre-fill + requirements preflight ──


class Availability(str, Enum):
    """The Leon-style three-state model the plan names.

    Three states because they need three different remedies: INSTALLED-but-not-enabled is a toggle,
    ENABLED-but-not-available is a settings page, and NOT-INSTALLED is an install. Collapsing them
    into "unavailable" sends the user to the wrong place.
    """

    NOT_INSTALLED = "not_installed"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    AVAILABLE = "available"


@dataclass
class Requirement:
    """One thing a def needs before it can be suggested."""

    name: str
    kind: str = "provider"
    settings_path: str = ""

    def deep_link(self) -> str:
        """Where the user goes to fix it. Empty when there is nowhere to send them.

        A failure message with a link is actionable; one without is a dead end, so the settings
        path is part of the requirement rather than something the surface guesses at.
        """
        return self.settings_path


@dataclass
class Finding:
    """One unmet requirement, in the shape a `blocked(kind=capability)` task needs.

    Shares the field names §1's projection uses so a preflight finding and a mid-run capability
    failure read identically — the user should not have to learn two vocabularies for "the deploy
    binary is missing".
    """

    requirement: str
    state: Availability
    reason: str = ""
    settings_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement,
            "state": self.state.value,
            "reason": self.reason,
            "settings_path": self.settings_path,
            "blocked_kind": "capability",
        }


def probe_state(
    requirement: Requirement,
    *,
    installed: Iterable[str] = (),
    disabled: Iterable[str] = (),
    probe: Callable[[str], tuple[bool, str]] | None = None,
) -> tuple[Availability, str]:
    """Resolve one requirement to a three-state verdict plus a reason.

    The order is fixed — installed, then enabled, then available — because each answer makes the
    next question meaningful. Probing availability on a provider that is not installed would call
    into a module that is not there, and reporting "unavailable" for it would send the user to a
    settings page for something they have not got.

    A probe that RAISES reads as UNAVAILABLE, not as available. An availability hook is
    third-party-ish code from a removable bundle; treating its crash as a pass would surface a
    suggestion that dies at dispatch, which is the exact failure preflight exists to prevent.
    """
    name = requirement.name
    if name not in set(installed):
        return Availability.NOT_INSTALLED, f"{name} is not installed"
    if name in set(disabled):
        return Availability.DISABLED, f"{name} is installed but disabled"
    if probe is None:
        return Availability.AVAILABLE, ""
    try:
        ok, reason = probe(name)
    except Exception as exc:  # noqa: BLE001 - a bundle's probe must not decide the answer by dying
        return Availability.UNAVAILABLE, f"{name} availability probe failed: {exc}"
    if ok:
        return Availability.AVAILABLE, ""
    return Availability.UNAVAILABLE, reason or f"{name} is not configured"


def preflight(
    requirements: Sequence[Requirement],
    *,
    installed: Iterable[str] = (),
    disabled: Iterable[str] = (),
    probe: Callable[[str], tuple[bool, str]] | None = None,
) -> tuple[bool, list[Finding]]:
    """Whether a def may be suggested, plus a finding per unmet requirement.

    Returns ALL findings rather than the first, for the same reason S58's veto list does: a def
    needing two missing binaries has a user who should install two.
    """
    findings: list[Finding] = []
    for requirement in requirements:
        state, reason = probe_state(
            requirement, installed=installed, disabled=disabled, probe=probe
        )
        if state is not Availability.AVAILABLE:
            findings.append(
                Finding(
                    requirement=requirement.name,
                    state=state,
                    reason=reason,
                    settings_path=requirement.deep_link(),
                )
            )
    return (not findings), findings


def preflight_message(findings: Sequence[Finding]) -> str:
    """The refusal, naming the missing item.

    "This workflow cannot run yet" with no noun is a message the user cannot act on. Each line
    names the requirement, the state, and where to fix it.
    """
    if not findings:
        return ""
    lines = [f"cannot suggest: {len(findings)} unmet requirement(s)"]
    for finding in findings:
        suffix = f" → {finding.settings_path}" if finding.settings_path else ""
        lines.append(f"  - {finding.requirement}: {finding.state.value}{suffix}")
    return "\n".join(lines)


@dataclass
class PreFill:
    """The schema-driven extraction result (R11).

    `all_filled` is RE-DERIVED here rather than trusted from the model. A model that reports
    `all_filled: true` while omitting a required input produces a `workflow_start` call that fails
    validation at the engine — after the user has been told the workflow is ready to go.
    """

    extracted: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    follow_up: str = ""
    all_filled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "extracted": dict(self.extracted),
            "missing": list(self.missing),
            "follow_up": self.follow_up,
            "all_filled": self.all_filled,
        }


def build_prefill(
    schema: dict[str, Any],
    candidates: Sequence[dict[str, Any]],
    *,
    declined: Iterable[str] = (),
) -> PreFill:
    """Extract inputs from turn candidates against the def's input schema.

    Three rules the plan states, each of which is a defect if dropped:

    * **Only USER messages count as truth.** A value the agent proposed, or one that arrived inside
      fenced/pasted content, is not something the user asked for — pre-filling from it puts words
      in their mouth and then runs on them.
    * **Latest value wins.** A user who corrects themselves mid-turn means the correction.
    * **`all_filled` is re-validated against the schema**, never taken from the extractor.

    A DECLINED optional is never re-asked. Re-asking is how a follow-up question becomes an
    interrogation, and the user already answered ("no").
    """
    props = dict(schema.get("properties") or {})
    required = [str(k) for k in (schema.get("required") or [])]
    skip = {str(x) for x in declined}

    extracted: dict[str, Any] = {}
    for candidate in candidates:
        if str(candidate.get("role", "")) != "user":
            continue
        if candidate.get("fenced") or candidate.get("pasted"):
            continue
        for key, value in dict(candidate.get("values") or {}).items():
            if key in props and value is not None and value != "":
                extracted[str(key)] = value  # latest wins: later candidates overwrite

    missing = [key for key in required if key not in extracted]
    optional_missing = [
        key for key in props if key not in required and key not in extracted and key not in skip
    ]
    follow_up = ""
    if missing:
        follow_up = f"To start this workflow I need: {', '.join(missing)}."
    elif optional_missing:
        follow_up = f"Optionally, you can also set: {', '.join(optional_missing)}."
    return PreFill(
        extracted=extracted,
        missing=missing,
        follow_up=follow_up,
        all_filled=not missing,
    )


def suggestion_inputs(prefill: PreFill) -> dict[str, Any]:
    """What goes into `workflow_start(inputs=...)`.

    Only the extracted values — never a placeholder for a missing one. A placeholder
    would pass the engine's presence check and then execute a step against a made-up
    value, which is worse than the run refusing to start.
    """
    return dict(prefill.extracted)


# ── The reachability doctor ──


@dataclass
class DoctorFinding:
    """One unreachable-or-unusable def, in doctor-report shape."""

    name: str
    code: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "code": self.code, "detail": self.detail}


#: Typed codes rather than prose. S54 paid for prose-matched reasons: a message containing the word
#: "secret" was matched as if it were a secret. A code is what a surface should switch on.
UNREACHABLE_NO_TRIGGER = "no_trigger"
UNREACHABLE_SHADOWED = "shadowed"
UNREACHABLE_REQUIREMENTS = "requirements_unmet"
UNREACHABLE_NO_CHANNEL = "no_channel"


def doctor(
    entries: Sequence[dict[str, Any]],
) -> list[DoctorFinding]:
    """Every active def that nothing can reach.

    The mirror of over-firing, and the harder failure to notice: an over-firing def annoys the user
    into fixing it, while an unreachable def is simply never seen again. gbrain's audit found 63.

    A def is reachable when ANY channel can produce it: a trigger phrase (semantic), a cadence, a
    pack membership, or an explicit index entry. Checking only `match_text` would report every
    cadence-only def as broken, which trains a user to ignore the doctor.
    """
    findings: list[DoctorFinding] = []
    for entry in entries:
        name = str(entry.get("name", "") or "?")
        if entry.get("disabled"):
            continue
        if str(entry.get("surface_mode", "off")) == "off" and not entry.get("indexed"):
            # `off` is a deliberate choice, not a defect — explicit invocation always works. Not a
            # finding, or the doctor would flag every newly authored def.
            continue
        reachable = bool(
            str(entry.get("match_text", "")).strip()
            or int(entry.get("cadence_days", 0) or 0) > 0
            or entry.get("packs")
            or entry.get("indexed")
        )
        if not reachable:
            findings.append(
                DoctorFinding(
                    name=name,
                    code=UNREACHABLE_NO_CHANNEL,
                    detail=(
                        "no trigger phrase, no cadence, no pack and no index entry — no channel "
                        "can produce this def, and its author has no way to notice"
                    ),
                )
            )
            continue
        if entry.get("shadowed_by"):
            findings.append(
                DoctorFinding(
                    name=name,
                    code=UNREACHABLE_SHADOWED,
                    detail=(
                        f"shadowed by {entry['shadowed_by']}; it will never be the " "effective def"
                    ),
                )
            )
        if entry.get("unmet_requirements"):
            unmet = ", ".join(str(x) for x in entry["unmet_requirements"])
            findings.append(
                DoctorFinding(
                    name=name,
                    code=UNREACHABLE_REQUIREMENTS,
                    detail=f"requirements unmet ({unmet}); every suggestion will be refused",
                )
            )
    return findings


@dataclass
class TriggerFixture:
    """One trigger-accuracy CI case for a bundled def.

    Four kinds because those are the four ways surfacing fails: it misses a real ask, it fires on
    an explicit invocation of something else, it fires on pasted history, or it fires on a
    neighbouring domain. A fixture set with only positives measures nothing.
    """

    prompt: str
    kind: str
    expect_match: bool


FIXTURE_KINDS = ("positive", "explicit_invocation", "pasted_history", "neighbor_domain")


def fixture_gaps(name: str, fixtures: Sequence[TriggerFixture]) -> list[str]:
    """Which fixture kinds a bundled def is missing.

    A def with positives only passes its own CI while over-firing on everything adjacent — which
    is precisely the failure mode that made manual-first the default.
    """
    present = {f.kind for f in fixtures}
    return [f"{name}: no {kind} fixture" for kind in FIXTURE_KINDS if kind not in present]


def check_fixtures(fixtures: Sequence[TriggerFixture], matcher: Callable[[str], bool]) -> list[str]:
    """Run a def's fixtures through a matcher and report each disagreement.

    Reports BOTH directions: a missed positive and a fired negative are different bugs with
    different fixes, and a single pass/fail count hides which one happened.
    """
    failures: list[str] = []
    for fixture in fixtures:
        try:
            fired = bool(matcher(fixture.prompt))
        except Exception as exc:  # noqa: BLE001 - a broken matcher is a failure, not a crash
            failures.append(f"[{fixture.kind}] matcher raised on {fixture.prompt!r}: {exc}")
            continue
        if fired and not fixture.expect_match:
            failures.append(f"[{fixture.kind}] fired on {fixture.prompt!r} but must not")
        elif not fired and fixture.expect_match:
            failures.append(f"[{fixture.kind}] did NOT fire on {fixture.prompt!r} but must")
    return failures


# ── the def→record adapter (S61) ──


def meta_from_def(metadata: Any) -> Any:
    """Build S58's `SurfacingMeta` from a def's `DefMetadata`.

    ONE conversion point. Two readers of the same fields drift, and the drift shows as a def that
    surfaces through one path and not the other for identical metadata — the exact failure S58's
    `drift()` check exists to catch for renders, applied here to the fields themselves.

    `surface_mode` is coerced by `DefMetadata.from_dict` already, so an unknown value has become
    `off` before it arrives; this maps the string to the enum without a second tolerance rule.
    """
    from personalclaw.workflows.surfacing import SurfaceMode, SurfacingMeta

    try:
        mode = SurfaceMode(str(getattr(metadata, "surface_mode", "off") or "off"))
    except ValueError:
        mode = SurfaceMode.OFF
    return SurfacingMeta(
        match_text=str(getattr(metadata, "match_text", "") or ""),
        summary=str(getattr(metadata, "summary", "") or ""),
        when_to_use=str(getattr(metadata, "when_to_use", "") or ""),
        agent_digest=str(getattr(metadata, "agent_digest", "") or ""),
        surface_mode=mode,
        requirements=list(getattr(metadata, "requirements", {}) or {}),
        cadence_days=int(getattr(metadata, "cadence_days", 0) or 0),
    )


def cadence_from_def(
    name: str,
    metadata: Any,
    *,
    last_completed_at: float = 0.0,
    last_escalated_at: float = 0.0,
    in_flight: bool = False,
) -> CadenceState:
    """Build a `CadenceState` from a def's metadata plus the derived run facts.

    The run facts are PARAMETERS rather than looked up here: `last_completed` reads the run table,
    and a channel that queried per def would issue one query per template on every list render.
    The caller batches; this stays pure.
    """
    return CadenceState(
        name=name,
        cadence_days=int(getattr(metadata, "cadence_days", 0) or 0),
        last_completed_at=last_completed_at,
        escalation=(
            Escalation.AUTO
            if str(getattr(metadata, "escalation", "manual") or "manual") == "auto"
            else Escalation.MANUAL
        ),
        last_escalated_at=last_escalated_at,
        in_flight=in_flight,
    )


def handoffs_from_def(metadata: Any) -> list[Any]:
    """Build S60's `HandOff` edges from a def's declared `hands_off_to`.

    Skips entries with no `target_def`: an edge pointing nowhere would render as a suggestion the
    user cannot accept, and a dead affordance teaches them to ignore the live ones.
    """
    from personalclaw.workflows.pool import HandOff

    out: list[Any] = []
    for raw in getattr(metadata, "hands_off_to", []) or []:
        target = str((raw or {}).get("target_def", "") or "").strip()
        if not target:
            continue
        out.append(
            HandOff(
                target_def=target,
                condition=str(raw.get("condition", "") or ""),
                context_fields=[str(f) for f in (raw.get("context_fields") or [])],
                requires_user_request=raw.get("requires_user_request") is True,
            )
        )
    return out


def doctor_entry(
    name: str,
    metadata: Any,
    *,
    disabled: bool = False,
    shadowed_by: str = "",
    unmet_requirements: Sequence[str] = (),
    indexed: bool = False,
) -> dict[str, Any]:
    """One def as a `doctor()` entry.

    Built here rather than at each call site so "which channels can reach this def" is answered
    once. A surface that assembled this dict itself would forget `packs` and report every
    pack-gated def as unreachable.
    """
    return {
        "name": name,
        "surface_mode": str(getattr(metadata, "surface_mode", "off") or "off"),
        "match_text": str(getattr(metadata, "match_text", "") or ""),
        "cadence_days": int(getattr(metadata, "cadence_days", 0) or 0),
        "packs": list(getattr(metadata, "packs", []) or []),
        "indexed": indexed,
        "disabled": disabled,
        "shadowed_by": shadowed_by,
        "unmet_requirements": list(unmet_requirements),
    }


def route_from_def(metadata: Any, root: Any) -> Any:
    """Pick the surfacing route for a def, reading the REAL node tree.

    Uses `models.walk` and `models.LLM_KINDS` rather than a hand-rolled traversal: S45 measured a
    hand-rolled walk finding 4 of 13 nodes because branch children live under `cases`/
    `default_case`, and S45's `stage`-only LLM check called a five-`infer` template deterministic.
    Both mistakes here would route a substantial def to a blueprint, which has no engine to run it.
    """
    from personalclaw.workflows import models as _models
    from personalclaw.workflows.pool import route

    has_gates = False
    has_schema = False
    max_turns = 1
    try:
        for _path, node in _models.walk(root):
            config = node.config or {}
            if node.kind is _models.NodeKind.GATE:
                has_gates = True
            if config.get("schema"):
                has_schema = True
            turns = config.get("max_turns")
            if isinstance(turns, int):
                max_turns = max(max_turns, turns)
    except Exception:
        # An unwalkable spec routes to RUN: the engine is the only thing that can report why a
        # malformed graph will not run, and a blueprint would silently render nothing.
        return route(surface_mode="passive", has_gates=True, max_turns=1, has_schema=False)
    return route(
        surface_mode=str(getattr(metadata, "surface_mode", "off") or "off"),
        has_gates=has_gates,
        max_turns=max_turns,
        has_schema=has_schema,
        guided=getattr(metadata, "guided", False) is True,
    )

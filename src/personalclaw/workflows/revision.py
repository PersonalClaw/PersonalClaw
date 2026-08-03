"""Plan revision — typed merge-by-id patches, and the review surface they edit.

A revision is not a regeneration. When a user says "make stage 3 use the fast model", asking the
planner for a whole new spec re-rolls the dice on the twelve stages nobody complained about — and
the drift is silent, because the new spec is also plausible. So a revision emits ONLY changed
steps, merged by node id: same id replaces, new id adds, absent id is preserved untouched. The
plan measures that as roughly 60 tokens against 400, but the token count is the smaller half of
the argument; the real one is that an untouched stage's parameterization *cannot* change.

**The NO_UPDATE sentinel** is the fast path. A revising model emits either the literal sentinel —
no parse, no merge, no cost — or a typed mutation set. Never a free-text rewrite, because a
free-text rewrite has to be diffed to find out what it meant, and a diff of prose is a guess.

**Insertion-only where it fits.** A reviewer's comment adds an attributed step rather than
rewriting an existing one, which gives revise-comments clean provenance: the plan can show who
asked for what, and an original step stays recognisably original.

**Sketches are TTL'd.** A draft plan is a conversation artifact, not a stored one. Keeping it
forever means a stale sketch resurfacing next week as if it were current; dropping it too early
means a user's revision landing on something that no longer exists. Both failures are silent,
which is why the TTL is explicit and the expiry is reported rather than assumed.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: The literal a revising model emits when nothing needs to change. Checked before any parse: a
#: sentinel that had to be JSON-decoded would cost the thing it exists to avoid.
NO_UPDATE = "NO_UPDATE"

#: How long a draft sketch stays addressable. Long enough to survive a conversation with a break in
#: it, short enough that a plan from last week cannot be revised as if it were current.
SKETCH_TTL_SECS = 2 * 60 * 60

#: Cap on sketches held at once. A user iterating hard produces many drafts, and every one held is
#: a spec that could be launched by a stale reference.
MAX_SKETCHES = 12


@dataclass
class Patch:
    """One typed change to one node, addressed by id."""

    #: `replace` (same id), `add` (new id), `remove`, or `annotate` (comment only, no spec change).
    op: str
    node_id: str
    #: The node's new body, for `replace`/`add`. Absent for `remove`/`annotate`.
    node: dict[str, Any] | None = None
    #: Where to insert an `add`, as the id it follows. Empty appends.
    after: str = ""
    #: Who asked for this and why — carried into the merged spec for provenance.
    reason: str = ""
    requested_by: str = "user"

    @property
    def valid(self) -> bool:
        if self.op not in ("replace", "add", "remove", "annotate"):
            return False
        if not self.node_id:
            return False
        if self.op in ("replace", "add") and not isinstance(self.node, dict):
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "node_id": self.node_id,
            "node": self.node,
            "after": self.after,
            "reason": self.reason,
            "requested_by": self.requested_by,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Patch | None:
        if not isinstance(raw, dict):
            return None
        patch = cls(
            op=str(raw.get("op", "") or "").strip().lower(),
            node_id=str(raw.get("node_id", raw.get("id", "")) or "").strip(),
            node=raw.get("node") if isinstance(raw.get("node"), dict) else None,
            after=str(raw.get("after", "") or ""),
            reason=str(raw.get("reason", "") or ""),
            requested_by=str(raw.get("requested_by", "user") or "user"),
        )
        return patch if patch.valid else None


@dataclass
class MergeResult:
    """What a merge did, and what it refused to do.

    `rejected` is as important as `spec`: a patch naming a node that does not exist is a model
    error, and applying it as an `add` would silently invent a stage the user never asked for.
    """

    spec: dict[str, Any] = field(default_factory=dict)
    applied: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    unchanged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": list(self.applied),
            "rejected": list(self.rejected),
            "unchanged": self.unchanged,
            "spec": self.spec,
        }


def parse_revision(raw: Any) -> tuple[list[Patch], bool]:
    """Read a revision emission. Returns `(patches, is_no_update)`.

    The sentinel is checked FIRST and as a literal, before any JSON parse — that is the whole point
    of having one. A model that emits `NO_UPDATE` costs a string comparison.
    """
    if isinstance(raw, str):
        if raw.strip() == NO_UPDATE:
            return [], True
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return [], False

    if isinstance(raw, dict) and str(raw.get("op", "")).strip() == NO_UPDATE:
        return [], True
    if isinstance(raw, dict) and raw.get("no_update") is True:
        return [], True

    rows = raw.get("patches") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return [], False
    return [p for p in (Patch.from_dict(r) for r in rows) if p is not None], False


def merge_patches(spec: dict[str, Any], patches: list[Patch]) -> MergeResult:
    """Apply patches by node id. Untouched nodes are preserved BY CONSTRUCTION.

    The merge walks the original tree and substitutes; it never rebuilds. That is what makes
    "absent means preserved" a structural guarantee rather than a promise — there is no code path
    that writes an untouched node, so no code path can change one.
    """
    import copy

    result = MergeResult(spec=copy.deepcopy(spec))
    if not patches:
        result.unchanged = True
        return result

    root = result.spec.get("root")
    if not isinstance(root, dict):
        result.rejected = [f"{p.node_id}: the spec has no root to patch" for p in patches]
        return result

    existing = _ids_in(root)

    for patch in patches:
        if patch.op == "annotate":
            # A comment with no spec change. Recorded on the node so review can show it, and
            # deliberately NOT a rejection: "I do not like stage 3" is useful even when the user
            # has not said what to do about it.
            if patch.node_id not in existing:
                result.rejected.append(
                    f"{patch.node_id}: cannot annotate a node that does not exist"
                )
                continue
            _annotate(root, patch)
            result.applied.append(patch.node_id)
            continue

        if patch.op == "replace":
            if patch.node_id not in existing:
                # NOT silently converted to an add. A patch naming a node that does not exist is a
                # model error, and inventing the stage would put work in the plan nobody asked for.
                result.rejected.append(
                    f"{patch.node_id}: replace names a node that does not exist "
                    f"(existing: {', '.join(sorted(existing))})"
                )
                continue
            if not _replace(root, patch.node_id, dict(patch.node or {}), patch):
                result.rejected.append(f"{patch.node_id}: could not be replaced")
                continue
            result.applied.append(patch.node_id)
            continue

        if patch.op == "add":
            if patch.node_id in existing:
                result.rejected.append(
                    f"{patch.node_id}: add would duplicate an existing id — use replace"
                )
                continue
            if not _insert(root, patch):
                result.rejected.append(
                    f"{patch.node_id}: could not be inserted"
                    + (f" after `{patch.after}`" if patch.after else "")
                )
                continue
            existing.add(patch.node_id)
            result.applied.append(patch.node_id)
            continue

        if patch.op == "remove":
            if patch.node_id not in existing:
                result.rejected.append(f"{patch.node_id}: cannot remove a node that does not exist")
                continue
            if not _remove(root, patch.node_id):
                result.rejected.append(f"{patch.node_id}: could not be removed")
                continue
            existing.discard(patch.node_id)
            result.applied.append(patch.node_id)

    result.unchanged = not result.applied
    return result


def _ids_in(node: Any, out: set[str] | None = None) -> set[str]:
    out = set() if out is None else out
    if not isinstance(node, dict):
        return out
    if node.get("id"):
        out.add(str(node["id"]))
    for child in node.get("children") or []:
        _ids_in(child, out)
    if isinstance(node.get("body"), dict):
        _ids_in(node["body"], out)
    for case in (node.get("cases") or {}).values():
        _ids_in(case, out)
    if isinstance(node.get("default"), dict):
        _ids_in(node["default"], out)
    return out


def _containers(node: Any, out: list[tuple[dict, list]] | None = None) -> list[tuple[dict, list]]:
    """Every `(parent, children-list)` pair, so an insert or remove can find its list."""
    out = [] if out is None else out
    if not isinstance(node, dict):
        return out
    children = node.get("children")
    if isinstance(children, list):
        out.append((node, children))
        for child in children:
            _containers(child, out)
    if isinstance(node.get("body"), dict):
        _containers(node["body"], out)
    for case in (node.get("cases") or {}).values():
        _containers(case, out)
    if isinstance(node.get("default"), dict):
        _containers(node["default"], out)
    return out


def _replace(root: dict, node_id: str, replacement: dict, patch: Patch) -> bool:
    """Substitute one node in place, keeping its id and its provenance.

    The id is forced back onto the replacement: a model that renamed the node while replacing it
    would break every binding pointing at it, and the user asked to change the stage, not to
    re-address it.
    """
    replacement = dict(replacement)
    replacement["id"] = node_id
    if patch.reason:
        replacement.setdefault("extra", {})["revised_because"] = patch.reason
        replacement["extra"]["revised_by"] = patch.requested_by

    if str(root.get("id", "")) == node_id:
        root.clear()
        root.update(replacement)
        return True

    for parent, children in _containers(root):
        for index, child in enumerate(children):
            if isinstance(child, dict) and str(child.get("id", "")) == node_id:
                children[index] = replacement
                return True
        if isinstance(parent.get("body"), dict) and str(parent["body"].get("id", "")) == node_id:
            parent["body"] = replacement
            return True
        for label, case in (parent.get("cases") or {}).items():
            if isinstance(case, dict) and str(case.get("id", "")) == node_id:
                parent["cases"][label] = replacement
                return True

    # A body or case hanging off a node with no `children` list is missed by `_containers`, so
    # walk for it explicitly rather than reporting a failure the caller cannot act on.
    return _replace_deep(root, node_id, replacement)


def _replace_deep(node: Any, node_id: str, replacement: dict) -> bool:
    if not isinstance(node, dict):
        return False
    if isinstance(node.get("body"), dict):
        if str(node["body"].get("id", "")) == node_id:
            node["body"] = replacement
            return True
        if _replace_deep(node["body"], node_id, replacement):
            return True
    for child in node.get("children") or []:
        if _replace_deep(child, node_id, replacement):
            return True
    for label, case in (node.get("cases") or {}).items():
        if isinstance(case, dict):
            if str(case.get("id", "")) == node_id:
                node["cases"][label] = replacement
                return True
            if _replace_deep(case, node_id, replacement):
                return True
    if isinstance(node.get("default"), dict) and _replace_deep(
        node["default"], node_id, replacement
    ):
        return True
    return False


def _insert(root: dict, patch: Patch) -> bool:
    """Add a node, after a named sibling when one is given.

    Insertion-only revision semantics: a reviewer's suggestion becomes an attributed step rather
    than a rewrite of someone else's, which is what gives a revised plan readable provenance.
    """
    node = dict(patch.node or {})
    node["id"] = patch.node_id
    if patch.reason:
        node.setdefault("extra", {})["added_because"] = patch.reason
        node["extra"]["added_by"] = patch.requested_by

    if patch.after:
        for _parent, children in _containers(root):
            for index, child in enumerate(children):
                if isinstance(child, dict) and str(child.get("id", "")) == patch.after:
                    children.insert(index + 1, node)
                    return True
        return False  # named an anchor that does not exist — the caller reports it

    for _parent, children in _containers(root):
        children.append(node)
        return True
    return False


def _remove(root: dict, node_id: str) -> bool:
    for _parent, children in _containers(root):
        for index, child in enumerate(children):
            if isinstance(child, dict) and str(child.get("id", "")) == node_id:
                children.pop(index)
                return True
    return False


def _annotate(root: dict, patch: Patch) -> None:
    for _parent, children in _containers(root):
        for child in children:
            if isinstance(child, dict) and str(child.get("id", "")) == patch.node_id:
                notes = child.setdefault("extra", {}).setdefault("review_notes", [])
                notes.append({"comment": patch.reason, "by": patch.requested_by})
                return
    if str(root.get("id", "")) == patch.node_id:
        notes = root.setdefault("extra", {}).setdefault("review_notes", [])
        notes.append({"comment": patch.reason, "by": patch.requested_by})


# ── TTL'd sketches ──


@dataclass
class Sketch:
    """A draft plan, addressable for revision until it expires."""

    sketch_id: str
    spec: dict[str, Any]
    created_at: float
    goal: str = ""
    revisions: int = 0

    def expired(self, *, now: float, ttl: float = SKETCH_TTL_SECS) -> bool:
        return (now - self.created_at) > ttl


class SketchStore:
    """In-memory draft store with an explicit TTL.

    In-memory deliberately: a draft plan is a conversation artifact. Persisting it would make a
    sketch outlive the conversation that produced it, and a plan revised from a forgotten context
    is a plan nobody reviewed.

    Both failure directions are silent, so both are explicit here: an expired sketch reports
    EXPIRED rather than MISSING (the user's revision was reasonable, the draft just aged out), and
    eviction is oldest-first with a stated cap.
    """

    def __init__(self, *, ttl: float = SKETCH_TTL_SECS, cap: int = MAX_SKETCHES) -> None:
        self._sketches: dict[str, Sketch] = {}
        #: Ids that existed and aged out. Dropping on read is right — a sweep needs a clock nobody
        #: owns — but without a tombstone the EXPIRED reason was one-shot, and a second attempt on
        #: the same id reported "unknown sketch". That loses the distinction that matters: the
        #: user's revision was reasonable and the draft aged out, which is a different thing from
        #: a wrong id.
        self._expired: set[str] = set()
        self._ttl = ttl
        self._cap = cap

    def put(
        self, sketch_id: str, spec: dict[str, Any], *, goal: str = "", now: float | None = None
    ) -> Sketch:
        stamp = time.time() if now is None else now
        sketch = Sketch(sketch_id=sketch_id, spec=spec, created_at=stamp, goal=goal)
        self._sketches[sketch_id] = sketch
        self._evict(now=stamp)
        return sketch

    def get(self, sketch_id: str, *, now: float | None = None) -> tuple[Sketch | None, str]:
        """Returns `(sketch, reason)`. `reason` distinguishes expired from never-existed."""
        stamp = time.time() if now is None else now
        sketch = self._sketches.get(sketch_id)
        if sketch is None:
            if sketch_id in self._expired:
                return None, "sketch expired — re-plan rather than revising a stale draft"
            return None, "unknown sketch"
        if sketch.expired(now=stamp, ttl=self._ttl):
            # Dropped on read rather than by a sweep: a sweep needs a clock nobody owns, and the
            # only moment the staleness matters is when someone tries to use it.
            self._sketches.pop(sketch_id, None)
            self._expired.add(sketch_id)
            return None, "sketch expired — re-plan rather than revising a stale draft"
        return sketch, ""

    def revise(
        self, sketch_id: str, patches: list[Patch], *, now: float | None = None
    ) -> tuple[MergeResult | None, str]:
        sketch, reason = self.get(sketch_id, now=now)
        if sketch is None:
            return None, reason
        result = merge_patches(sketch.spec, patches)
        if result.applied:
            sketch.spec = result.spec
            sketch.revisions += 1
        return result, ""

    def _evict(self, *, now: float) -> None:
        for key, sketch in list(self._sketches.items()):
            if sketch.expired(now=now, ttl=self._ttl):
                self._sketches.pop(key, None)
                self._expired.add(key)
        while len(self._sketches) > self._cap:
            oldest = min(self._sketches.values(), key=lambda s: s.created_at)
            self._sketches.pop(oldest.sketch_id, None)
            self._expired.add(oldest.sketch_id)
        # The tombstone set is bounded too — it is a courtesy, not a log, and an unbounded set of
        # ids would outlive the process it was helping.
        while len(self._expired) > self._cap * 4:
            self._expired.pop()

    def __len__(self) -> int:
        return len(self._sketches)


# ── the review surface ──


def announce_block(
    *,
    intent: Any = None,
    match: Any = None,
    contracts: list[Any] | None = None,
    decisions: list[Any] | None = None,
    cost: dict[str, Any] | None = None,
) -> str:
    """The review header (UP-R4): what was detected, what it risks, what it will cost.

    Ordered so the two things a user might VETO come first. Detection and risk decide whether to
    read further; the pipeline is what they read if they do. Putting the pipeline first would bury
    "this touches payments" under twelve stage names.
    """
    lines: list[str] = []

    primary = str(getattr(match, "primary", "") or "") if match is not None else ""
    if primary:
        confidence = float(getattr(match, "confidence", 0.0) or 0.0)
        reason = str(getattr(match, "reason", "") or "")
        lines.append(f"Detected:  {match.primary} ({reason[:70]}) @ {confidence:.0%}")
    elif intent is not None:
        lines.append(
            f"Detected:  no template — generating ({getattr(intent, 'shape', '') or 'freeform'})"
        )

    if intent is not None:
        risk_bits = []
        if getattr(intent, "irreversible", False):
            risk_bits.append("IRREVERSIBLE action")
        stakes = getattr(getattr(intent, "stakes", None), "value", "")
        if stakes == "high":
            risk_bits.append("high stakes")
        signals = (getattr(intent, "signals", {}) or {}).get("stakes") or []
        positive = [s for s in signals if not str(s).startswith("-")]
        if positive:
            risk_bits.append(", ".join(str(s) for s in positive[:3]))
        lines.append(f"Risk:      {'; '.join(risk_bits) if risk_bits else 'nothing flagged'}")
        lines.append(f"Rigor:     {getattr(getattr(intent, 'rigor', None), 'value', 'standard')}")

    blocking = [d for d in (decisions or []) if getattr(d, "blocking", False)]
    if blocking:
        names = ", ".join(getattr(d, "node_id", "?") for d in blocking[:4])
        lines.append(f"Pauses at: {names}")

    if cost:
        parts = [f"{v} {k}" for k, v in cost.items() if v]
        if parts:
            lines.append(f"Cost:      ~{', '.join(parts)}")

    # Same exemptions the contract lint applies, because a header that flags four stages the lint
    # deliberately exempts makes the two views of one plan disagree — and the user believes the
    # scarier one. `feeds_verified` stages are checked through their consumer; zero-token nodes have
    # a deterministic contract with nothing for a judge to weigh.
    unverified = [
        c
        for c in (contracts or [])
        if not getattr(c, "verifiable", True)
        and not getattr(c, "feeds_verified", False)
        and getattr(c, "kind", "") not in ("action", "transform")
    ]
    if unverified:
        # Surfaced in the header, not buried per-stage: "three stages nobody checks" is a reason to
        # revise the plan, and a user scrolling past it has already approved.
        names = ", ".join(getattr(c, "node_id", "?") for c in unverified[:4])
        lines.append(f"Unchecked: {names}")

    return "\n".join(lines)


def estimate_cost(spec: dict[str, Any]) -> dict[str, Any]:
    """A structural cost estimate: model calls by tier, plus fan-out multipliers.

    Structural rather than measured, and labelled as such by returning counts rather than a price.
    A dollar figure derived from a node count would be a confident number built on an unknown
    per-call cost, and a user who sees "$0.42" believes it.
    """
    from personalclaw.workflows.models import LLM_KINDS, Node, walk

    root_raw = spec.get("root")
    if not isinstance(root_raw, dict):
        return {}
    try:
        root = Node.from_dict(root_raw)
    except Exception:
        return {}

    llm_kinds = {k.value for k in LLM_KINDS}
    calls = 0
    fan_out = 0
    unbounded_loops = 0

    for path, node in walk(root):
        if node.kind.value in llm_kinds:
            # Multiply by every enclosing foreach/loop: a stage inside a fan-out is not one call.
            multiplier = 1
            for other_path, other in walk(root):
                if other.kind.value in ("foreach", "loop") and path.startswith(other_path + "."):
                    cap = (other.config or {}).get("max_iterations") or (other.config or {}).get(
                        "n"
                    )
                    multiplier *= int(cap) if isinstance(cap, int) and cap > 0 else 3
            calls += multiplier
        if node.kind.value == "foreach":
            fan_out += 1
        if node.kind.value == "loop":
            cfg = node.config or {}
            if not isinstance(cfg.get("max_iterations"), int) and not isinstance(cfg.get("n"), int):
                unbounded_loops += 1

    out: dict[str, Any] = {"model calls": calls}
    if fan_out:
        out["fan-outs"] = fan_out
    if unbounded_loops:
        # Named rather than folded into the estimate: an unbounded loop makes the number a floor,
        # and presenting a floor as an estimate understates exactly the case that runs away.
        out["UNBOUNDED loops"] = unbounded_loops
    return out


def plan_markdown(
    spec: dict[str, Any],
    *,
    goal: str = "",
    header: str = "",
    contracts: list[Any] | None = None,
) -> str:
    """The plan as a markdown artifact — the thing a user can read, keep and revise against.

    Markdown rather than JSON for the human surface, with the JSON staying authoritative. A user
    reviewing a plan is deciding whether the WORK is right, and a node tree makes them read
    structure to find the work.
    """
    lines: list[str] = [f"# Plan: {goal or 'untitled'}", ""]
    if header:
        lines.extend(["```", header, "```", ""])

    by_id = {getattr(c, "node_id", ""): c for c in (contracts or [])}
    lines.append("## Steps")
    for index, (node_id, kind, scope) in enumerate(_steps(spec), start=1):
        contract = by_id.get(node_id)
        lines.append(f"{index}. **{node_id}** ({kind}) — {scope}")
        if contract is not None:
            if getattr(contract, "done_means", ""):
                lines.append(f"   - done means: {contract.done_means}")
            if not getattr(contract, "verifiable", True):
                lines.append("   - ⚠️ nothing checks this step")
    return "\n".join(lines)


def _steps(spec: dict[str, Any]) -> list[tuple[str, str, str]]:
    from personalclaw.workflows.models import Node, walk

    root_raw = spec.get("root")
    if not isinstance(root_raw, dict):
        return []
    try:
        root = Node.from_dict(root_raw)
    except Exception:
        return []
    out: list[tuple[str, str, str]] = []
    for path, node in walk(root):
        if node.is_container:
            continue
        cfg = node.config or {}
        scope = str(cfg.get("label", "") or "")
        if not scope:
            prompt = str(cfg.get("prompt", "") or "")
            scope = (
                " ".join(prompt.split())[:110]
                if prompt
                else str(cfg.get("provider", "") or node.kind.value)
            )
        out.append((node.id or path, node.kind.value, scope))
    return out


def inferred_chips(spec: dict[str, Any], user_words: str) -> list[dict[str, Any]]:
    """Which parameter values came from the user's own words versus were inferred.

    The distinction is what makes review possible: a user re-reads what they said and skims what
    the system assumed, so an inferred value presented identically to a stated one is the one that
    ships wrong. Chips are marked `inferred` when the value does not appear in the user's text.
    """
    haystack = " ".join((user_words or "").lower().split())
    out: list[dict[str, Any]] = []
    inputs = spec.get("inputs")
    if not isinstance(inputs, dict):
        return out
    for name, meta in inputs.items():
        value = meta.get("default") if isinstance(meta, dict) else meta
        if value in (None, "", [], {}):
            continue
        stated = str(value).lower() in haystack
        out.append(
            {
                "name": name,
                "value": value,
                "source": "stated" if stated else "inferred",
                "confirm": not stated,
            }
        )
    return out

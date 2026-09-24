"""Memory formation — the Extract → Gather → Decide pipeline for consolidation.

MEMORY-GRAPH-AND-VAULT §4.1 (MGAV-5). Consolidation used to extract candidate facts in
one prompt and write them straight in. That is how a memory store accretes both
duplicates ("pref.editor: vim" beside "pref.text_editor: vim") and contradictions (an
old value that was never retired), and neither is visible until the model starts
answering from the wrong one.

The restructure keeps ONE extra model call, no more:

* **Extract** — the existing consolidation prompt, unchanged. Candidates in, nothing
  written yet.
* **Gather** — fully deterministic: for each candidate find the existing rows it might
  collide with (same key, keyword overlap, graph-arm neighbours). Zero model calls.
* **Decide** — ONE structured call for the WHOLE batch: per candidate a verdict of
  ``ADD`` / ``UPDATE`` / ``SUPERSEDE`` / ``NOOP`` plus a one-line reason.

Four safety properties are non-negotiable and each is enforced here rather than trusted
to the model:

1. **No physical deletes, ever.** ``SUPERSEDE`` writes the new row and then calls
   ``supersede_semantic``, which soft-deletes the old row with ``superseded_by`` +
   ``invalidated_at`` and a WAL entry. The superseded row stays READABLE — that is what
   makes a wrong supersede recoverable instead of a silent data loss.
2. **Unsure means keep BOTH.** A contradiction the model flags as unsure — or one where
   holder precedence forbids the overwrite — becomes two live rows joined by a
   ``references`` edge carrying the conflict provenance, surfaced by the memory lint.
   Averaging two contradictory facts into one is how a store starts lying confidently.
3. **Holder precedence is enforced at the DECISION point.** A lower-precedence claim
   (an ``external`` rumour) cannot supersede a higher-precedence one (something the user
   said) no matter what the model returns. Ranking it lower at read time would not be
   the same guarantee.
4. **Fail-safe degradation.** No Decide prompt, no model, or an unparseable response →
   every candidate falls back to ``ADD``, i.e. exactly today's behavior. A formation
   failure must never mean "this session's memories were dropped".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from personalclaw import memory_holder

logger = logging.getLogger(__name__)

#: The four verdicts the Decide call may return (§4.1). "keep both" is deliberately NOT
#: one of them: it is the OUTCOME of an unsure SUPERSEDE/UPDATE, derived here, so the
#: model is never asked to choose "do nothing useful" as a first-class answer.
VERDICT_ADD = "ADD"
VERDICT_UPDATE = "UPDATE"
VERDICT_SUPERSEDE = "SUPERSEDE"
VERDICT_NOOP = "NOOP"
VERDICTS = (VERDICT_ADD, VERDICT_UPDATE, VERDICT_SUPERSEDE, VERDICT_NOOP)

#: ``mem_links.provenance`` marking a kept-both contradiction. The lint scans for this
#: exact value, so it is a constant rather than a literal at two call sites.
CONFLICT_PROVENANCE = "conflict"
#: The WAL event type for a kept-both decision, so the conflict is auditable even on a
#: store whose entity graph is switched off.
CONFLICT_EVENT = "conflict_keep_both"

#: How many existing rows one candidate may carry into the Decide prompt. The prompt is
#: one call for the whole batch, so this bounds the payload, not the accuracy.
MAX_OVERLAPS_PER_CANDIDATE = 4
#: Keyword-overlap ratio at which an existing row is considered a possible collision.
#: Matches ``memory_lint._NEAR_DUP_RATIO``'s intent (the same "these two are about the
#: same thing" judgement) but is set lower here: Gather feeds a model that can say NOOP,
#: so a false candidate costs tokens, while a missed one costs a duplicate row forever.
OVERLAP_RATIO = 0.5

_STOPWORDS = frozenset(
    {"the", "a", "an", "to", "in", "for", "and", "or", "not", "is", "of", "on", "with", "at"}
)


def _words(text: str) -> set[str]:
    import re

    return {w for w in re.split(r"\W+", (text or "").lower()) if len(w) > 2 and w not in _STOPWORDS}


def _namespace(key: str) -> str:
    """The dotted parent of ``key`` when it is specific enough to mean something, else "".

    ``project.x.status`` → ``project.x``; ``pref.editor`` → ``""`` (its parent is the bare
    allowlist prefix, which every sibling shares).
    """
    parent = key.rsplit(".", 1)[0] if "." in key else ""
    return parent if "." in parent else ""


def _value_str(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)


@dataclass(frozen=True)
class Overlap:
    """One existing row a candidate might collide with, and why we think so."""

    key: str
    value_str: str
    holder: str = ""
    weight: float = 1.0
    why: str = "keyword"

    def as_payload(self) -> dict:
        out: dict[str, Any] = {"key": self.key, "value": self.value_str, "matched_by": self.why}
        if self.holder:
            out["holder"] = self.holder
            out["weight"] = round(self.weight, 2)
        return out


@dataclass
class Candidate:
    """One extracted fact plus the existing rows Gather found for it."""

    index: int
    key: str
    value: object
    confidence: float = 0.5
    holder: str = ""
    weight: float = 1.0
    delete: bool = False
    overlaps: list[Overlap] = field(default_factory=list)

    @property
    def value_str(self) -> str:
        return _value_str(self.value)

    def as_payload(self) -> dict:
        out: dict[str, Any] = {
            "index": self.index,
            "key": self.key,
            "value": self.value_str,
            "existing": [o.as_payload() for o in self.overlaps],
        }
        if self.holder:
            out["holder"] = self.holder
            out["weight"] = round(self.weight, 2)
        return out


@dataclass
class Decision:
    """The adjudicated outcome for one candidate — what we will actually do."""

    index: int
    verdict: str = VERDICT_ADD
    target: str = ""
    reason: str = ""
    unsure: bool = False


@dataclass
class FormationReport:
    """What a formation pass did. Rendered into the consolidation log line."""

    added: int = 0
    updated: int = 0
    superseded: int = 0
    noop: int = 0
    rejected: int = 0
    #: ``(new_key, old_key)`` pairs kept as a flagged contradiction.
    conflicts: list[tuple[str, str]] = field(default_factory=list)
    #: True when Decide did not run (no prompt/model/parse) and everything fell back.
    degraded: bool = False

    def summary(self) -> str:
        parts = [
            f"added={self.added}",
            f"updated={self.updated}",
            f"superseded={self.superseded}",
            f"noop={self.noop}",
        ]
        if self.rejected:
            parts.append(f"rejected={self.rejected}")
        if self.conflicts:
            parts.append(f"conflicts={len(self.conflicts)}")
        if self.degraded:
            parts.append("decide=degraded")
        return " ".join(parts)


# ── Extract → candidates ───────────────────────────────────────────────────────


def candidates_from_extract(
    items: Iterable[object], *, holder_attribution: bool, limit: int
) -> list[Candidate]:
    """Turn the Extract phase's ``semantic`` array into typed candidates.

    ``holder_attribution`` off means the axis is not persisted at all: the extracted
    holder/weight are dropped and the row is written as a plain fact, which is exactly
    the pre-MGAV-5 shape. That is the flag doing real work rather than decorating.
    """
    out: list[Candidate] = []
    for item in list(items)[:limit]:
        if not isinstance(item, dict) or "key" not in item:
            continue
        key = str(item["key"])
        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        holder = memory_holder.normalize_holder(item.get("holder")) if holder_attribution else ""
        weight = memory_holder.normalize_weight(holder, item.get("weight", 1.0))
        out.append(
            Candidate(
                index=len(out),
                key=key,
                value=item.get("value"),
                confidence=confidence,
                holder=holder,
                weight=weight,
                delete=bool(item.get("delete")),
            )
        )
    return out


# ── Gather (deterministic, zero model calls) ──────────────────────────────────


def gather(vs, candidates: Sequence[Candidate]) -> list[Candidate]:
    """Populate each candidate's ``overlaps`` from the existing store. In place.

    Three deterministic arms, in precedence order:

    * **same key** — the row this write would overwrite. Always first, because it is the
      only overlap that is certain rather than inferred.
    * **shared key namespace** — two keys under the same dotted namespace
      (``project.x.status`` vs ``project.x.state``). Requires the shared parent to itself
      contain a dot: sharing only the top-level allowlist prefix (``pref``) would make
      every preference collide with every other one, which is noise, not a signal.
    * **keyword overlap** — the generalization of the ``episodic_dedup_threshold``
      machinery that already half-did this for episodics (§4.1).
    * **graph arm** — records linked to entities the candidate NAMES, which catches a
      collision whose wording shares nothing with the candidate's.

    Deliberately no vector arm: in this schema embeddings live on ``episodic_memories``,
    not on ``semantic_memory``, so a "vector_query over existing memories" would either
    search the wrong table or embed every fact on every consolidation. The graph arm is
    the recall path that actually covers the wording-independent case here.
    """
    rows = vs.db.execute(
        "SELECT key, value_json, holder, weight FROM semantic_memory WHERE is_deleted = 0 "
        "ORDER BY key"
    ).fetchall()
    existing = {}
    for r in rows:
        try:
            val = json.loads(r["value_json"])
        except (json.JSONDecodeError, TypeError):
            val = r["value_json"]
        existing[r["key"]] = Overlap(
            key=r["key"],
            value_str=_value_str(val),
            holder=memory_holder.normalize_holder(r["holder"]),
            weight=float(r["weight"] if r["weight"] is not None else 1.0),
        )

    for cand in candidates:
        found: list[Overlap] = []
        seen: set[str] = set()

        same = existing.get(cand.key)
        if same is not None:
            found.append(Overlap(**{**same.__dict__, "why": "same_key"}))
            seen.add(same.key)

        namespace = _namespace(cand.key)
        if namespace:
            for key in sorted(existing):
                if key not in seen and _namespace(key) == namespace:
                    found.append(Overlap(**{**existing[key].__dict__, "why": "key_namespace"}))
                    seen.add(key)

        cand_words = _words(f"{cand.key.replace('.', ' ').replace('_', ' ')} {cand.value_str}")
        if cand_words:
            for key in sorted(existing):
                if key in seen:
                    continue
                other = existing[key]
                other_words = _words(f"{key.replace('.', ' ').replace('_', ' ')} {other.value_str}")
                if not other_words:
                    continue
                ratio = len(cand_words & other_words) / min(len(cand_words), len(other_words))
                if ratio >= OVERLAP_RATIO:
                    found.append(Overlap(**{**other.__dict__, "why": "keyword"}))
                    seen.add(key)

        # Graph arm — best-effort by contract (off/empty graph degrades to nothing).
        try:
            boosts = vs._graph_boosts(cand.value_str)
        except Exception:  # noqa: BLE001
            boosts = {}
        for ref in sorted(boosts, key=lambda r: (-boosts[r], r)):
            if ref in seen or ref not in existing:
                continue
            found.append(Overlap(**{**existing[ref].__dict__, "why": "graph"}))
            seen.add(ref)

        cand.overlaps = found[:MAX_OVERLAPS_PER_CANDIDATE]
    return list(candidates)


# ── Decide (the ONE added structured call) ────────────────────────────────────


def build_decide_prompt(candidates: Sequence[Candidate]) -> str:
    """The Decide prompt, or "" when there is nothing to adjudicate.

    "" when NO candidate has an overlap: with nothing to collide with, every verdict is
    ADD by construction, and spending a model call to be told so would make the "one
    extra cheap call" claim false on the common path.
    """
    with_overlaps = [c for c in candidates if c.overlaps]
    if not with_overlaps:
        return ""
    from personalclaw.prompt_providers.runtime import render_snippet_block

    instructions = render_snippet_block("memory-decide")
    if not instructions.strip():
        return ""
    payload = json.dumps([c.as_payload() for c in with_overlaps], indent=1)
    return f"{instructions}\n\n## Candidates\n{payload}\n"


def parse_decisions(result: object, candidates: Sequence[Candidate]) -> dict[int, Decision]:
    """Map a Decide response onto ``{candidate index: Decision}``.

    Unknown verdicts, missing indices and malformed rows are all treated as "no opinion"
    and left to the ADD fallback — a garbled verdict must never be interpreted as
    permission to retire a row.
    """
    out: dict[int, Decision] = {}
    if not isinstance(result, dict):
        return out
    rows = result.get("verdicts")
    if not isinstance(rows, list):
        return out
    valid = {c.index for c in candidates}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("index", -1))
        except (TypeError, ValueError):
            continue
        if index not in valid:
            continue
        verdict = str(row.get("verdict", "")).strip().upper()
        if verdict not in VERDICTS:
            continue
        out[index] = Decision(
            index=index,
            verdict=verdict,
            target=str(row.get("target", "") or ""),
            reason=str(row.get("reason", "") or "")[:200],
            unsure=bool(row.get("unsure")),
        )
    return out


def adjudicate(cand: Candidate, decision: Decision | None) -> Decision:
    """Resolve one candidate's final action, enforcing the invariants the model can't.

    Returns a Decision whose ``verdict``/``unsure`` are safe to execute:

    * no decision at all → ``ADD`` (the fail-safe default);
    * a ``SUPERSEDE``/``UPDATE`` whose target is unknown → ``ADD``;
    * a ``SUPERSEDE`` the model is unsure about → still SUPERSEDE-shaped but
      ``unsure``, which the executor turns into keep-both;
    * a ``SUPERSEDE``/``UPDATE`` where the target's holder outranks the candidate's →
      forced ``unsure`` (holder precedence, §4.2). This is the rule that stops an
      external rumour from retiring something the user told us.
    """
    if decision is None:
        return Decision(index=cand.index, verdict=VERDICT_ADD, reason="no verdict — default add")
    verdict, target = decision.verdict, decision.target
    targets = {o.key: o for o in cand.overlaps}
    if verdict in (VERDICT_SUPERSEDE, VERDICT_UPDATE):
        if not target:
            # UPDATE with no explicit target means "the same key" when that row exists.
            same = next((o for o in cand.overlaps if o.why == "same_key"), None)
            if same is None:
                return Decision(
                    index=cand.index,
                    verdict=VERDICT_ADD,
                    reason="update/supersede without a target row",
                )
            target = same.key
        if target not in targets:
            return Decision(
                index=cand.index, verdict=VERDICT_ADD, reason=f"unknown target {target!r}"
            )
        if verdict == VERDICT_SUPERSEDE and target == cand.key:
            # A row cannot supersede itself; that is an UPDATE.
            verdict = VERDICT_UPDATE
        unsure = decision.unsure
        if memory_holder.precedence(cand.holder) < memory_holder.precedence(targets[target].holder):
            unsure = True
        return Decision(
            index=cand.index,
            verdict=verdict,
            target=target,
            reason=decision.reason,
            unsure=unsure,
        )
    return Decision(
        index=cand.index, verdict=verdict, target=target, reason=decision.reason, unsure=False
    )


# ── Execute ───────────────────────────────────────────────────────────────────


def _write(vs, cand: Candidate, source: str, *, holder_attribution: bool) -> bool:
    item_source = "user_explicit" if cand.confidence >= 1.0 else source
    kwargs: dict[str, Any] = {}
    if holder_attribution and cand.holder:
        kwargs = {"holder": cand.holder, "weight": cand.weight}
    err = vs.set_semantic(cand.key, cand.value, cand.confidence, item_source, **kwargs)
    return err is None


def flag_conflict(vs, new_key: str, old_key: str, *, source: str, reason: str = "") -> None:
    """Record a kept-both contradiction: a ``references`` edge + a WAL event.

    Written to BOTH surfaces on purpose. The edge is what the lint reads and what the
    graph UI can draw; the WAL event is what survives on a store whose entity graph is
    switched off, and a contradiction the user can't see is the one failure mode this
    whole path exists to prevent.
    """
    try:
        vs.append_event(
            event_type=CONFLICT_EVENT,
            memory_type="semantic",
            memory_key=new_key,
            old_value=old_key,
            new_value=reason[:200] or None,
            source=source,
        )
    except Exception:  # noqa: BLE001
        logger.debug("conflict WAL event failed for %s/%s", new_key, old_key, exc_info=True)
    try:
        vs.graph.add_link(
            from_kind="semantic",
            from_ref=new_key,
            to_ref=old_key,
            link_type="references",
            provenance=CONFLICT_PROVENANCE,
            context=reason[:200] or None,
            source=source,
        )
    except Exception:  # noqa: BLE001
        logger.debug("conflict edge failed for %s/%s", new_key, old_key, exc_info=True)


def conflicts(vs) -> list[dict]:
    """Every live kept-both contradiction, newest first.

    Reads the ``references``+``conflict`` edges. Pairs where either side is gone are
    skipped: a conflict the user already resolved is not a standing flag.
    """
    try:
        rows = vs.db.execute(
            "SELECT from_ref, to_ref, context, created_at FROM mem_links "
            "WHERE link_type = 'references' AND provenance = ? ORDER BY id DESC",
            (CONFLICT_PROVENANCE,),
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.debug("conflict scan unavailable", exc_info=True)
        return []
    out: list[dict] = []
    for row in rows:
        new_key, old_key = row["from_ref"], row["to_ref"]
        live = vs.db.execute(
            "SELECT COUNT(*) AS n FROM semantic_memory WHERE key IN (?, ?) AND is_deleted = 0",
            (new_key, old_key),
        ).fetchone()
        if int(live["n"]) < 2:
            continue
        out.append(
            {
                "new_key": new_key,
                "old_key": old_key,
                "reason": row["context"] or "",
                "created_at": row["created_at"],
            }
        )
    return out


def apply_decisions(
    vs,
    candidates: Sequence[Candidate],
    decisions: dict[int, Decision],
    *,
    source: str,
    holder_attribution: bool = False,
) -> FormationReport:
    """Execute the adjudicated verdicts against the store. Never physically deletes.

    ``SUPERSEDE`` order matters: the new row is written FIRST, then the old row is
    pointed at it. Superseding first would leave a window where neither the old nor the
    new value is live, and a crash inside that window would lose the fact outright.
    """
    report = FormationReport()
    for cand in candidates:
        final = adjudicate(cand, decisions.get(cand.index))
        if cand.delete:
            # An extract-phase retirement. Still a soft tombstone with a WAL entry —
            # the row stays readable — so it needs no verdict.
            if vs.delete_semantic(cand.key, source):
                report.superseded += 1
            continue
        if final.verdict == VERDICT_NOOP:
            report.noop += 1
            continue
        if final.verdict == VERDICT_SUPERSEDE and final.unsure:
            # Keep BOTH: write the new row, flag the contradiction, retire nothing.
            if _write(vs, cand, source, holder_attribution=holder_attribution):
                report.added += 1
                flag_conflict(vs, cand.key, final.target, source=source, reason=final.reason)
                report.conflicts.append((cand.key, final.target))
            else:
                report.rejected += 1
            continue
        if final.verdict == VERDICT_UPDATE:
            target_cand = cand if final.target == cand.key else _retarget(cand, final.target)
            if final.unsure and final.target != cand.key:
                # An unsure cross-key UPDATE is a contradiction, not an edit.
                if _write(vs, cand, source, holder_attribution=holder_attribution):
                    report.added += 1
                    flag_conflict(vs, cand.key, final.target, source=source, reason=final.reason)
                    report.conflicts.append((cand.key, final.target))
                else:
                    report.rejected += 1
                continue
            if _write(vs, target_cand, source, holder_attribution=holder_attribution):
                report.updated += 1
            else:
                report.rejected += 1
            continue
        if final.verdict == VERDICT_SUPERSEDE:
            if not _write(vs, cand, source, holder_attribution=holder_attribution):
                report.rejected += 1
                continue
            report.added += 1
            if vs.supersede_semantic(final.target, cand.key, source):
                report.superseded += 1
            continue
        if _write(vs, cand, source, holder_attribution=holder_attribution):
            report.added += 1
        else:
            report.rejected += 1
    return report


def _retarget(cand: Candidate, key: str) -> Candidate:
    """A copy of ``cand`` writing to ``key`` — an UPDATE onto an existing row's key."""
    return Candidate(
        index=cand.index,
        key=key,
        value=cand.value,
        confidence=cand.confidence,
        holder=cand.holder,
        weight=cand.weight,
        overlaps=cand.overlaps,
    )

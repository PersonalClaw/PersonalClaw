"""When a lesson learned would replace one the owner taught: hers stays, and she is asked.

A lesson she taught (its row's source is a person's: ``vector_memory.only_a_person_replaces``) is
replaced, retired or rewritten by her alone. When a lesson something else learned (a chat's
consolidation, the after-turn review, the contradiction judge's verdict, an app's work) would
replace one of hers, the store keeps hers as it is and keeps nothing of the learned one
(``VectorMemoryStore.write_lesson``), and :func:`ask` puts the question to her: one proposal in the
learning queue, which the Learning page lists and the Inbox raises, naming her lesson and the
learned one. Accept keeps the learned one in place of hers, as a lesson she taught
(:func:`install_accepted`; Settings → Memory → Audit can undo it). Reject keeps hers, and the
queue's decision memory keeps the same question from being asked again, so a learned lesson she
turned down is never kept over hers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from personalclaw import memory_locality, memory_writes
from personalclaw.learning import proposals
from personalclaw.memory_record import MemoryScope

if TYPE_CHECKING:
    from personalclaw.vector_memory import VectorMemoryStore

logger = logging.getLogger(__name__)

#: The tag of the question, which is how its installer finds it (:func:`is_conflict_proposal`).
TAG = "lesson_conflict"

#: What learned a lesson, by the source its write names, in the words the question uses.
_LEARNED_FROM = {
    "consolidation": "A lesson learned from a chat",
    "after_turn_review": "A lesson learned from your correction in a chat",
    "facet_veto": "A lesson learned from something you said in a chat",
    "migration": "A lesson carried over from an earlier version",
}

_TITLE_MAX = 120
#: What her answers do, said for both places she answers: the Learning page's Accept and Reject,
#: and the Inbox's Approve, which is the same Accept.
_ANSWERS = (
    "Accept (Approve in the Inbox) keeps the learned lesson instead: it is saved as a lesson of "
    "yours, in place of what you taught (Settings → Memory → Audit can undo that). Reject, on the "
    "Learning page, keeps what you taught, and the learned one is not offered again. Until you "
    "answer, what you taught is what PersonalClaw follows."
)


@dataclass(frozen=True)
class _Held:
    """The learned lesson a question holds for her answer, and what keeping it replaces."""

    memory: str
    rule: str
    negative: str | None
    scope: MemoryScope
    scope_ref: str | None
    replacing: tuple[str, ...]


def _learned_from(source: str) -> str:
    if source.startswith(memory_writes.APP_SOURCE_PREFIX):
        return f"A lesson the app {source.removeprefix(memory_writes.APP_SOURCE_PREFIX)} wrote"
    return _LEARNED_FROM.get(source, "A lesson PersonalClaw learned")


def _quoted(text: str) -> str:
    return f"“{text}”"


def ask(
    vs: "VectorMemoryStore",
    *,
    key: str,
    rule: str,
    negative: str | None,
    learned_by: str,
    scope: MemoryScope,
    scope_ref: str | None,
    taught: dict[str, str],
    replacing: list[str],
) -> bool:
    """Ask her which to keep: the lessons she taught (*taught*, key → rule) in the store *vs*, or
    the lesson *rule* (with *negative*, under *key*) that *learned_by* would have kept in their
    place, also replacing the others in *replacing*. True when the question is waiting for her:
    filed now, or already waiting. False when she already answered it, and when *vs* is no memory
    of this home's (a scratch or benchmark store), where nobody is asked and hers simply stay."""
    part = memory_locality.partition_holding(vs.db_path)
    if part is None:
        logger.info("Kept a taught lesson over a learned one in %s; nobody to ask", vs.db_path)
        return False
    learned = rule if not negative else f"{rule} — NOT: {negative}"
    yours = [_quoted(text) for text in taught.values()]
    lines = [f"Yours: {text}" for text in yours] + [f"Learned: {_quoted(learned)}"]
    one = len(taught) == 1
    where = f" {'Yours is' if one else 'Yours are'} in the memory of {part.shown}."
    where = where if part.shown else ""
    some = "one you taught" if one else "lessons you taught"
    body = "\n".join(
        [
            f"{_learned_from(learned_by)} would replace {some}. Only you replace a lesson you "
            f"taught, so what you taught stays as it is and the learned one was not saved.{where}",
            "",
            *lines,
            "",
            _ANSWERS,
        ]
    )
    title = f"A learned lesson would replace one you taught: {yours[0]}"
    if len(title) > _TITLE_MAX:
        title = title[: _TITLE_MAX - 1].rstrip() + "…"
    refs = [f"lesson:{taught_key}" for taught_key in taught]
    held = {
        "memory": part.id,
        "rule": rule,
        "negative": negative or "",
        "scope": scope.value,
        "scope_ref": scope_ref or "",
        "replacing": list(replacing),
    }
    verdict, prop = proposals.enqueue(
        kind=proposals.Kind.LESSON_BATCH.value,
        title=title,
        body=body,
        # One question per learned lesson in one memory: a different lesson, or the same one in
        # another memory, is another question.
        target=f"{part.id}:{key}" if part.id else key,
        provenance="inferred",
        source_cadence=learned_by,
        session_key=memory_writes.filed_under(),
        # What the Learning page shows on the row she decides.
        source_excerpt="\n".join([*lines, _ANSWERS]),
        evidence_refs=refs,
        change_manifest=proposals.ChangeManifest(
            component="lessons",
            failure_pattern="a lesson learned would replace one you taught",
            evidence_refs=refs,
            root_cause="only you replace a lesson you taught",
            # The lesson her Accept keeps, which the accept path applies (:func:`install_accepted`).
            targeted_fix=[held],
        ),
        tags=[TAG],
        occurrences=1,
        min_evidence=1,
    )
    if prop is None:
        logger.info("Kept a taught lesson over a learned one she already turned down")
        return False
    logger.info("Asked which lesson to keep (%s, %s)", prop.id, verdict.value)
    return True


def is_conflict_proposal(data: dict) -> bool:
    """Whether the proposal record *data* is a question :func:`ask` filed."""
    return str(data.get("kind") or "") == proposals.Kind.LESSON_BATCH.value and TAG in (
        data.get("tags") or []
    )


def _held(data: dict) -> _Held:
    """The learned lesson the question *data* holds, or :class:`proposals.AcceptError` when it
    holds none it can keep."""
    manifest = data.get("change_manifest") or {}
    fix = manifest.get("targeted_fix") if isinstance(manifest, dict) else None
    held = fix[0] if isinstance(fix, list) and fix and isinstance(fix[0], dict) else {}
    rule = held.get("rule")
    replacing = held.get("replacing")
    try:
        scope = MemoryScope(str(held.get("scope") or ""))
    except ValueError:
        scope = None
    if (
        not isinstance(rule, str)
        or not rule.strip()
        or scope is None
        or not isinstance(replacing, list)
        or not isinstance(held.get("memory"), str)
    ):
        raise proposals.AcceptError(
            "nothing changed: the question holds no lesson to keep, and it is still waiting"
        )
    return _Held(
        memory=str(held["memory"]),
        rule=rule,
        negative=str(held.get("negative") or "") or None,
        scope=scope,
        scope_ref=str(held.get("scope_ref") or "") or None,
        replacing=tuple(str(k) for k in replacing if k),
    )


def _store_of(memory: str, service: Any) -> "VectorMemoryStore | None":
    """The record store of the memory *memory* names (:mod:`memory_locality`'s partition id): the
    gateway's own for the global memory (*service*'s), a folder's partition's for a folder's."""
    if memory == memory_locality.GLOBAL_PARTITION:
        return getattr(service, "provider", None)
    part = memory_locality.partition_named(memory)
    if part is None:
        return None
    return memory_locality.open_partition(part, writes=True).vector_store


def install_accepted(data: dict, service: Any) -> None:
    """Her Accept of the question *data*: the learned lesson is saved as a lesson she taught in
    the memory it was learned in, and each lesson it would have replaced is retired toward it
    (``VectorMemoryStore.keep_lesson_in_place_of``; Settings → Memory → Audit can undo it). One of
    hers she removed since is not there to retire. Refused, changing nothing, when that memory is
    not reachable or memory refuses the lesson: the question is still waiting."""
    held = _held(data)
    store = _store_of(held.memory, service)
    if store is None:
        raise proposals.AcceptError(
            "nothing changed: the memory the lesson was learned in is not reachable, and the "
            "question is still waiting"
        )
    if store.keep_lesson_in_place_of(
        held.rule,
        held.negative,
        replacing=held.replacing,
        scope=held.scope,
        scope_ref=held.scope_ref,
    ):
        return
    refused = store.lesson_refusal(
        held.rule, held.negative, scope=held.scope, scope_ref=held.scope_ref
    )
    why = refused[1] if refused is not None else "it was not kept"
    raise proposals.AcceptError(
        f"nothing changed: memory did not keep the learned lesson ({why}), and the question is "
        "still waiting"
    )

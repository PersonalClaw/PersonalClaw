"""What a piece of work waits on, as a typed record: why, what resumes it, by when, and who says so.

A chat's run state said it waited (its phase ``approval`` or ``input``, and a line), and every
surface that wanted to know on what read the registries again and matched them its own way. A
:class:`Wait` says it once (shared contracts §4.3): its ``reason``, the ``resume_condition`` that
ends it (``{kind, ref}``, the ref naming what resumes it: a waiting list item's id for an ask),
``deadline_at`` (``None`` for a wait with no end of its own) and ``evidence_source``, the registry
it is known from. Each reason is known from its own registries and resumed its own ways
(:data:`REASONS`), and the constructor refuses any other: a source not on the list, and a
model's words by name (:data:`MODEL_TEXT`), so nothing an agent writes makes a wait. A chat's
(``chat_phase``) is the first; a helper's and a goal cycle's take the same record.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any

#: Each reason a piece of work waits, with the registries it is known from and the ways it resumes:
#: an approval (``owner_answer``), a question (``input``), a sign-in or more access
#: (``permission``), the helpers or background commands it started (``children``), and a throttled
#: provider (``dependency``).
REASONS: Mapping[str, tuple[frozenset[str], frozenset[str]]] = MappingProxyType(
    {
        "owner_answer": (frozenset({"approval_registry"}), frozenset({"answered"})),
        "input": (frozenset({"question_registry"}), frozenset({"answered"})),
        "permission": (frozenset({"sign_in_ask"}), frozenset({"answered"})),
        "children": (frozenset({"subagent_registry", "jobs"}), frozenset({"children_done"})),
        "dependency": (frozenset({"dependency_waits"}), frozenset({"at_time", "signal"})),
    }
)
#: Every registry a wait may be known from.
EVIDENCE = frozenset().union(*(sources for sources, _ in REASONS.values()))
#: Every way a wait may end.
RESUMES = frozenset().union(*(resumes for _, resumes in REASONS.values()))
#: The source no wait is ever made from: what a model wrote.
MODEL_TEXT = "model_text"
#: The ways a wait ends on something it names, which its ``ref`` must then say.
_NAMED = frozenset({"answered", "children_done"})


@dataclass(frozen=True)
class Resume:
    """What ends a wait: an ask ``answered``, the work's ``children_done``, a time reached
    (``at_time``) or a ``signal``; *ref* names it (a waiting list item's id for an ask)."""

    kind: str
    ref: str


@dataclass(frozen=True)
class Wait:
    """Why a piece of work waits, what resumes it, by when, and the registry that says so."""

    reason: str
    resume_condition: Resume
    deadline_at: str | None
    evidence_source: str

    def __post_init__(self) -> None:
        if self.evidence_source == MODEL_TEXT:
            raise ValueError("a model's words never make a wait: it is known from a registry")
        if self.reason not in REASONS:
            raise ValueError(f"no wait has the reason {self.reason!r}: one of {sorted(REASONS)}")
        sources, resumes = REASONS[self.reason]
        if self.evidence_source not in sources:
            known = "nowhere a wait is known from" if self.evidence_source not in EVIDENCE else ""
            raise ValueError(
                f"a wait for {self.reason} is known from {sorted(sources)}, not "
                f"{self.evidence_source!r}{f' ({known})' if known else ''}"
            )
        kind = self.resume_condition.kind
        if kind not in resumes:
            raise ValueError(f"a wait for {self.reason} ends {sorted(resumes)}, not {kind!r}")
        if kind in _NAMED and not self.resume_condition.ref:
            raise ValueError(f"a wait that ends {kind} names what it waits on")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
